from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_selecting_patient
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


class HandleSelectingPatientTests(TestCase):
    def setUp(self):
        self.candidate_a = {"external_id": "ext-1", "name": "Jane Doe", "phone_number": "+919****3210"}
        self.candidate_b = {"external_id": "ext-2", "name": "John Roe", "phone_number": "+911****1111"}
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.SELECTING_PATIENT,
            user_type="staff",
            user_id=7,
            candidates=[self.candidate_a, self.candidate_b],
        )

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    def test_patient_prefixed_choice_zero_based_selects_first_candidate(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_0", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.active_patient_external_id, "ext-1")
        self.assertEqual(self.session.candidates, [])
        mock_send_menu.assert_called_once_with(
            PHONE, "staff", CHANNEL, outbox, prefix="Viewing records for *Jane Doe*. What would you like to see?"
        )
        self.assertEqual(outbox, [])

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    def test_patient_prefixed_choice_index_one_selects_second_candidate(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.active_patient_external_id, "ext-2")
        mock_send_menu.assert_called_once_with(
            PHONE, "staff", CHANNEL, outbox, prefix="Viewing records for *John Roe*. What would you like to see?"
        )

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    def test_plain_digit_choice_is_one_based_and_selects_first_candidate(self, mock_send_menu):
        # NOTE: unlike the "patient_" prefix path (zero-based), the plain-digit fallback
        # is one-based — "1" means index 0, matching numbered_list()'s 1-based display.
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.active_patient_external_id, "ext-1")

    def test_patient_prefix_with_non_integer_suffix_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_abc", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PATIENT)
        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_non_digit_non_patient_text_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "hello", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_patient_index_out_of_range_too_high_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_5", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PATIENT)
        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_patient_index_negative_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_-1", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    def test_plain_digit_zero_is_out_of_range_since_one_based(self):
        # "0" -> index -1 under the one-based plain-digit path -> out of range -> invalid.
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "0", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])
