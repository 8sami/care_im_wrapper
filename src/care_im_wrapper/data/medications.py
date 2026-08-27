"""Fetch medication requests for the authenticated actor."""

from dataclasses import replace
from datetime import datetime
from typing import Any

from django.utils import timezone

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import (
    ENTERED_IN_ERROR_STATUS,
    INACTIVE_MEDICATION_STATUSES,
    cached_fetch,
    humanize_choice,
    humanize_date,
    humanize_datetime,
)
from care_im_wrapper.data.common import ALL_PRESCRIPTIONS, resolve_target_encounter
from care_im_wrapper.data.pagination import Page, map_page, paginate_or_raise
from care_im_wrapper.data.records import DosageLine, MedicationRecord, PrescriptionChoiceRecord, PrescriptionRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

# Mirrors care_fe: Medicine/utils.ts and types/emr/medicationRequest. Exact-match tables --
# care_fe labels only these patterns and shows anything else verbatim.
_MAN_FREQUENCY_PRESETS: tuple[tuple[str, str], ...] = (
    ("1-0-1", "Twice a day"),
    ("1-1-1", "Thrice a day"),
    ("1-0-0", "Morning only"),
    ("0-0-1", "Night only"),
    ("0-1-0", "Noon only"),
    ("1-1-0", "Morning & Noon"),
    ("0-1-1", "Noon & Night"),
    ("1-1-1-1", "Four times a day"),
)
_MAN_LABELS: dict[str, str] = dict(_MAN_FREQUENCY_PRESETS)

_TIMING_CODE_TO_MAN: dict[str, str] = {
    "BID": "1-0-1",
    "TID": "1-1-1",
    "QID": "1-1-1-1",
    "AM": "1-0-0",
    "PM": "0-0-1",
    "NOON": "0-1-0",
}

# Timing codes with no M-A-N equivalent fall back to the option's own display string.
_TIMING_CODE_DISPLAYS: dict[str, str] = {
    "QD": "QD (Once a day)",
    "QOD": "QOD (Alternate days)",
    "BED": "BED (0-0-1)",
    "WK": "WK (Weekly)",
    "MO": "MO (Monthly)",
    "HS": "HS (At bedtime)",
    "AC": "AC (Before meals)",
    "PC": "PC (After meals)",
    "STAT": "STAT (Immediately)",
}

_DURATION_UNIT_LABELS: dict[str, tuple[str, str]] = {
    "d": ("day", "days"),
    "h": ("hour", "hours"),
    "wk": ("week", "weeks"),
    "mo": ("month", "months"),
    "a": ("year", "years"),
}


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_prescriptions(actor: Actor, session: ConversationSession) -> Page:
    """One page of the open encounter's medications, newest first, grouped as care_fe groups them.

    Narrowed further to one prescription when the reader picked one; the sentinel (or an
    unset scope) means all of them.
    """
    from care.emr.models.medication_request import MedicationRequest  # type: ignore[import-untyped]

    encounter = resolve_target_encounter(actor, session)
    queryset = (
        MedicationRequest.objects.filter(patient=encounter.patient, encounter=encounter)
        .exclude(status__in=INACTIVE_MEDICATION_STATUSES)
        # Also gone if the prescription above it was entered in error.
        .exclude(prescription__status=ENTERED_IN_ERROR_STATUS)
        .select_related(
            "requested_product",
            "requester",
            "prescription",
            "prescription__prescribed_by",
            "prescription__encounter__facility",
        )
    )

    selected = getattr(session, "active_prescription_external_id", "") or ""
    if selected and selected != ALL_PRESCRIPTIONS:
        queryset = queryset.filter(prescription__external_id=selected)

    page = paginate_or_raise(queryset.order_by("-created_date", "-id"), session)

    groups, weights = group_medications(page.records, page.next_record)
    return map_page_to_groups(page, groups, weights)


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_prescription_choices(actor: Actor, session: ConversationSession) -> Page:
    """One page of the open encounter's prescriptions, as care_fe's sidebar lists them."""
    from care.emr.models.medication_request import MedicationRequestPrescription  # type: ignore[import-untyped]

    encounter = resolve_target_encounter(actor, session)
    queryset = (
        MedicationRequestPrescription.objects.filter(patient=encounter.patient, encounter=encounter)
        .exclude(status=ENTERED_IN_ERROR_STATUS)
        .select_related("prescribed_by")
    )
    page = paginate_or_raise(queryset.order_by("-created_date", "-id"), session)

    def build(prescription) -> PrescriptionChoiceRecord:
        # care_fe's card: formatDateTime(created_date) over "Prescribed by: <name>".
        return PrescriptionChoiceRecord(
            prescribed_on=humanize_datetime(getattr(prescription, "created_date", None)),
            prescribed_by=format_user_name(getattr(prescription, "prescribed_by", None)),
            name=getattr(prescription, "name", None) or None,
            external_id=str(prescription.external_id),
        )

    return map_page(page, build)


def group_medications(medications: list[Any], next_record: Any = None) -> tuple[list[PrescriptionRecord], list[int]]:
    """Groups a page of medications as care_fe does, newest group first."""
    groups: dict[Any, list[Any]] = {}
    for medication in medications:
        groups.setdefault(_group_key(medication), []).append(medication)

    # Drop a group the window cut in half, or it renders partially here and again in full
    # on the next page.
    if next_record is not None and len(groups) > 1:
        keys = list(groups)
        if _group_key(next_record) == keys[-1]:
            del groups[keys[-1]]

    records: list[PrescriptionRecord] = []
    weights: list[int] = []
    for (kind, _identity), unordered in groups.items():
        # Groups come newest-first, but a prescription reads top-down.
        members = sorted(unordered, key=lambda m: (getattr(m, "created_date", None), getattr(m, "id", 0)))
        first = members[0]
        weights.append(len(members))
        prescription = getattr(first, "prescription", None) if kind == "prescription" else None
        if prescription is not None:
            encounter = getattr(prescription, "encounter", None)
            records.append(
                PrescriptionRecord(
                    name=getattr(prescription, "name", None) or None,
                    status=humanize_choice(getattr(prescription, "status", None)),
                    prescribed_by=format_user_name(getattr(prescription, "prescribed_by", None)),
                    prescribed_on=humanize_date(getattr(prescription, "created_date", None)),
                    facility=getattr(getattr(encounter, "facility", None), "name", None),
                    note=getattr(prescription, "note", None) or None,
                    medications=tuple(build_medication(medication) for medication in members),
                )
            )
        else:
            # No prescription: the group is its authored date, the prescriber its requester.
            records.append(
                PrescriptionRecord(
                    name=None,
                    status="",
                    prescribed_by=format_user_name(getattr(first, "requester", None)),
                    prescribed_on=humanize_date(getattr(first, "created_date", None)),
                    facility=None,
                    note=None,
                    medications=tuple(build_medication(medication) for medication in members),
                )
            )
    return records, weights


def map_page_to_groups(page: Page, records: list[PrescriptionRecord], weights: list[int]) -> Page:
    """Replaces a page of medications with its groups."""
    return replace(page, records=records, source_weights=tuple(weights))


def _group_key(medication: Any) -> tuple[str, Any]:
    """Group key; prefixed so a prescription id cannot collide with a date."""
    prescription_id = getattr(medication, "prescription_id", None)
    if prescription_id is not None:
        return ("prescription", prescription_id)
    authored = getattr(medication, "created_date", None)
    return ("date", timezone.localtime(authored).date() if authored else None)


def format_user_name(user: Any) -> str | None:
    """care_fe formatName: prefix, first, last, suffix, else username."""
    if user is None:
        return None
    parts = [
        getattr(user, "prefix", None),
        getattr(user, "first_name", None),
        getattr(user, "last_name", None),
        getattr(user, "suffix", None),
    ]
    name = " ".join(part.strip() for part in parts if part and part.strip())
    return name or (getattr(user, "username", None) or None)


def build_medication(med: Any) -> MedicationRecord:
    """One MedicationRequest and its dosage lines."""
    return MedicationRecord(
        name=display_medication_name(med),
        status=humanize_choice(getattr(med, "status", None)),
        lines=build_dosage_lines(med),
        note=getattr(med, "note", None) or None,
    )


def build_dosage_lines(med: Any) -> tuple[DosageLine, ...]:
    """One DosageLine per dosage_instruction; joining them loses which dose lasts how long."""
    instructions = getattr(med, "dosage_instruction", None)
    if isinstance(instructions, str):
        # Legacy shape: prose, not a list of dicts. Surface it as a free-text sig.
        return (
            DosageLine(
                dosage="",
                frequency="",
                additional_instructions=(),
                duration="",
                sig=instructions,
                is_non_unit_dose=False,
            ),
        )
    if not isinstance(instructions, list):
        return ()

    lines = []
    for inst in instructions:
        if not isinstance(inst, dict):
            continue
        lines.append(
            DosageLine(
                dosage=_format_dosage(inst) or "",
                frequency=_format_frequency(inst) or "",
                additional_instructions=_additional_instructions(inst),
                duration=_format_duration(inst) or "",
                sig=_format_sig(inst) or "",
                is_non_unit_dose=_is_non_unit_dose(inst),
            )
        )
    return tuple(lines)


def display_medication_name(med: Any) -> str:
    """care_fe displayMedicationName: medication.display, else requested_product.name."""
    medication = getattr(med, "medication", None)
    if isinstance(medication, dict):
        display = medication.get("display")
        if display:
            return str(display)

    requested_product = getattr(med, "requested_product", None)
    name = getattr(requested_product, "name", None) if requested_product else None
    if name:
        return str(name)

    # Beyond care_fe's two sources: `text` also carries a label.
    if isinstance(medication, dict) and medication.get("text"):
        return str(medication["text"])

    return "Unknown medication"


def _coding_display(coding) -> str | None:
    if not isinstance(coding, dict):
        return None
    return coding.get("display") or coding.get("code")


def _trim_number(value: Any) -> str:
    """Dose without trailing zeros. Not care_fe's `round()`, which is a monetary."""
    text = str(value).strip()
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_frequency(inst: dict) -> str | None:
    """care_fe formatFrequency: PRN first, then free text, then the FHIR timing code."""
    if inst.get("as_needed_boolean"):
        # care_fe labels PRN as "SOS", not "As needed".
        reason = _coding_display(inst.get("as_needed_for"))
        return f"SOS ({reason})" if reason else "SOS"

    text = inst.get("text")
    if text:
        text = str(text).strip()
        label = _MAN_LABELS.get(text)
        return f"{text} ({label})" if label else text

    code = ((inst.get("timing") or {}).get("code") or {}).get("code")
    if code:
        code = str(code).upper()
        man = _TIMING_CODE_TO_MAN.get(code)
        if man:
            return f"{man} ({_MAN_LABELS[man]})"
        return _TIMING_CODE_DISPLAYS.get(code, code)

    return None


def _format_quantity(quantity: Any) -> str | None:
    """A {value, unit} pair as "500 mg"."""
    if not isinstance(quantity, dict) or quantity.get("value") is None:
        return None
    unit = _coding_display(quantity.get("unit"))
    value = _trim_number(quantity["value"])
    return f"{value} {unit}" if unit else value


def _format_dosage(inst: dict) -> str | None:
    """care_fe formatDosage: a dose_range renders both ends, else the dose_quantity."""
    dose_and_rate = inst.get("dose_and_rate") or {}
    if not isinstance(dose_and_rate, dict):
        return None

    dose_range = dose_and_rate.get("dose_range")
    if isinstance(dose_range, dict):
        low = _format_quantity(dose_range.get("low"))
        high = _format_quantity(dose_range.get("high"))
        if low and high:
            return f"{low} -> {high}"
        if low or high:
            return low or high

    return _format_quantity(dose_and_rate.get("dose_quantity"))


def _is_non_unit_dose(inst: dict) -> bool:
    """care_fe isNonUnitDose: a dose range, or a quantity that is not exactly one."""
    dose_and_rate = inst.get("dose_and_rate") or {}
    if not isinstance(dose_and_rate, dict):
        return False
    if isinstance(dose_and_rate.get("dose_range"), dict):
        return True

    dose_quantity = dose_and_rate.get("dose_quantity")
    if not isinstance(dose_quantity, dict) or dose_quantity.get("value") is None:
        return False
    try:
        return float(str(dose_quantity["value"])) != 1
    except ValueError:
        return False


def _format_sig(inst: dict) -> str | None:
    """care_fe formatSig: route, method and site as "Via X by Y to Z"."""
    parts = []
    route = _coding_display(inst.get("route"))
    if route:
        parts.append(f"Via {route}")
    method = _coding_display(inst.get("method"))
    if method:
        parts.append(f"by {method}")
    site = _coding_display(inst.get("site"))
    if site:
        parts.append(f"to {site}")
    return " ".join(parts) if parts else None


def _additional_instructions(inst: dict) -> tuple[str, ...]:
    """patient_instruction plus each additional_instruction display."""
    values = []
    patient_instruction = inst.get("patient_instruction")
    if patient_instruction:
        values.append(str(patient_instruction))
    for coding in inst.get("additional_instruction") or []:
        display = _coding_display(coding)
        if display:
            values.append(display)
    return tuple(values)


def _bound_date(value: Any) -> str | None:
    """A period bound as a readable date; ISO strings pass through humanize_date untouched."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return humanize_date(parsed.date())
    return humanize_date(value)


def _format_duration_label(duration: Any) -> str | None:
    """care_fe formatDurationLabel: "5 days", not "5 d". "0" counts as no duration."""
    if not isinstance(duration, dict):
        # A JSONField enforces no schema, so tolerate raw prose.
        return str(duration) if duration else None

    value = duration.get("value")
    if value is None or str(value).strip() in ("", "0"):
        return None

    unit = duration.get("unit")
    labels = _DURATION_UNIT_LABELS.get(str(unit))
    if not labels:
        return f"{value} {unit}".strip() if unit else str(value)

    singular, plural = labels
    try:
        is_one = float(str(value)) == 1
    except ValueError:
        is_one = False
    return f"{value} {singular if is_one else plural}"


def _format_duration(inst: dict) -> str | None:
    """Mirrors care_fe getTimingBounds + formatTimingBounds."""
    repeat = (inst.get("timing") or {}).get("repeat") or {}
    if not isinstance(repeat, dict):
        return None

    bounds_range = repeat.get("bounds_range")
    if isinstance(bounds_range, dict):
        low = (bounds_range.get("low") or {}).get("value")
        high = bounds_range.get("high") or {}
        high_value, high_unit = high.get("value"), high.get("unit")
        if low is not None and high_value is not None:
            labels = _DURATION_UNIT_LABELS.get(str(high_unit))
            if labels:
                singular, plural = labels
                unit_text = singular if str(high_value).strip() == "1" else plural
            else:
                unit_text = str(high_unit or "")
            return f"{low}–{high_value} {unit_text}".strip()

    bounds_period = repeat.get("bounds_period")
    if isinstance(bounds_period, dict):
        start, end = _bound_date(bounds_period.get("start")), _bound_date(bounds_period.get("end"))
        if start and end:
            return f"{start} → {end}"

    return _format_duration_label(repeat.get("bounds_duration"))
