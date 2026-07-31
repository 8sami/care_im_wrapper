from __future__ import annotations

from datetime import date, datetime
from functools import wraps
from typing import Any

from django.core.cache import cache as django_cache
from django.utils import timezone

ENTERED_IN_ERROR_STATUS = "entered_in_error"

ACTIVE_MEDICATION_STATUSES = ("active", "on_hold", "draft", "unknown")
INACTIVE_MEDICATION_STATUSES = ("ended", "completed", "cancelled", "entered_in_error")


def humanize_choice(value: str | None) -> str:
    """Converts a Django TextChoices-style value like 'in_progress' or."""
    if not value:
        return "Not recorded"
    return value.replace("_", " ").title()


def humanize_encounter_class(value: str | None) -> str:
    """
    Converts FHIR short codes into human-readable strings.
    """
    mapping = {
        "imp": "Inpatient",
        "amb": "Ambulatory",
        "obsenc": "Observation",
        "emer": "Emergency",
        "vr": "Virtual",
        "hh": "Home Health",
    }
    if not value:
        return "Not recorded"
    return mapping.get(value, value.title())


def describe_resource(resource: Any, default: str = "Unknown") -> str:
    """Names a SchedulableResource for display, e.g. "Ada Lovelace", "Cardiology Location",."""
    if resource is None:
        return default

    resource_type = getattr(resource, "resource_type", None)

    if resource_type == "location":
        name = getattr(getattr(resource, "location", None), "name", None)
        return f"{name} Location" if name else "Location"

    if resource_type == "healthcare_service":
        name = getattr(getattr(resource, "healthcare_service", None), "name", None)
        return f"{name} HealthcareService" if name else "HealthcareService"

    user = getattr(resource, "user", None)
    if user is None:
        return default
    first_name = getattr(user, "first_name", "") or ""
    last_name = getattr(user, "last_name", "") or ""
    return f"{first_name} {last_name}".strip() or default


def humanize_date(value: datetime | date | str | None) -> str:
    """Converts a raw date/datetime into a short human-readable date."""
    if not value:
        return "Not recorded"
    if isinstance(value, str):
        return value
    try:
        if isinstance(value, datetime):
            value = timezone.localtime(value)
        return value.strftime("%d %b %Y")
    except (AttributeError, ValueError):
        return str(value)


def humanize_time(value: datetime | str | None) -> str:
    """Converts a raw datetime into a short human-readable time."""
    if not value:
        return "Not recorded"
    if isinstance(value, str):
        return value
    try:
        local_dt = timezone.localtime(value)
        return local_dt.strftime("%I:%M %p").lower()
    except (AttributeError, ValueError):
        return str(value)


_CACHE_SCHEMA_VERSION = 3


def cached_fetch(timeout_seconds: int):
    """Caches a patient-data fetcher's result per (function, actor, target patient)."""

    def decorator(fetch_fn):
        @wraps(fetch_fn)
        def wrapper(actor: Any, session: Any) -> Any:
            from care_im_wrapper.data.common import resolve_target_patient

            resolve_target_patient(actor, session)  # authorize before the cache is consulted
            key = _build_cache_key(fetch_fn.__name__, actor, session)
            hit = django_cache.get(key)
            if hit is not None:
                return hit
            result = fetch_fn(actor, session)
            django_cache.set(key, result, timeout_seconds)
            return result

        return wrapper

    return decorator


def _build_cache_key(fn_name: str, actor: Any, session: Any) -> str:
    """Key must be unique per (function, actor type, actor id, active patient, record offset)."""
    from care_im_wrapper.data.pagination import current_offset

    patient_ctx = session.active_patient_external_id or "self"
    offset = current_offset(session)
    return (
        f"care_im:fetch:v{_CACHE_SCHEMA_VERSION}:{fn_name}:"
        f"{actor.user_type}:{actor.instance.id}:{patient_ctx}:o{offset}"
    )
