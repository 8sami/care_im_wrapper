import hashlib
import hmac
import json
from unittest.mock import MagicMock

from django.test import RequestFactory, TestCase, override_settings

from care_im_wrapper.signals import meta_message_received, meta_status_updated
from care_im_wrapper.webhooks.providers.meta import MetaWebhookView

SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
VALID_SETTINGS = {
    "care_im_wrapper": {
        "WHATSAPP_APP_SECRET": SECRET,
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN": VERIFY_TOKEN,
    }
}


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@override_settings(PLUGIN_CONFIGS=VALID_SETTINGS)
class MetaWebhookChallengeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.view = MetaWebhookView.as_view()

    def test_correct_mode_and_token_returns_challenge(self):
        request = self.factory.get(
            "/webhooks/meta/",
            {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"},
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"12345")

    def test_wrong_mode_returns_403(self):
        request = self.factory.get(
            "/webhooks/meta/",
            {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"},
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 403)

    def test_correct_mode_wrong_token_returns_403(self):
        request = self.factory.get(
            "/webhooks/meta/",
            {"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "12345"},
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 403)

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WHATSAPP_WEBHOOK_VERIFY_TOKEN": ""}})
    def test_unconfigured_verify_token_returns_500(self):
        request = self.factory.get(
            "/webhooks/meta/",
            {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"},
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 500)

    def test_missing_challenge_param_returns_empty_200(self):
        request = self.factory.get("/webhooks/meta/", {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN})

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")


@override_settings(PLUGIN_CONFIGS=VALID_SETTINGS)
class MetaWebhookSignatureTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.view = MetaWebhookView.as_view()

    def test_valid_signature_with_empty_entries_returns_200(self):
        body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
        request = self.factory.post(
            "/webhooks/meta/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_sign(body),
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 200)

    def test_invalid_signature_returns_401(self):
        body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
        request = self.factory.post(
            "/webhooks/meta/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=" + ("0" * 64),
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 401)

    def test_missing_signature_header_returns_401(self):
        body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
        request = self.factory.post("/webhooks/meta/", data=body, content_type="application/json")

        response = self.view(request)

        self.assertEqual(response.status_code, 401)

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WHATSAPP_APP_SECRET": ""}})
    def test_unconfigured_secret_returns_401(self):
        body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
        request = self.factory.post(
            "/webhooks/meta/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_sign(body),
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 401)

    def test_invalid_json_body_returns_400_even_with_valid_signature(self):
        body = b"not valid json{{{"
        request = self.factory.post(
            "/webhooks/meta/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_sign(body),
        )

        response = self.view(request)

        # NOTE: payload parsing happens before signature verification in WebhookView.post(),
        # so a malformed body returns 400 regardless of whether the signature is valid.
        self.assertEqual(response.status_code, 400)


@override_settings(PLUGIN_CONFIGS=VALID_SETTINGS)
class MetaWebhookDispatchTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.view = MetaWebhookView.as_view()
        self.message_receiver = MagicMock()
        self.status_receiver = MagicMock()
        meta_message_received.connect(self.message_receiver, weak=False)
        meta_status_updated.connect(self.status_receiver, weak=False)

    def tearDown(self):
        meta_message_received.disconnect(self.message_receiver)
        meta_status_updated.disconnect(self.status_receiver)

    def _post(self, payload):
        body = json.dumps(payload).encode()
        request = self.factory.post(
            "/webhooks/meta/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_sign(body),
        )
        return self.view(request)

    def test_message_in_payload_sends_meta_message_received_with_mapped_channel(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [{"id": "wamid1", "from": "+91"}]}}]}],
        }

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.message_receiver.assert_called_once()
        call_kwargs = self.message_receiver.call_args.kwargs
        self.assertEqual(call_kwargs["payload"], {"id": "wamid1", "from": "+91"})
        self.assertEqual(call_kwargs["channel"], "whatsapp")
        self.status_receiver.assert_not_called()

    def test_status_in_payload_sends_meta_status_updated(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid1", "status": "delivered"}]}}]}],
        }

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.status_receiver.assert_called_once()
        call_kwargs = self.status_receiver.call_args.kwargs
        self.assertEqual(call_kwargs["payload"], {"id": "wamid1", "status": "delivered"})
        self.assertEqual(call_kwargs["channel"], "whatsapp")
        self.message_receiver.assert_not_called()

    def test_unmapped_object_value_passes_through_unchanged_as_channel(self):
        payload = {
            "object": "some_other_object",
            "entry": [{"changes": [{"value": {"messages": [{"id": "m1"}]}}]}],
        }

        self._post(payload)

        call_kwargs = self.message_receiver.call_args.kwargs
        self.assertEqual(call_kwargs["channel"], "some_other_object")

    def test_multiple_entries_and_changes_all_dispatch(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {"changes": [{"value": {"messages": [{"id": "m1"}]}}]},
                {"changes": [{"value": {"messages": [{"id": "m2"}]}}]},
            ],
        }

        self._post(payload)

        self.assertEqual(self.message_receiver.call_count, 2)

    def test_unhandled_exception_in_dispatch_returns_500(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": "not-a-list-of-dicts-so-iterating-changes-breaks"}],
        }
        # Force an unhandled error: change.get(...) on a string will raise AttributeError,
        # which is caught by the bare `except Exception` in WebhookView.post() -> 500.
        body = json.dumps(payload).encode()
        request = self.factory.post(
            "/webhooks/meta/",
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_sign(body),
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 500)
