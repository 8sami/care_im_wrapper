import json
import logging

from django.http import HttpResponse
from django.middleware.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View

from care_im_wrapper.webhooks.exceptions import (
    PayloadParseError,
    SignatureVerificationError,
)


class WebhookView(View):
    @method_decorator(csrf_exempt, name="dispatch")
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs) -> HttpResponse:
        return self.handle_challenge(request)

    def post(self, request, *args, **kwargs) -> HttpResponse:
        payload = self._parse_payload(request)
        try:
            return self.handle_event(request, payload)
        except SignatureVerificationError:
            return HttpResponse(status=401)
        except PayloadParseError:
            return HttpResponse(status=400)
        except Exception:
            logging.getLogger(__name__).exception("Unexpected error in WebhookView")
            raise PayloadParseError("Unexpected error") from None

    def _parse_payload(self, request) -> dict:
        try:
            return json.loads(request.body)
        except json.JSONDecodeError:
            from care_im_wrapper.webhooks.exceptions import PayloadParseError

            raise PayloadParseError("Invalid JSON") from None

    def handle_event(self, request, payload: dict) -> HttpResponse:
        raise NotImplementedError
