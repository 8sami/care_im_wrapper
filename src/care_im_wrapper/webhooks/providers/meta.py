from django.http import HttpResponse

from care_im_wrapper.signals import meta_message_received, meta_status_updated
from care_im_wrapper.webhooks.mixins import ChallengeMixin, HmacVerificationMixin
from care_im_wrapper.webhooks.views import WebhookView


class MetaWebhookView(ChallengeMixin, HmacVerificationMixin, WebhookView):
    verify_token_setting = "CARE_IM_WRAPPER_META_VERIFY_TOKEN"
    challenge_param = "hub.challenge"
    token_param = "hub.verify_token"
    mode_param = "hub.mode"
    required_mode = "subscribe"
    hmac_header = "X-Hub-Signature-256"
    hmac_algorithm = "sha256"
    secret_setting = "CARE_IM_WRAPPER_META_APP_SECRET"
    signature_prefix = "sha256="

    _CHANNEL_MAP = {
        "whatsapp_business_account": "whatsapp",
        "instagram": "instagram",
        "page": "messenger",
    }

    def handle_event(self, request, payload: dict) -> HttpResponse:
        if not self.verify_signature(request):
            from care_im_wrapper.webhooks.exceptions import SignatureVerificationError

            raise SignatureVerificationError("Invalid signature")

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                self._dispatch_change(change)

        return HttpResponse(status=200)

    def _dispatch_change(self, change: dict) -> None:
        field = change.get("field", "")
        channel = self._CHANNEL_MAP.get(field, field)
        value = change.get("value", {})

        for message in value.get("messages", []):
            meta_message_received.send(
                sender=self.__class__,
                payload=message,
                channel=channel,
            )

        for status in value.get("statuses", []):
            meta_status_updated.send(
                sender=self.__class__,
                payload=status,
                channel=channel,
            )
