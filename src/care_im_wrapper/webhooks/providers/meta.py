from __future__ import annotations

from typing import Any, ClassVar

from django.http import HttpRequest, HttpResponse

from care_im_wrapper.models import ConversationSession
from care_im_wrapper.signals import inbound_message_received, inbound_status_updated
from care_im_wrapper.webhooks.mixins import ChallengeMixin, HmacVerificationMixin
from care_im_wrapper.webhooks.views import WebhookView


class MetaWebhookView(ChallengeMixin, HmacVerificationMixin, WebhookView):
    verify_token_setting: ClassVar[str] = "WHATSAPP_WEBHOOK_VERIFY_TOKEN"
    challenge_param: ClassVar[str] = "hub.challenge"
    token_param: ClassVar[str] = "hub.verify_token"
    mode_param: ClassVar[str] = "hub.mode"
    required_mode: ClassVar[str] = "subscribe"
    hmac_header: ClassVar[str] = "X-Hub-Signature-256"
    hmac_algorithm: ClassVar[str] = "sha256"
    secret_setting: ClassVar[str] = "WHATSAPP_APP_SECRET"
    signature_prefix: ClassVar[str] = "sha256="

    _CHANNEL_MAP: ClassVar[dict[str, str]] = {
        "whatsapp_business_account": ConversationSession.Provider.WHATSAPP.value,
        # "instagram": "instagram",
        # "page": "messenger",
    }

    def handle_event(self, request: HttpRequest, payload: dict[str, Any]) -> HttpResponse:
        obj = str(payload.get("object", ""))
        channel = self._CHANNEL_MAP.get(obj, obj)

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                self._dispatch_change(change, channel)

        return HttpResponse(status=200)

    def _dispatch_change(self, change: dict[str, Any], channel: str) -> None:
        value = change.get("value", {})

        for message in value.get("messages", []):
            inbound_message_received.send(
                sender=self.__class__,
                payload=message,
                channel=channel,
            )

        for status in value.get("statuses", []):
            inbound_status_updated.send(
                sender=self.__class__,
                payload=status,
                channel=channel,
            )
