from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_authenticated
from care_im_wrapper.conversation.messages import InteractiveType, OutboundMessage
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"
GREETING = "Please choose an option:"  # len == 24
LIMIT = 1024  # WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT default


def _make_actor():
    return SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))


class HandleAuthenticatedSuccessPathTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _patch_menu(self, renderer_text):
        fetcher = MagicMock(return_value="fake_data")
        renderer = MagicMock(return_value=OutboundMessage(text=renderer_text))
        entry = {"1": ("Test Label", fetcher, renderer, None)}
        return patch.dict("care_im_wrapper.conversation.handlers._PATIENT_MENU", entry, clear=True)

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_short_data_sends_single_combined_message(self, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu("DATA"):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 1)
        item = outbox[0]
        self.assertEqual(item.phone_number, PHONE)
        call_msg = item.message

        expected_full_text = "DATA\n\nPlease choose an option:\n\n1. Test Label\n0. Logout"
        self.assertEqual(call_msg.text, expected_full_text)
        self.assertIsNotNone(call_msg.interactive)
        self.assertEqual(call_msg.interactive.body, "DATA\n\nPlease choose an option:")
        self.assertEqual(call_msg.interactive.type, InteractiveType.LIST)
        self.assertEqual(call_msg.interactive.button_label, "View Menu")
        self.assertEqual(
            call_msg.interactive.action_data,
            [{"title": "Menu", "rows": [{"id": "1", "title": "Test Label"}, {"id": "0", "title": "Logout"}]}],
        )

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_data_exceeding_limit_splits_into_two_messages(self, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        long_text = "A" * 1001  # + GREETING(24) = 1025 > 1024 -> triggers fallback
        outbox: list[Outbound] = []

        with self._patch_menu(long_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 2)

        first = outbox[0]
        self.assertEqual(first.phone_number, PHONE)
        self.assertEqual(first.message.text, long_text)
        self.assertIsNone(first.message.interactive)
        self.assertTrue(first.pace)

        second = outbox[1]
        self.assertEqual(second.phone_number, PHONE)
        self.assertEqual(second.message.text, GREETING)
        self.assertIsNotNone(second.message.interactive)
        self.assertEqual(second.message.interactive.body, GREETING)
        self.assertFalse(second.pace)

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_data_length_exactly_at_boundary_does_not_trigger_fallback(self, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        # len(text) + len(GREETING) == LIMIT exactly -> condition is strict ">", so this must
        # NOT trigger the fallback (single combined message expected).
        boundary_text = "A" * (LIMIT - len(GREETING))  # 999 chars, sum == 1024
        outbox: list[Outbound] = []

        with self._patch_menu(boundary_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 1)

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_data_length_one_over_boundary_triggers_fallback(self, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        # One character more than the boundary case above -> sum == LIMIT + 1 -> must trigger fallback.
        over_boundary_text = "A" * (LIMIT - len(GREETING) + 1)  # 1000 chars, sum == 1025
        outbox: list[Outbound] = []

        with self._patch_menu(over_boundary_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 2)
