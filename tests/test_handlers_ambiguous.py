from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import _handle_ambiguous
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


class HandleAmbiguousTests(TestCase):
    def setUp(self):
        self.candidate_a = {
            "user_type": "patient",
            "user_id": 42,
            "year_of_birth": 1990,
            "full_name": "Jane Doe",
            "phone_number": PHONE,
        }
        self.candidate_b = {
            "user_type": "staff",
            "user_id": 99,
            "year_of_birth": 1985,
            "full_name": "John Roe",
            "phone_number": "+911111111111",
        }
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AMBIGUOUS,
            candidates=[self.candidate_a, self.candidate_b],
        )

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_candidate_prefixed_choice_selects_by_one_based_index(self, mock_send, mock_send_menu):
        _handle_ambiguous(self.session, PHONE, "candidate_1", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.user_id, 42)
        self.assertEqual(self.session.snapshot_name, "Jane Doe")
        mock_send_menu.assert_called_once_with(PHONE, "patient", name="Jane Doe", channel=CHANNEL)
        mock_send.assert_not_called()

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_candidate_prefixed_choice_second_index_selects_second_candidate(self, mock_send, mock_send_menu):
        _handle_ambiguous(self.session, PHONE, "candidate_2", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.user_id, 99)
        self.assertEqual(self.session.snapshot_name, "John Roe")
        mock_send_menu.assert_called_once_with(PHONE, "staff", name="John Roe", channel=CHANNEL)

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_plain_digit_choice_also_selects_by_one_based_index(self, mock_send, mock_send_menu):
        _handle_ambiguous(self.session, PHONE, "1", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.user_id, 42)
        mock_send_menu.assert_called_once_with(PHONE, "patient", name="Jane Doe", channel=CHANNEL)

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_candidate_prefix_with_non_integer_suffix_sends_invalid_choice(self, mock_send):
        _handle_ambiguous(self.session, PHONE, "candidate_abc", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please reply with a valid number from the list.")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_non_digit_non_candidate_text_sends_invalid_choice(self, mock_send):
        _handle_ambiguous(self.session, PHONE, "hello", CHANNEL)

        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please reply with a valid number from the list.")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_out_of_range_index_too_high_sends_invalid_choice(self, mock_send):
        _handle_ambiguous(self.session, PHONE, "candidate_5", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please reply with a valid number from the list.")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_out_of_range_index_zero_sends_invalid_choice(self, mock_send):
        _handle_ambiguous(self.session, PHONE, "candidate_0", CHANNEL)

        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please reply with a valid number from the list.")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_negative_index_sends_invalid_choice(self, mock_send):
        _handle_ambiguous(self.session, PHONE, "candidate_-1", CHANNEL)

        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please reply with a valid number from the list.")
