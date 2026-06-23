from care_im_wrapper.settings import plugin_settings


def normalize_phone_number(phone: str) -> str:
    """Removes any non-digit characters except the leading +."""
    if not phone:
        return ""
    return "".join(c for c in phone if c.isdigit() or c == "+")


def mask_phone_number(phone: str) -> str:
    """Returns a masked phone number, e.g. '+1234567890' -> '+1*******90'."""
    sanitized = normalize_phone_number(phone)

    if len(sanitized) < 5:
        return sanitized

    prefix_len = int(plugin_settings.PHONE_NUMBER_MASK_PREFIX_LEN)
    suffix_len = int(plugin_settings.PHONE_NUMBER_MASK_SUFFIX_LEN)

    if len(sanitized) <= prefix_len + suffix_len:
        return sanitized

    prefix = sanitized[:prefix_len]
    suffix = sanitized[-suffix_len:]
    mask = "*" * (len(sanitized) - prefix_len - suffix_len)

    return f"{prefix}{mask}{suffix}"


def mask_name(name: str) -> str:
    """Returns e.g. 'John Doe' -> 'J*** Doe' or 'John D.' depending on implementation.
    Requirement says: first name + last initial."""
    parts = name.strip().split()
    if not parts:
        return ""
    if len(parts) == 1:
        return f"{parts[0][0]}***"

    first_name = parts[0]
    last_initial = parts[-1][0]

    masked_first = f"{first_name[0]}{'*' * (len(first_name) - 1)}" if len(first_name) > 1 else first_name

    return f"{masked_first} {last_initial}."
