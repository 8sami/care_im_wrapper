from __future__ import annotations

import hashlib
import hmac
import logging
from typing import ClassVar

from django.http import HttpRequest, HttpResponse

from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


class HmacVerificationMixin:
    hmac_header: ClassVar[str]
    hmac_algorithm: ClassVar[str] = "sha256"
    secret_setting: ClassVar[str]
    signature_prefix: ClassVar[str] = ""

    def verify_signature(self, request: HttpRequest) -> bool:
        secret = getattr(plugin_settings, self.secret_setting, "")
        if not secret:
            logger.error("Missing setting: %s", self.secret_setting)
            return False
        received = request.headers.get(self.hmac_header)  # pyright: ignore[reportAttributeAccessIssue]
        if not received:
            logger.warning("Missing header: %s", self.hmac_header)
            return False
        received = received.removeprefix(self.signature_prefix)
        digestmod = {
            "sha256": hashlib.sha256,
            "sha1": hashlib.sha1,
        }.get(self.hmac_algorithm, hashlib.sha256)
        expected = hmac.new(secret.encode(), request.body, digestmod).hexdigest()
        return hmac.compare_digest(expected, received)


class ChallengeMixin:
    verify_token_setting: ClassVar[str]
    challenge_param: ClassVar[str] = "challenge"
    token_param: ClassVar[str] = "verify_token"
    mode_param: ClassVar[str] = ""
    required_mode: ClassVar[str] = ""

    def handle_challenge(self, request: HttpRequest) -> HttpResponse:
        if self.mode_param and self.required_mode:
            if request.GET.get(self.mode_param) != self.required_mode:
                return HttpResponse(status=403)
        expected = getattr(plugin_settings, self.verify_token_setting, "")
        if not expected:
            logger.error("%s: %s not configured", self.__class__.__name__, self.verify_token_setting)
            return HttpResponse(status=500)
        received = request.GET.get(self.token_param, "")
        received = received if isinstance(received, str) else ""
        if not hmac.compare_digest(expected, received):
            return HttpResponse(status=403)
        challenge = request.GET.get(self.challenge_param, "")
        return HttpResponse(challenge if isinstance(challenge, str) else "")  # pyright: ignore[reportArgumentType]
