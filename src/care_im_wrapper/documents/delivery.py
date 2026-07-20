"""Delivery helper for a document link."""

from __future__ import annotations

from care_im_wrapper.conversation.messages import InteractivePayload, InteractiveType, OutboundMessage
from care_im_wrapper.conversation.templates import _msg


def build_document_message(summary: str, link_url: str, footer: str | None = None) -> OutboundMessage:
    """One message carrying both a CTA_URL button and a plain-text fallback with the url
    inline. send_message picks whichever the provider supports."""
    return OutboundMessage(
        text=f"{summary}\n{link_url}",
        interactive=InteractivePayload(
            type=InteractiveType.CTA_URL,
            body=summary,
            action_data=[{"display_text": _msg("view_document"), "url": link_url}],
            footer=footer,
        ),
    )
