from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_ambiguous
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
    def test_candidate_prefixed_choice_selects_by_one_based_index(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.user_id, 42)
        self.assertEqual(self.session.snapshot_name, "Jane Doe")
        mock_send_menu.assert_called_once_with(PHONE, "patient", CHANNEL, outbox, name="Jane Doe")
        self.assertEqual(outbox, [])

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    def test_candidate_prefixed_choice_second_index_selects_second_candidate(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_2", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.user_id, 99)
        self.assertEqual(self.session.snapshot_name, "John Roe")
        mock_send_menu.assert_called_once_with(PHONE, "staff", CHANNEL, outbox, name="John Roe")

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    def test_plain_digit_choice_also_selects_by_one_based_index(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.user_id, 42)
        mock_send_menu.assert_called_once_with(PHONE, "patient", CHANNEL, outbox, name="Jane Doe")

    def test_candidate_prefix_with_non_integer_suffix_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_abc", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_non_digit_non_candidate_text_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "hello", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_out_of_range_index_too_high_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_5", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_out_of_range_index_zero_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_0", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_negative_index_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_-1", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])
