from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_selecting_patient
from care_im_wrapper.conversation.replies import enumerate_choices
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"

INVALID = "Please reply with a valid number from the list."


def _candidates(start=1):
    """The two ways a result can be picked, recorded exactly as the search offered them."""
    return [
        choice.candidate
        for choice in enumerate_choices(
            [
                ("Jane Doe", "+9193210", {"external_id": "ext-1", "name": "Jane Doe"}),
                ("John Roe", "+9111111", {"external_id": "ext-2", "name": "John Roe"}),
            ],
            prefix="patient",
            start=start,
        )
    ]


class HandleSelectingPatientTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.SELECTING_PATIENT,
            user_type="staff",
            user_id=7,
            candidates=_candidates(),
        )

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_a_row_id_selects_that_patient(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_0", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.active_patient_external_id, "ext-1")
        self.assertEqual(self.session.candidates, [])
        # No "now viewing X" prefix: the scope line on every reply already says whose these are.
        mock_send_menu.assert_called_once_with(self.session, PHONE, CHANNEL, outbox)
        self.assertEqual(outbox, [])

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_the_second_row_id_selects_the_second_patient(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.active_patient_external_id, "ext-2")

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_the_selected_name_is_remembered_for_the_scope_line(self, mock_send_menu):
        _handle_selecting_patient(self.session, PHONE, "patient_0", CHANNEL, [])

        self.session.refresh_from_db()
        self.assertEqual(self.session.active_patient_label, "Jane Doe")

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_a_typed_number_picks_the_row_it_is_printed_against(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.active_patient_external_id, "ext-1")

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_typed_numbers_follow_the_page_they_were_printed_on(self, mock_send_menu):
        """A second page keeps counting, so "3" is its first row -- and "1" is no longer here."""
        self.session.candidates = _candidates(start=3)
        self.session.save(update_fields=["candidates"])

        _handle_selecting_patient(self.session, PHONE, "3", CHANNEL, [])
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_patient_external_id, "ext-1")

    def test_a_number_from_another_page_is_not_silently_reinterpreted(self):
        self.session.candidates = _candidates(start=3)
        self.session.save(update_fields=["candidates"])

        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PATIENT)
        self.assertEqual(outbox, [Outbound(PHONE, INVALID)])

    def test_a_malformed_row_id_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_abc", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PATIENT)
        self.assertEqual(outbox, [Outbound(PHONE, INVALID)])

    def test_non_digit_non_patient_text_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "hello", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, INVALID)])

    def test_a_row_id_past_the_end_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_5", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PATIENT)
        self.assertEqual(outbox, [Outbound(PHONE, INVALID)])

    def test_a_negative_row_id_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "patient_-1", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, INVALID)])

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_zero_backs_out_to_the_menu(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_selecting_patient(self.session, PHONE, "0", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.candidates, [])
        self.assertIsNone(self.session.active_patient_external_id)
        mock_send_menu.assert_called_once_with(self.session, PHONE, CHANNEL, outbox)
