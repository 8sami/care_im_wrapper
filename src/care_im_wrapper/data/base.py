"""Shared formatting helpers for WhatsApp message construction."""

from datetime import datetime

_WHATSAPP_MESSAGE_LIMIT = 4096


def truncate(text: str) -> str:
    if len(text) <= _WHATSAPP_MESSAGE_LIMIT:
        return text
    return text[: _WHATSAPP_MESSAGE_LIMIT - 20] + "\n... (truncated)"


def numbered_list(header: str, items: list[str]) -> str:
    lines = [header, ""]
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item}")
    return truncate("\n".join(lines))


def field(label: str, value: str | None) -> str:
    return f"{label}: {value or 'Not recorded'}"


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
        return value.strftime("%d %b %Y")
    except (AttributeError, ValueError):
        return str(value)
