from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Any

from django.core.cache import cache as django_cache
from django.utils import timezone

from care_im_wrapper.settings import plugin_settings

_WHATSAPP_MESSAGE_LIMIT = 4096


def truncate(text: str) -> str:
    if len(text) <= int(plugin_settings.WHATSAPP_MESSAGE_CHAR_LIMIT):
        return text
    return (
        text[: int(plugin_settings.WHATSAPP_MESSAGE_CHAR_LIMIT) - int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)]
        + "\n... (truncated)"
    )


def humanize_choice(value: str | None) -> str:
    """
    Converts a Django TextChoices-style value like 'in_progress' or
    'A_positive' into 'In Progress' / 'A Positive'.
    """
    if not value:
        return "Not recorded"
    return value.replace("_", " ").title()


def humanize_date(value: datetime | str | None) -> str:
    """
    Converts a raw datetime into a short human-readable date.
    Never shows microseconds or UTC offset to the end user.
    """
    if not value:
        return "Not recorded"
    if isinstance(value, str):
        return value
    try:
        local_dt = timezone.localtime(value)
        return local_dt.strftime("%d %b %Y")
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
