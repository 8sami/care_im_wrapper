def mask_phone_number(phone: str) -> str:
    """Returns e.g. +9232****475 — keeps country code prefix and last 3 digits."""
    if not phone:
        return ""
    # Remove any non-digit characters except the leading +
    sanitized = "".join(c for c in phone if c.isdigit() or c == "+")

    if len(sanitized) < 5:
        return sanitized

    # We want to keep a prefix and a suffix.
    # For international numbers, the length varies.
    # Let's assume we keep the first 4 (including + if present) and last 3.
    prefix_len = 4
    suffix_len = 3

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
