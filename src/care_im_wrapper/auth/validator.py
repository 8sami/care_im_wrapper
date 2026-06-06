from __future__ import annotations

from care_im_wrapper.auth.resolver import ResolvedIdentity


def validate_year_of_birth(user_input: str, identity: ResolvedIdentity) -> bool:
    stripped = user_input.strip()
    if not stripped.isdigit() or len(stripped) != 4:
        return False
    return int(stripped) == identity.year_of_birth
