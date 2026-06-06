import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self):
        self.access_token = getattr(settings, "CARE_IM_WRAPPER_META_ACCESS_TOKEN", "")
        self.phone_number_id = getattr(settings, "CARE_IM_WRAPPER_META_PHONE_NUMBER_ID", "")
        if not self.access_token or not self.phone_number_id:
            logger.error("WhatsAppClient: Missing Meta configuration (token or phone ID)")

    def send_text(self, to: str, body: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        self._send(payload)

    def send_interactive_menu(self, to: str, header: str, options: list[str]) -> None:
        # Using numbered plain-text list for maximum compatibility and simplicity in Week 2
        body = f"{header}\n\n" + "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(options)])
        self.send_text(to, body)

    def _send(self, payload: dict) -> None:
        url = f"https://graph.facebook.com/v20.0/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(url, json=payload, headers=headers)
        except httpx.HTTPStatusError as exc:
            logger.error("WhatsApp API error: %s", exc)
        except httpx.RequestError as exc:
            logger.error("WhatsApp network error: %s", exc)
        except Exception as exc:
            logger.error("Unexpected error sending WhatsApp message: %s", exc)


whatsapp = WhatsAppClient()
