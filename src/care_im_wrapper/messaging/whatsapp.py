import logging

import httpx

from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


class WhatsAppClient:
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

    def send_interactive_menu(self, to: str, header: str, options: list[str]) -> None:
        body = f"{header}\n\n" + "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options))
        self.send_text(to, body)

    def _send(self, payload: dict) -> None:
        token = plugin_settings.WHATSAPP_ACCESS_TOKEN
        phone_id = plugin_settings.WHATSAPP_PHONE_NUMBER_ID
        api_url = plugin_settings.WHATSAPP_API_URL

        if not token or not phone_id:
            logger.error("WhatsApp credentials not configured")
            return

        url = f"{api_url}/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("WhatsApp API %s: %s", exc.response.status_code, exc.response.text)
        except httpx.RequestError as exc:
            logger.error("WhatsApp network error: %s", exc)


def send_text(to: str, body: str) -> None:
    WhatsAppClient().send_text(to, body)


def send_interactive_menu(to: str, header: str, options: list[str]) -> None:
    WhatsAppClient().send_interactive_menu(to, header, options)
