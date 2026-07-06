from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


class RunStateMachineDispatchTests(TestCase):
    @patch("care_im_wrapper.conversation.handlers._handle_new")
    def test_creates_new_session_and_dispatches_to_handle_new(self, mock_handle_new):
        self.assertFalse(ConversationSession.objects.filter(phone_number=PHONE, provider=CHANNEL).exists())

        run_state_machine(PHONE, "hello", CHANNEL)

        session = ConversationSession.objects.get(phone_number=PHONE, provider=CHANNEL)
        self.assertEqual(session.state, ConversationSession.State.NEW)
        mock_handle_new.assert_called_once_with(session, PHONE, "hello", CHANNEL)

    @patch("care_im_wrapper.conversation.handlers._handle_authenticated")
    def test_existing_authenticated_session_dispatches_to_handle_authenticated(self, mock_handler):
        session = ConversationSession.objects.create(
            phone_number=PHONE, provider=CHANNEL, state=ConversationSession.State.AUTHENTICATED
        )

        run_state_machine(PHONE, "1", CHANNEL)

        mock_handler.assert_called_once()
        called_session = mock_handler.call_args[0][0]
        self.assertEqual(called_session.pk, session.pk)

    @patch("care_im_wrapper.conversation.handlers._handle_awaiting_yob")
    def test_awaiting_yob_state_dispatches_correctly(self, mock_handler):
        ConversationSession.objects.create(
            phone_number=PHONE, provider=CHANNEL, state=ConversationSession.State.AWAITING_YOB
        )

        run_state_machine(PHONE, "1990", CHANNEL)

        mock_handler.assert_called_once()

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers.logger")
    def test_unrecognized_state_logs_error_and_calls_no_handler(self, mock_logger, mock_handle_new):
        ConversationSession.objects.create(phone_number=PHONE, provider=CHANNEL, state="bogus_state")

        run_state_machine(PHONE, "hello", CHANNEL)

        mock_handle_new.assert_not_called()
        mock_logger.error.assert_called_once_with("run_state_machine: unhandled state %s", "bogus_state")


class RunStateMachineCooldownGateTests(TestCase):
    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_active_cooldown_sends_cooldown_message_and_skips_dispatch(self, mock_send, mock_handle_new):
        ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.COOLDOWN,
            cooldown_until=timezone.now() + timedelta(minutes=30),
        )

        run_state_machine(PHONE, "hello", CHANNEL)

        mock_handle_new.assert_not_called()
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        self.assertEqual(call_args[0], CHANNEL)
        self.assertEqual(call_args[1], PHONE)
        self.assertTrue(call_args[2].startswith("Your account is locked. Please try again in"))

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_expired_cooldown_resets_session_and_dispatches_to_new_state(self, mock_send, mock_handle_new):
        session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.COOLDOWN,
            cooldown_until=timezone.now() - timedelta(minutes=5),
            failed_attempts=5,
        )

        run_state_machine(PHONE, "hello", CHANNEL)

        session.refresh_from_db()
        self.assertEqual(session.state, ConversationSession.State.NEW)
        self.assertEqual(session.failed_attempts, 0)
        self.assertIsNone(session.cooldown_until)
        mock_handle_new.assert_called_once_with(session, PHONE, "hello", CHANNEL)
        mock_send.assert_not_called()
