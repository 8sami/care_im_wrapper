from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from care_im_wrapper.webhooks.exceptions import (
    PayloadParseError,
    SignatureVerificationError,
)


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs) -> HttpResponse:
        return self.handle_challenge(request)

    def post(self, request, *args, **kwargs) -> HttpResponse:
        try:
            # Verify the signature against the raw body before parsing, so an unauthenticated
            # caller can't reach JSON parsing (or anything downstream of it) at all.
            if not self.verify_signature(request):
                raise SignatureVerificationError("Invalid signature")
            payload = self._parse_payload(request)
            return self.handle_event(request, payload)
        except SignatureVerificationError:
            return HttpResponse(status=401)
        except PayloadParseError:
            return HttpResponse(status=400)
        except Exception:
            logging.getLogger(__name__).exception("Unhandled error in %s", self.__class__.__name__)
            return HttpResponse(status=500)

    def verify_signature(self, request) -> bool:
        """No signature scheme by default; providers that need one mix in HmacVerificationMixin."""
        return True

    def _parse_payload(self, request) -> dict[str, Any]:
        try:
            return json.loads(request.body)
        except json.JSONDecodeError:
            raise PayloadParseError("Invalid JSON") from None

    def handle_event(self, request, payload: dict[str, Any]) -> HttpResponse:
        raise NotImplementedError

    def handle_challenge(self, request: HttpRequest) -> HttpResponse:
        raise NotImplementedError
