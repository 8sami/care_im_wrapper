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
