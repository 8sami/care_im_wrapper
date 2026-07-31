"""Regression tests for the outbound throttle aborting a turn that needs two messages.

The turn sent message 1, the throttle raised on message 2, the task retried, the
transaction rolled back as though message 1 had not gone out, and the retry replayed every
send -- delivering the same document four times and the menu zero times.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.messaging.exceptions import OutboundRateLimitedError
from care_im_wrapper.messaging.registry import send_message
from care_im_wrapper.models import ConversationSession
from tests.utils import override_test_cache

PHONE = "+919876543210"
CHANNEL = "whatsapp"


@override_test_cache()
class SendMessagePacingTests(TestCase):
    def setUp(self):
        # override_test_cache isolates per class, not per method.
        cache.clear()
        self.client_mock = MagicMock(supports_interactive=False, min_send_interval_seconds=6, max_interactive_rows=10)
        self.client_mock.send_text.return_value = "wamid.1"
        self.patcher = patch.dict(
            "care_im_wrapper.messaging.registry._PROVIDERS", {CHANNEL: lambda: self.client_mock}, clear=True
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_second_paced_send_in_the_window_is_rate_limited(self):
        send_message(CHANNEL, PHONE, "first")

        with self.assertRaises(OutboundRateLimitedError):
            send_message(CHANNEL, PHONE, "second")

    def test_continuation_send_is_not_rate_limited(self):
        send_message(CHANNEL, PHONE, "first")

        send_message(CHANNEL, PHONE, "continuation", pace=False)

        self.assertEqual(self.client_mock.send_text.call_count, 2)

    def test_continuation_does_not_consume_the_window_for_the_next_turn(self):
        """pace=False must not reset the throttle, or a continuation would let an unrelated
        later event through early."""
        send_message(CHANNEL, PHONE, "first", pace=False)

        send_message(CHANNEL, PHONE, "next turn")

        self.assertEqual(self.client_mock.send_text.call_count, 2)


@override_test_cache()
class DocumentSelectionSendTests(TestCase):
    """Selecting a document sends just the document against the real throttle and stays in
    the pick-list -- no menu re-send, no turn abort."""

    def setUp(self):
        cache.clear()
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.SELECTING_DOCUMENT,
            user_type="patient",
            user_id=42,
            candidates=[
                {"external_id": "uuid-1", "title": "Urine", "description": "20 Jul 2026 (Final)", "menu_key": "5"},
            ],
        )
        self.client_mock = MagicMock(supports_interactive=True, min_send_interval_seconds=6, max_interactive_rows=10)
        self.client_mock.send_interactive.return_value = "wamid.1"
        self.client_mock.send_text.return_value = "wamid.1"
        self.client_mock.max_message_chars = 4096
        self.client_mock.interactive_body_char_limit = 1024
        patcher = patch.dict(
            "care_im_wrapper.messaging.registry._PROVIDERS", {CHANNEL: lambda: self.client_mock}, clear=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.resolve_target_patient")
    @patch("care_im_wrapper.conversation.handlers.get_or_create_document_link")
    @patch("care_im_wrapper.conversation.handlers.build_document_url", return_value="https://example.com/d/tok/")
    def test_document_send_completes_and_stays_in_pick_list(
        self, mock_url, mock_link, mock_patient, mock_resolve_actor
    ):
        mock_resolve_actor.return_value = SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))
        entry = {"5": ("Lab reports", MagicMock(), MagicMock(), MagicMock(return_value=object()))}

        with patch.dict("care_im_wrapper.conversation.handlers._PATIENT_MENU", entry, clear=True):
            # Through run_state_machine (not the handler directly) so the send actually
            # flushes through the real throttle/provider after the turn commits.
            run_state_machine(PHONE, "document_0", CHANNEL)

        self.assertEqual(self.client_mock.send_interactive.call_count, 1)
        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_DOCUMENT)
