from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from care_im_wrapper.conversation.messages import (
    InteractivePayload,
    InteractiveType,
    OutboundMessage,
)
from care_im_wrapper.messaging import registry
from care_im_wrapper.messaging.exceptions import (
    OutboundRateLimitedError,
    WhatsAppBadRequestError,
    WhatsAppPairRateLimitError,
)
from care_im_wrapper.messaging.limits import default_limits
from tests.utils import channel_limits, override_test_cache


def _make_fake_client(*, supports_interactive: bool, max_message_chars: int = 1000):
    fake_client = MagicMock()
    fake_client.supports_interactive = supports_interactive
    fake_client.limits = channel_limits(text_body=max_message_chars)
    # Real pacing is exercised by SendMessageOutboundPacingTests; other tests here just
    # need is_outbound_rate_limited's cache.add(..., timeout=...) call to accept an int.
    fake_client.min_send_interval_seconds = 0
    return fake_client


class GetChannelLimitsTests(SimpleTestCase):
    """Capability lookup goes through the provider, so registering one is all it takes."""

    def test_an_unregistered_channel_gets_the_generic_defaults(self):
        self.assertEqual(registry.get_channel_limits("nonexistent"), default_limits())

    def test_a_registered_provider_describes_itself(self):
        fake_client = _make_fake_client(supports_interactive=False, max_message_chars=1000)

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            self.assertEqual(registry.get_channel_limits("fake").text_body, 1000)


@override_test_cache()
class SendMessageTests(SimpleTestCase):
    def test_unregistered_channel_does_nothing_and_returns_none(self):
        result = registry.send_message("nonexistent", "+919876543210", "hi")
        self.assertIsNone(result)

    def test_string_message_is_converted_and_sent_as_plain_text(self):
        fake_client = _make_fake_client(supports_interactive=False)

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            registry.send_message("fake", "+919876543210", "hello")
            fake_client.send_text.assert_called_once_with("+919876543210", "hello")
            fake_client.send_interactive.assert_not_called()

    def test_non_interactive_provider_ignores_interactive_payload(self):
        fake_client = _make_fake_client(supports_interactive=False)

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(
                text="hi",
                interactive=InteractivePayload(type=InteractiveType.LIST, body="test"),
            )
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_text.assert_called_once_with("+919876543210", "hi")
            fake_client.send_interactive.assert_not_called()

    def test_interactive_provider_with_interactive_payload_calls_send_interactive(self):
        fake_client = _make_fake_client(supports_interactive=True)

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(
                text="hi",
                interactive=InteractivePayload(type=InteractiveType.LIST, body="test"),
            )
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_interactive.assert_called_once_with("+919876543210", msg)
            fake_client.send_text.assert_not_called()

    def test_interactive_provider_without_interactive_payload_uses_send_text(self):
        fake_client = _make_fake_client(supports_interactive=True)

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(text="hi")
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_text.assert_called_once_with("+919876543210", "hi")
            fake_client.send_interactive.assert_not_called()

    def test_interactive_send_failure_falls_back_to_send_text(self):
        fake_client = _make_fake_client(supports_interactive=True)
        fake_client.send_interactive.side_effect = RuntimeError("boom")

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(
                text="hi",
                interactive=InteractivePayload(type=InteractiveType.LIST, body="test"),
            )
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_interactive.assert_called_once_with("+919876543210", msg)
            fake_client.send_text.assert_called_once_with("+919876543210", "hi")


@override_test_cache()
class SendMessageOutboundPacingTests(SimpleTestCase):
    # OverrideCache isolates per test class, not per test method, so each test below
    # uses its own dedicated phone number(s) to avoid cross-method cache leakage.
    def test_rate_limited_send_raises_without_calling_client(self):
        fake_client = _make_fake_client(supports_interactive=False)

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            with patch("care_im_wrapper.messaging.registry.is_outbound_rate_limited", return_value=True):
                with self.assertRaises(OutboundRateLimitedError):
                    registry.send_message("fake", "+919876500001", "hi")

            fake_client.send_text.assert_not_called()
            fake_client.send_interactive.assert_not_called()

    def test_second_send_within_min_interval_is_rate_limited(self):
        fake_client = _make_fake_client(supports_interactive=False)
        fake_client.min_send_interval_seconds = 6

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            registry.send_message("fake", "+919876500002", "first")
            with self.assertRaises(OutboundRateLimitedError):
                registry.send_message("fake", "+919876500002", "second")

        fake_client.send_text.assert_called_once_with("+919876500002", "first")

    def test_sends_to_different_phone_numbers_are_independent(self):
        fake_client = _make_fake_client(supports_interactive=False)
        fake_client.min_send_interval_seconds = 6

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            registry.send_message("fake", "+919876500003", "hi")
            registry.send_message("fake", "+919876500004", "hi")

        self.assertEqual(fake_client.send_text.call_count, 2)


@override_test_cache()
class SendMessageInteractiveFallbackTests(SimpleTestCase):
    """The plain-text fallback is for an interactive payload the provider won't render.
    A transient failure is not that: retrying it as text is a second request the provider
    has just refused, which against a per-recipient rate limit doubles the offence."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.msg = OutboundMessage(
            text="fallback text",
            interactive=InteractivePayload(type=InteractiveType.LIST, body="pick", action_data=[]),
        )

    def _send_with(self, interactive_error):
        client = _make_fake_client(supports_interactive=True)
        client.send_interactive.side_effect = interactive_error
        client.send_text.return_value = "wamid.text"
        with patch.dict(registry._PROVIDERS, {"whatsapp": lambda: client}):  # noqa: SLF001
            return client, registry.send_message("whatsapp", "+919876543210", self.msg)

    def test_transient_failure_propagates_without_a_second_send(self):
        client = _make_fake_client(supports_interactive=True)
        client.send_interactive.side_effect = WhatsAppPairRateLimitError("pair rate limit hit")

        with patch.dict(registry._PROVIDERS, {"whatsapp": lambda: client}):  # noqa: SLF001
            with self.assertRaises(WhatsAppPairRateLimitError):
                registry.send_message("whatsapp", "+919876543210", self.msg)

        client.send_text.assert_not_called()

    def test_payload_rejection_still_falls_back_to_text(self):
        client, tracking_id = self._send_with(WhatsAppBadRequestError("unsupported interactive shape"))

        self.assertEqual(tracking_id, "wamid.text")
        client.send_text.assert_called_once()
