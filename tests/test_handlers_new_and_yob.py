from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.auth.resolver import ResolutionResult, ResolvedIdentity
from care_im_wrapper.conversation.handlers import _handle_awaiting_yob, _handle_new
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


class HandleNewTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(phone_number=PHONE, provider=CHANNEL)

    @patch("care_im_wrapper.conversation.handlers.send_message")
    @patch("care_im_wrapper.conversation.handlers.resolve_phone_number")
    def test_not_found_sends_not_found_message_and_leaves_state_unchanged(self, mock_resolve, mock_send):
        mock_resolve.return_value = ResolutionResult(found=False, identities=[])

        _handle_new(self.session, PHONE, "hello", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.NEW)
        mock_send.assert_called_once_with(CHANNEL, PHONE, "Sorry, we couldn't find an account linked to your number.")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    @patch("care_im_wrapper.conversation.handlers.resolve_phone_number")
    def test_found_stores_candidates_and_moves_to_awaiting_yob(self, mock_resolve, mock_send):
        identity = ResolvedIdentity(
            user_type="patient", user_id=42, year_of_birth=1990, full_name="Jane Doe", phone_number=PHONE
        )
        mock_resolve.return_value = ResolutionResult(found=True, identities=[identity])

        _handle_new(self.session, PHONE, "hello", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_YOB)
        self.assertEqual(
            self.session.candidates,
            [
                {
                    "user_type": "patient",
                    "user_id": 42,
                    "year_of_birth": 1990,
                    "full_name": "Jane Doe",
                    "phone_number": PHONE,
                }
            ],
        )
        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please reply with your year of birth (e.g. 1990).")


class HandleAwaitingYobTests(TestCase):
    def setUp(self):
        self.candidate = {
            "user_type": "patient",
            "user_id": 42,
            "year_of_birth": 1990,
            "full_name": "Jane Doe",
            "phone_number": PHONE,
        }
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AWAITING_YOB,
            candidates=[self.candidate],
        )

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_non_four_digit_input_sends_yob_invalid(self, mock_send):
        _handle_awaiting_yob(self.session, PHONE, "abc", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_YOB)
        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please enter a valid 4-digit year (e.g. 1990).")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_three_digit_input_sends_yob_invalid(self, mock_send):
        _handle_awaiting_yob(self.session, PHONE, "199", CHANNEL)

        mock_send.assert_called_once_with(CHANNEL, PHONE, "Please enter a valid 4-digit year (e.g. 1990).")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_wrong_year_increments_failed_attempts_and_sends_remaining_count(self, mock_send):
        _handle_awaiting_yob(self.session, PHONE, "1985", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_YOB)
        self.assertEqual(self.session.failed_attempts, 1)
        mock_send.assert_called_once_with(CHANNEL, PHONE, "That doesn't match. You have *2* attempt(s) remaining.")

    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_fifth_wrong_attempt_triggers_cooldown(self, mock_send):
        self.session.failed_attempts = 4
        self.session.save(update_fields=["failed_attempts"])

        _handle_awaiting_yob(self.session, PHONE, "1985", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.COOLDOWN)
        self.assertIsNotNone(self.session.cooldown_until)
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        self.assertEqual(call_args[0], CHANNEL)
        self.assertEqual(call_args[1], PHONE)
        self.assertTrue(call_args[2].startswith("Your account is locked. Please try again in"))
        self.assertTrue(call_args[2].endswith("minutes."))

    @patch("care_im_wrapper.conversation.handlers._send_main_menu")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_single_matching_candidate_authenticates_and_sends_main_menu(self, mock_send, mock_send_menu):
        _handle_awaiting_yob(self.session, PHONE, "1990", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.user_type, "patient")
        self.assertEqual(self.session.user_id, 42)
        self.assertEqual(self.session.snapshot_name, "Jane Doe")
        self.assertEqual(self.session.snapshot_phone, PHONE)
        self.assertEqual(self.session.failed_attempts, 0)
        mock_send_menu.assert_called_once_with(PHONE, "patient", name="Jane Doe", channel=CHANNEL)
        mock_send.assert_not_called()

    @patch("care_im_wrapper.conversation.handlers._send_candidate_menu")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_multiple_matching_candidates_moves_to_ambiguous_state(self, mock_send, mock_send_candidate_menu):
        second_candidate = {
            "user_type": "staff",
            "user_id": 99,
            "year_of_birth": 1990,
            "full_name": "John Roe",
            "phone_number": "+911111111111",
        }
        self.session.candidates = [self.candidate, second_candidate]
        self.session.save(update_fields=["candidates"])

        _handle_awaiting_yob(self.session, PHONE, "1990", CHANNEL)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        self.assertEqual(self.session.candidates, [self.candidate, second_candidate])
        mock_send_candidate_menu.assert_called_once_with(PHONE, [self.candidate, second_candidate], CHANNEL)
        mock_send.assert_not_called()
