from __future__ import annotations

from datetime import date, datetime
from functools import wraps
from typing import Any

from django.core.cache import cache as django_cache
from django.utils import timezone

ENTERED_IN_ERROR_STATUS = "entered_in_error"


def humanize_choice(value: str | None) -> str:
    """
    Converts a Django TextChoices-style value like 'in_progress' or
    'A_positive' into 'In Progress' / 'A Positive'.
    """
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


def humanize_date(value: datetime | date | str | None) -> str:
    """
    Converts a raw date/datetime into a short human-readable date.
    datetimes are localized first; plain dates have no timezone to convert.
    Never shows microseconds or UTC offset to the end user.
    """
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
    """
    Converts a raw datetime into a short human-readable time.
    Never shows microseconds or UTC offset to the end user.
    """
    if not value:
        return "Not recorded"
    if isinstance(value, str):
        return value
    try:
        local_dt = timezone.localtime(value)
        return local_dt.strftime("%I:%M %p").lower()
    except (AttributeError, ValueError):
        return str(value)


def cached_fetch(timeout_seconds: int):
    def decorator(fetch_fn):
        @wraps(fetch_fn)
        def wrapper(actor: Any, session: Any) -> Any:
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
    """
    Key must be unique per (function, actor type, actor id, active patient).
    A collision between two patients' data is a privacy bug.
    """
    patient_ctx = session.active_patient_external_id or "self"
    return f"care_im:fetch:{fn_name}:{actor.user_type}:{actor.instance.id}:{patient_ctx}"
