import hashlib
import hmac


class HmacVerificationMixin:
    hmac_header: str
    hmac_algorithm: str = "sha256"
    secret_setting: str
    signature_prefix: str = ""

    def verify_signature(self, request) -> bool:
        import logging

        from django.conf import settings

        logger = logging.getLogger(__name__)

        secret = getattr(settings, self.secret_setting, "")
        if not secret:
            logger.error("Missing setting: %s", self.secret_setting)
            return False

        received = request.headers.get(self.hmac_header)
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
    verify_token_setting: str
    challenge_param: str = "challenge"
    token_param: str = "verify_token"
    mode_param: str | None = None
    required_mode: str | None = None

    def handle_challenge(self, request) -> any:
        import hmac

        from django.conf import settings
        from django.http import HttpResponse

        if self.mode_param and self.required_mode:
            if request.GET.get(self.mode_param) != self.required_mode:
                return HttpResponse(status=403)

        expected = getattr(settings, self.verify_token_setting, "")
        if not expected:
            from django.core.exceptions import ImproperlyConfigured
            from django.http import HttpResponse

            raise ImproperlyConfigured("Missing setting: %s", self.verify_token_setting)

        received = request.GET.get(self.token_param, "")
        if not hmac.compare_digest(expected, received):
            return HttpResponse(status=403)

        return HttpResponse(request.GET.get(self.challenge_param, ""))
