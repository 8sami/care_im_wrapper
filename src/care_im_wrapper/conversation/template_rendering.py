from __future__ import annotations


def merge_variable_values(
    event_variable_values: dict | None,
    recipient_variable_overrides: dict | None,
) -> dict:
    return {**(event_variable_values or {}), **(recipient_variable_overrides or {})}
