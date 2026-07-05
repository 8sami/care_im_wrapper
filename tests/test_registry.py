from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from care_im_wrapper.conversation.messages import (
    InteractivePayload,
    InteractiveType,
    OutboundMessage,
)
from care_im_wrapper.messaging import registry


class GetMaxCharsTests(SimpleTestCase):
    def test_unregistered_channel_returns_default_4096(self):
        result = registry.get_max_chars("nonexistent")
        self.assertEqual(result, 4096)

    def test_registered_channel_returns_client_max_message_chars(self):
        fake_client = MagicMock()
        fake_client.supports_interactive = False
        fake_client.max_message_chars = 1000

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            result = registry.get_max_chars("fake")
            self.assertEqual(result, 1000)


class SendMessageTests(SimpleTestCase):
    def test_unregistered_channel_does_nothing_and_returns_none(self):
        result = registry.send_message("nonexistent", "+919876543210", "hi")
        self.assertIsNone(result)

    def test_string_message_is_converted_and_sent_as_plain_text(self):
        fake_client = MagicMock()
        fake_client.supports_interactive = False
        fake_client.max_message_chars = 1000

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            registry.send_message("fake", "+919876543210", "hello")
            fake_client.send_text.assert_called_once_with("+919876543210", "hello")
            fake_client.send_interactive.assert_not_called()

    def test_non_interactive_provider_ignores_interactive_payload(self):
        fake_client = MagicMock()
        fake_client.supports_interactive = False
        fake_client.max_message_chars = 1000

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(
                text="hi",
                interactive=InteractivePayload(type=InteractiveType.REPLY_BUTTONS, body="test"),
            )
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_text.assert_called_once_with("+919876543210", "hi")
            fake_client.send_interactive.assert_not_called()

    def test_interactive_provider_with_interactive_payload_calls_send_interactive(self):
        fake_client = MagicMock()
        fake_client.supports_interactive = True
        fake_client.max_message_chars = 1000

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(
                text="hi",
                interactive=InteractivePayload(type=InteractiveType.REPLY_BUTTONS, body="test"),
            )
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_interactive.assert_called_once_with("+919876543210", msg)
            fake_client.send_text.assert_not_called()

    def test_interactive_provider_without_interactive_payload_uses_send_text(self):
        fake_client = MagicMock()
        fake_client.supports_interactive = True
        fake_client.max_message_chars = 1000

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(text="hi")
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_text.assert_called_once_with("+919876543210", "hi")
            fake_client.send_interactive.assert_not_called()

    def test_interactive_send_failure_falls_back_to_send_text(self):
        fake_client = MagicMock()
        fake_client.supports_interactive = True
        fake_client.max_message_chars = 1000
        fake_client.send_interactive.side_effect = RuntimeError("boom")

        with patch.dict(registry._PROVIDERS, {"fake": lambda: fake_client}, clear=True):
            msg = OutboundMessage(
                text="hi",
                interactive=InteractivePayload(type=InteractiveType.REPLY_BUTTONS, body="test"),
            )
            registry.send_message("fake", "+919876543210", msg)
            fake_client.send_interactive.assert_called_once_with("+919876543210", msg)
            fake_client.send_text.assert_called_once_with("+919876543210", "hi")
