from __future__ import annotations

import logging
from typing import Any

import httpx

from care_im_wrapper.conversation.messages import OutboundMessage
from care_im_wrapper.messaging.exceptions import WhatsAppPairRateLimitError
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


class WhatsAppClient:
    supports_interactive: bool = True
    max_message_chars: int = 4096

    def send_text(self, to: str, body: str) -> None:
        self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": body},
            }
        )

    def send_interactive(self, to: str, msg: OutboundMessage) -> None:
        """
        Renders OutboundMessage.interactive as the exact Meta Cloud API JSON shape and sends it.
        Silently falls back to send_text() if msg.interactive is None.
        All field truncation happens here — callers must not pre-truncate.
        """
        from care_im_wrapper.conversation.messages import InteractiveType

        if msg.interactive is None:
            self.send_text(to, msg.as_plain_text())
            return

        iv = msg.interactive
        interactive_obj: dict[str, Any]

        if iv.type == InteractiveType.REPLY_BUTTONS:
            buttons = [
                {
                    "type": "reply",
                    "reply": {
                        "id": str(b["id"])[:256],
                        "title": str(b["title"])[:20],
                    },
                }
                for b in iv.action_data[:3]  # hard cap: max 3 buttons
            ]
            interactive_obj = {
                "type": "button",
                "body": {"text": iv.body},
                "action": {"buttons": buttons},
            }

        elif iv.type == InteractiveType.LIST:
            sections = []
            total_rows = 0
            for section in iv.action_data:
                rows = []
                for row in section.get("rows", []):
                    if total_rows >= 10:  # hard cap: max 10 rows across all sections
                        break
                    entry: dict[str, Any] = {
                        "id": str(row["id"])[:256],
                        "title": str(row["title"])[:24],
                    }
                    if row.get("description"):
                        entry["description"] = str(row["description"])[:72]
                    rows.append(entry)
                    total_rows += 1
                if rows:
                    sections.append(
                        {
                            "title": str(section.get("title", ""))[:24],
                            "rows": rows,
                        }
                    )
            interactive_obj = {
                "type": "list",
                "body": {"text": iv.body},
                "action": {
                    "button": str(iv.button_label)[:20],
                    "sections": sections,
                },
            }

        elif iv.type == InteractiveType.CTA_URL:
            params = iv.action_data[0] if iv.action_data else {}
            interactive_obj = {
                "type": "cta_url",
                "body": {"text": iv.body},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": str(params.get("display_text", "Open"))[:20],
                        "url": str(params.get("url", "")),
                    },
                },
            }

        else:
            self.send_text(to, msg.as_plain_text())
            return

        if iv.header:
            interactive_obj["header"] = {"type": "text", "text": iv.header}
        if iv.footer:
            interactive_obj["footer"] = {"text": iv.footer}

        self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": interactive_obj,
            }
        )

    def _send(self, payload: dict[str, Any]) -> None:
        token = plugin_settings.WHATSAPP_ACCESS_TOKEN
        phone_id = plugin_settings.WHATSAPP_PHONE_NUMBER_ID
        api_url = plugin_settings.WHATSAPP_API_URL

        if not token or not phone_id:
            raise RuntimeError(
                "WhatsApp credentials (WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID) are not configured"
            )

        url = f"{api_url}/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                try:
                    error_data = exc.response.json()
                    if error_data.get("error", {}).get("code") == 131056:
                        raise WhatsAppPairRateLimitError("WhatsApp pair rate limit hit (131056)") from exc
                except (ValueError, KeyError):
                    pass
            logger.error("WhatsApp API %s: %s", exc.response.status_code, exc.response.text)
        except httpx.RequestError as exc:
            logger.error("WhatsApp network error: %s", exc)
