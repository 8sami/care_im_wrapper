from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import _handle_authenticated
from care_im_wrapper.conversation.messages import InteractiveType, OutboundMessage
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"
GREETING = "Please choose an option:"  # len == 25
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
        entry = {"1": ("Test Label", fetcher, renderer)}
        return patch.dict("care_im_wrapper.conversation.handlers._PATIENT_MENU", entry, clear=True)

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_short_data_sends_single_combined_message(self, mock_send, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()

        with self._patch_menu("DATA"):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL)

        mock_send.assert_called_once()
        call_channel, call_phone, call_msg = mock_send.call_args[0]
        self.assertEqual(call_channel, CHANNEL)
        self.assertEqual(call_phone, PHONE)

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
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_data_exceeding_limit_splits_into_two_messages(self, mock_send, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        long_text = "A" * 1000  # + GREETING(25) = 1025 > 1024 -> triggers fallback

        with self._patch_menu(long_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL)

        self.assertEqual(mock_send.call_count, 2)

        first_call = mock_send.call_args_list[0][0]
        self.assertEqual(first_call[0], CHANNEL)
        self.assertEqual(first_call[1], PHONE)
        self.assertEqual(first_call[2].text, long_text)
        self.assertIsNone(first_call[2].interactive)

        second_call = mock_send.call_args_list[1][0]
        self.assertEqual(second_call[0], CHANNEL)
        self.assertEqual(second_call[1], PHONE)
        self.assertEqual(second_call[2].text, GREETING)
        self.assertIsNotNone(second_call[2].interactive)
        self.assertEqual(second_call[2].interactive.body, GREETING)

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_data_length_exactly_at_boundary_does_not_trigger_fallback(
        self, mock_send, mock_resolve_actor, mock_max_chars
    ):
        mock_resolve_actor.return_value = _make_actor()
        # len(text) + len(GREETING) == LIMIT exactly -> condition is strict ">", so this must
        # NOT trigger the fallback (single combined message expected).
        boundary_text = "A" * (LIMIT - len(GREETING))  # 999 chars, sum == 1024

        with self._patch_menu(boundary_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL)

        mock_send.assert_called_once()

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_data_length_one_over_boundary_triggers_fallback(self, mock_send, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        # One character more than the boundary case above -> sum == LIMIT + 1 -> must trigger fallback.
        over_boundary_text = "A" * (LIMIT - len(GREETING) + 1)  # 1000 chars, sum == 1025

        with self._patch_menu(over_boundary_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL)

        self.assertEqual(mock_send.call_count, 2)
