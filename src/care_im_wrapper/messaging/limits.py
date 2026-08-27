"""Per-channel field limits, and the truncation that enforces them.

Two surfaces, two sets of limits, and they are not interchangeable:

* **Free-form / interactive** messages -- what the chat sends. The body is large
  (`text_body`), but an interactive message's own body is far smaller, and each row,
  title and description has its own cap.
* **Template ("notification") messages** -- what the notification pipeline sends. The
  body is fixed at approval time; what we supply is *parameters*, and each parameter has
  its own limit, unrelated to the free-form body limit. Sending an over-long parameter
  fails the whole message, exactly as a blank one does.

A provider describes itself here; `registry.get_channel_limits` is how callers ask for a
channel's set, so nothing outside this package has to know which provider it is talking to.

Enforcement is at the send boundary (`messaging/whatsapp.py`), not at the call sites.
A caller that forgets to truncate is a bug that reaches the provider as a 400; a caller
that *cannot* exceed a limit is not. Call sites may still truncate for layout reasons --
the boundary clamp is the backstop, and is idempotent, so doing both is harmless.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from care_im_wrapper.settings import plugin_settings

# Truncation marker. One character, so it costs almost nothing of the budget it has to
# fit inside.
ELLIPSIS = "…"

_ZERO_WIDTH_JOINER = "‍"
_VARIATION_SELECTORS = frozenset("︎️")
_KEYCAP = "⃣"
_SKIN_TONE_RANGE = range(0x1F3FB, 0x1F400)
_REGIONAL_INDICATOR_RANGE = range(0x1F1E6, 0x1F200)
_MARK_CATEGORIES = frozenset({"Mn", "Me", "Mc"})


@dataclass(frozen=True)
class ChannelLimits:
    """Every length cap a channel imposes, in characters.

    Kept as data rather than scattered constants so the send boundary can enforce all of
    them uniformly and a new provider only has to describe itself.
    """

    # Free-form and interactive
    text_body: int
    interactive_body: int
    interactive_header: int
    interactive_footer: int
    section_title: int
    row_title: int
    row_description: int
    button_title: int
    list_button_label: int
    max_rows: int
    # Not a cap the provider rejects on, but the point its client folds a message behind a
    # "Read more". A page past it is delivered and unread, which is the same as lost.
    preview_lines: int
    # Template / notification
    template_parameter: int


def _splits_grapheme(text: str, index: int) -> bool:
    """Whether cutting immediately before ``text[index]`` would break a character apart.

    Python strings index by code point, so there are no surrogate halves to worry about,
    but a single user-perceived character is often several code points -- "é" as e +
    combining acute, a flag as two regional indicators, "👩‍⚕️" as woman + ZWJ + staff of
    aesculapius + variation selector. Cutting inside one leaves visible mojibake in the
    message.

    This is a deliberate approximation of UAX #29 grapheme clustering, covering the
    sequences that actually occur in names, clinical text and emoji. Full segmentation
    would mean taking on a dependency (`regex`'s ``\\X``) for a backstop that only runs
    when something is already over budget.
    """
    if index <= 0 or index >= len(text):
        return False

    previous, current = text[index - 1], text[index]
    code_point = ord(current)

    if current in _VARIATION_SELECTORS or current == _KEYCAP:
        return True
    if current == _ZERO_WIDTH_JOINER or previous == _ZERO_WIDTH_JOINER:
        return True
    if code_point in _SKIN_TONE_RANGE:
        return True
    if unicodedata.combining(current) or unicodedata.category(current) in _MARK_CATEGORIES:
        return True
    # A flag is a *pair* of regional indicators; splitting the pair shows two letters.
    return code_point in _REGIONAL_INDICATOR_RANGE and ord(previous) in _REGIONAL_INDICATOR_RANGE


def clamp(value: object, limit: int, *, marker: str = ELLIPSIS) -> str:
    """``value`` as a string, guaranteed to be at most ``limit`` characters.

    Truncation lands on a character boundary and flags itself with an ellipsis, so a
    reader can tell the difference between "this is the whole value" and "there was more".
    Idempotent: clamping an already-clamped value is a no-op.
    """
    text = "" if value is None else str(value)
    if limit <= 0:
        return ""
    # Fast path -- the overwhelmingly common case does no scanning at all.
    if len(text) <= limit:
        return text

    suffix = marker if len(marker) < limit else ""
    cut = limit - len(suffix)
    while cut > 0 and _splits_grapheme(text, cut):
        cut -= 1
    return text[:cut].rstrip() + suffix


def whatsapp_limits() -> ChannelLimits:
    """Read at call time, not import time, so PLUGIN_CONFIGS overrides apply."""
    button_title = int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)
    return ChannelLimits(
        text_body=int(plugin_settings.WHATSAPP_MESSAGE_CHAR_LIMIT),
        interactive_body=int(plugin_settings.WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT),
        interactive_header=int(plugin_settings.WHATSAPP_HEADER_CHAR_LIMIT),
        interactive_footer=int(plugin_settings.WHATSAPP_FOOTER_CHAR_LIMIT),
        section_title=int(plugin_settings.WHATSAPP_SECTION_TITLE_CHAR_LIMIT),
        row_title=int(plugin_settings.WHATSAPP_ROW_TITLE_CHAR_LIMIT),
        row_description=int(plugin_settings.WHATSAPP_DESCRIPTION_TRUNCATE),
        button_title=button_title,
        list_button_label=button_title,
        max_rows=int(plugin_settings.WHATSAPP_LIST_ROW_LIMIT),
        preview_lines=int(plugin_settings.WHATSAPP_PREVIEW_LINE_LIMIT),
        template_parameter=int(plugin_settings.WHATSAPP_TEMPLATE_PARAMETER_CHAR_LIMIT),
    )


def default_limits() -> ChannelLimits:
    """Fallback for a channel that has not described itself. Reads only DEFAULT_* settings:
    borrowing another provider's numbers would silently hand an unknown channel Meta's
    limits, which is exactly the coupling ChannelLimits exists to prevent."""
    body = int(plugin_settings.DEFAULT_MAX_MESSAGE_CHARS)
    button_title = int(plugin_settings.DEFAULT_BUTTON_TITLE_CHAR_LIMIT)
    return ChannelLimits(
        text_body=body,
        interactive_body=min(body, int(plugin_settings.DEFAULT_INTERACTIVE_BODY_CHAR_LIMIT)),
        interactive_header=int(plugin_settings.DEFAULT_INTERACTIVE_HEADER_CHAR_LIMIT),
        interactive_footer=int(plugin_settings.DEFAULT_INTERACTIVE_FOOTER_CHAR_LIMIT),
        section_title=int(plugin_settings.DEFAULT_SECTION_TITLE_CHAR_LIMIT),
        row_title=int(plugin_settings.DEFAULT_ROW_TITLE_CHAR_LIMIT),
        row_description=int(plugin_settings.DEFAULT_ROW_DESCRIPTION_CHAR_LIMIT),
        button_title=button_title,
        list_button_label=button_title,
        max_rows=int(plugin_settings.DEFAULT_MAX_INTERACTIVE_ROWS),
        preview_lines=int(plugin_settings.DEFAULT_PREVIEW_LINE_LIMIT),
        template_parameter=int(plugin_settings.DEFAULT_TEMPLATE_PARAMETER_CHAR_LIMIT),
    )
