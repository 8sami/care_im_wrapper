from unittest.mock import patch

from django.test import TestCase, override_settings

from care_im_wrapper.auth.resolver import ResolutionResult, ResolvedIdentity
from care_im_wrapper.conversation.handlers import Outbound, _handle_awaiting_yob, _handle_new
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


class HandleNewTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(phone_number=PHONE, provider=CHANNEL)

    @patch("care_im_wrapper.conversation.handlers.resolve_phone_number")
    def test_not_found_sends_not_found_message_and_leaves_state_unchanged(self, mock_resolve):
        mock_resolve.return_value = ResolutionResult(found=False, identities=[])

        outbox: list[Outbound] = []
        _handle_new(self.session, PHONE, "hello", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.NEW)
        self.assertEqual(outbox, [Outbound(PHONE, "Sorry, we couldn't find an account linked to your number.")])

    @patch("care_im_wrapper.conversation.handlers.resolve_phone_number")
    def test_found_stores_candidates_and_moves_to_awaiting_yob(self, mock_resolve):
        identity = ResolvedIdentity(
            user_type="patient", user_id=42, year_of_birth=1990, full_name="Jane Doe", phone_number=PHONE
        )
        mock_resolve.return_value = ResolutionResult(found=True, identities=[identity])

        outbox: list[Outbound] = []
        _handle_new(self.session, PHONE, "hello", CHANNEL, outbox)

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
        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with your year of birth (e.g. 1990).")])


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

    def test_non_four_digit_input_sends_yob_invalid(self):
        outbox: list[Outbound] = []
        _handle_awaiting_yob(self.session, PHONE, "abc", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_YOB)
        self.assertEqual(outbox, [Outbound(PHONE, "Please enter a valid 4-digit year (e.g. 1990).")])

    def test_three_digit_input_sends_yob_invalid(self):
        outbox: list[Outbound] = []
        _handle_awaiting_yob(self.session, PHONE, "199", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please enter a valid 4-digit year (e.g. 1990).")])

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"MAX_FAILED_ATTEMPTS": 3}})
    def test_wrong_year_increments_failed_attempts_and_sends_remaining_count(self):
        outbox: list[Outbound] = []
        _handle_awaiting_yob(self.session, PHONE, "1985", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_YOB)
        self.assertEqual(self.session.failed_attempts, 1)
        self.assertEqual(outbox, [Outbound(PHONE, "That doesn't match. You have *2* attempt(s) remaining.")])

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"MAX_FAILED_ATTEMPTS": 5}})
    def test_fifth_wrong_attempt_triggers_cooldown(self):
        self.session.failed_attempts = 4
        self.session.save(update_fields=["failed_attempts"])

        outbox: list[Outbound] = []
        _handle_awaiting_yob(self.session, PHONE, "1985", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.COOLDOWN)
        self.assertIsNotNone(self.session.cooldown_until)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].phone_number, PHONE)
        self.assertTrue(outbox[0].message.startswith("Your account is locked. Please try again in"))
        self.assertTrue(outbox[0].message.endswith("minutes."))

    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_single_matching_candidate_authenticates_and_sends_main_menu(self, mock_send_menu):
        outbox: list[Outbound] = []
        _handle_awaiting_yob(self.session, PHONE, "1990", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.user_type, "patient")
        self.assertEqual(self.session.user_id, 42)
        self.assertEqual(self.session.snapshot_name, "Jane Doe")
        self.assertEqual(self.session.snapshot_phone, PHONE)
        self.assertEqual(self.session.failed_attempts, 0)
        mock_send_menu.assert_called_once_with(self.session, PHONE, CHANNEL, outbox, name="Jane Doe")
        self.assertEqual(outbox, [])

    @patch("care_im_wrapper.conversation.handlers._send_candidate_menu")
    def test_multiple_matching_candidates_moves_to_ambiguous_state(self, mock_send_candidate_menu):
        second_candidate = {
            "user_type": "staff",
            "user_id": 99,
            "year_of_birth": 1990,
            "full_name": "John Roe",
            "phone_number": "+911111111111",
        }
        self.session.candidates = [self.candidate, second_candidate]
        self.session.save(update_fields=["candidates"])

        outbox: list[Outbound] = []
        _handle_awaiting_yob(self.session, PHONE, "1990", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AMBIGUOUS)
        # Stored as offered: the identity, plus the row id and number that pick it.
        self.assertEqual([c["full_name"] for c in self.session.candidates], ["Jane Doe", "John Roe"])
        self.assertEqual([c["row_id"] for c in self.session.candidates], ["candidate_0", "candidate_1"])
        self.assertEqual([c["token"] for c in self.session.candidates], ["1", "2"])

        phone, choices, channel, sent_outbox = mock_send_candidate_menu.call_args.args
        self.assertEqual((phone, channel, sent_outbox), (PHONE, CHANNEL, outbox))
        self.assertEqual([choice.title for choice in choices], ["Jane Doe", "John Roe"])
        self.assertEqual(outbox, [])
