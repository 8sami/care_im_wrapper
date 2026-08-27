from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_ambiguous
from care_im_wrapper.conversation.replies import enumerate_choices
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"

INVALID = "Sorry, that wasn't one of the options. Please pick one from the list below."

IDENTITY_A = {
    "user_type": "patient",
    "user_id": 42,
    "year_of_birth": 1990,
    "full_name": "Jane Doe",
    "phone_number": PHONE,
}
IDENTITY_B = {
    "user_type": "staff",
    "user_id": 99,
    "year_of_birth": 1985,
    "full_name": "John Roe",
    "phone_number": "+911111111111",
}


def _candidates():
    """The accounts as _handle_awaiting_yob offers them: row ids from 0, numbers from 1."""
    return [
        choice.candidate
        for choice in enumerate_choices(
            [(i["full_name"], i["user_type"].capitalize(), i) for i in (IDENTITY_A, IDENTITY_B)],
            prefix="candidate",
            start=1,
        )
    ]


def _assert_reprompted(case, outbox):
    """An unrecognised reply puts the accounts back on screen rather than ending in text."""
    [sent] = outbox
    case.assertIn(INVALID, sent.message.text)
    rows = sent.message.interactive.action_data[0]["rows"]
    case.assertEqual([r["id"] for r in rows], ["candidate_0", "candidate_1"])


class HandleAmbiguousTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AMBIGUOUS,
            candidates=_candidates(),
        )

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_a_row_id_selects_that_account(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_0", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.user_id, 42)
        self.assertEqual(self.session.snapshot_name, "Jane Doe")
        mock_send_menu.assert_called_once_with(self.session, PHONE, CHANNEL, outbox, name="Jane Doe")
        self.assertEqual(outbox, [])

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_the_second_row_id_selects_the_second_account(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.user_id, 99)
        self.assertEqual(self.session.snapshot_name, "John Roe")
        mock_send_menu.assert_called_once_with(self.session, PHONE, CHANNEL, outbox, name="John Roe")

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_a_typed_number_picks_the_row_it_is_printed_against(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.user_id, 42)
        mock_send_menu.assert_called_once_with(self.session, PHONE, CHANNEL, outbox, name="Jane Doe")

    def test_a_malformed_row_id_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_abc", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        _assert_reprompted(self, outbox)

    def test_non_digit_non_candidate_text_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "hello", CHANNEL, outbox)

        _assert_reprompted(self, outbox)

    def test_a_row_id_past_the_end_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_5", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        _assert_reprompted(self, outbox)

    def test_zero_is_not_an_account_here(self):
        """Numbering starts at 1, so "0" matches no token and no row id."""
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "0", CHANNEL, outbox)

        _assert_reprompted(self, outbox)

    def test_a_negative_row_id_sends_invalid_choice(self):
        outbox: list[Outbound] = []
        _handle_ambiguous(self.session, PHONE, "candidate_-1", CHANNEL, outbox)

        _assert_reprompted(self, outbox)
