from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from care_im_wrapper.conversation.handlers import Outbound, run_state_machine
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
        mock_handle_new.assert_called_once()
        args = mock_handle_new.call_args[0]
        self.assertEqual(args[:4], (session, PHONE, "hello", CHANNEL))
        self.assertEqual(args[4], [])

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
        mock_handle_new.assert_called_once()
        args = mock_handle_new.call_args[0]
        self.assertEqual(args[:4], (session, PHONE, "hello", CHANNEL))
        self.assertEqual(args[4], [])
        mock_send.assert_not_called()


class RecordActivityIdleExpiryTests(TestCase):
    """A session idle past SESSION_IDLE_TIMEOUT_SECONDS is logged out on its next turn,
    so an abandoned authenticated session can't be resumed by whoever has the phone next."""

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers._handle_authenticated")
    def test_idle_authenticated_session_is_logged_out_and_dispatches_to_new(
        self, mock_handle_authenticated, mock_handle_new
    ):
        session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
            last_active_at=timezone.now() - timedelta(hours=2),
        )

        run_state_machine(PHONE, "1", CHANNEL)

        session.refresh_from_db()
        self.assertEqual(session.state, ConversationSession.State.NEW)
        self.assertEqual(session.user_type, ConversationSession.UserType.UNKNOWN)
        self.assertIsNone(session.user_id)
        mock_handle_authenticated.assert_not_called()
        mock_handle_new.assert_called_once()

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers._handle_authenticated")
    def test_recently_active_session_is_not_logged_out(self, mock_handle_authenticated, mock_handle_new):
        session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
            last_active_at=timezone.now(),
        )

        run_state_machine(PHONE, "1", CHANNEL)

        session.refresh_from_db()
        self.assertEqual(session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(session.user_id, 42)
        mock_handle_new.assert_not_called()
        mock_handle_authenticated.assert_called_once()

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_cooldown_state_is_exempt_from_idle_reset(self, mock_send, mock_handle_new):
        session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.COOLDOWN,
            cooldown_until=timezone.now() + timedelta(minutes=30),
            last_active_at=timezone.now() - timedelta(hours=2),
        )

        run_state_machine(PHONE, "hello", CHANNEL)

        session.refresh_from_db()
        self.assertEqual(session.state, ConversationSession.State.COOLDOWN)
        mock_handle_new.assert_not_called()
        mock_send.assert_called_once()
        self.assertTrue(mock_send.call_args[0][2].startswith("Your account is locked. Please try again in"))

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    def test_every_turn_stamps_last_active_at(self, mock_handle_new):
        session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            last_active_at=timezone.now() - timedelta(minutes=5),
        )
        stale_last_active_at = session.last_active_at

        run_state_machine(PHONE, "hello", CHANNEL)

        session.refresh_from_db()
        self.assertGreater(session.last_active_at, stale_last_active_at)


class RunStateMachineFlushTests(TestCase):
    """Covers docs/chat-reply-delivery-refactor.md's core guarantee: state is committed
    before any send, and a send failure mid-flush doesn't propagate back into the caller
    (so a Celery task wrapping run_state_machine never retries a turn that already
    advanced its state)."""

    @patch("care_im_wrapper.conversation.handlers._handle_new")
    @patch("care_im_wrapper.conversation.handlers.send_message")
    def test_flush_failure_after_the_first_send_does_not_raise_and_state_stays_committed(
        self, mock_send, mock_handle_new
    ):
        def _append_two_messages(session, phone_number, text, channel, outbox):
            session.state = ConversationSession.State.AWAITING_YOB
            session.save(update_fields=["state"])
            outbox.append(Outbound(phone_number, "first message"))
            outbox.append(Outbound(phone_number, "second message"))

        mock_handle_new.side_effect = _append_two_messages
        mock_send.side_effect = ["wamid.1", Exception("network error")]

        run_state_machine(PHONE, "hello", CHANNEL)  # must not raise

        session = ConversationSession.objects.get(phone_number=PHONE, provider=CHANNEL)
        self.assertEqual(session.state, ConversationSession.State.AWAITING_YOB)
        self.assertEqual(mock_send.call_count, 2)
