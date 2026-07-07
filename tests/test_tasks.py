from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
)
from care_im_wrapper.tasks import process_inbound_message
from tests.utils import OverrideCache

PHONE = "+919876543210"
CHANNEL = "whatsapp"


@OverrideCache
class ProcessInboundMessageDedupTests(SimpleTestCase):
    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_duplicate_raw_id_is_dropped_without_calling_state_machine(self, mock_run):
        process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-1")
        process_inbound_message(PHONE, "hello again", CHANNEL, raw_id="wamid-1")

        mock_run.assert_called_once_with(PHONE, "hello", CHANNEL)

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_different_raw_ids_both_processed(self, mock_run):
        process_inbound_message(PHONE, "first", CHANNEL, raw_id="wamid-1")
        process_inbound_message(PHONE, "second", CHANNEL, raw_id="wamid-2")

        self.assertEqual(mock_run.call_count, 2)

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_missing_raw_id_always_processes_even_if_repeated(self, mock_run):
        process_inbound_message(PHONE, "hello", CHANNEL, raw_id=None)
        process_inbound_message(PHONE, "hello", CHANNEL, raw_id=None)

        self.assertEqual(mock_run.call_count, 2)


@OverrideCache
class ProcessInboundMessagePendingTaskCleanupTests(SimpleTestCase):
    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_pending_task_cache_key_is_deleted_on_processing(self, mock_run):
        cache.set(f"pending_task:{PHONE}", True, timeout=60)

        process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-1")

        self.assertIsNone(cache.get(f"pending_task:{PHONE}"))
        mock_run.assert_called_once_with(PHONE, "hello", CHANNEL)


@OverrideCache
class ProcessInboundMessageErrorHandlingTests(SimpleTestCase):
    def _patch_retry(self):
        return patch.object(process_inbound_message, "retry", side_effect=lambda exc: exc)

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_pair_rate_limit_error_triggers_retry(self, mock_run):
        mock_run.side_effect = WhatsAppPairRateLimitError("rate limited")

        with self._patch_retry() as mock_retry:
            with self.assertRaises(WhatsAppPairRateLimitError):
                process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-1")

        mock_retry.assert_called_once()
        self.assertIsInstance(mock_retry.call_args.kwargs["exc"], WhatsAppPairRateLimitError)

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_network_error_triggers_retry(self, mock_run):
        mock_run.side_effect = WhatsAppNetworkError("network down")

        with self._patch_retry() as mock_retry:
            with self.assertRaises(WhatsAppNetworkError):
                process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-2")

        mock_retry.assert_called_once()

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_server_error_triggers_retry(self, mock_run):
        mock_run.side_effect = WhatsAppServerError("5xx")

        with self._patch_retry() as mock_retry:
            with self.assertRaises(WhatsAppServerError):
                process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-3")

        mock_retry.assert_called_once()

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_bad_request_error_is_logged_and_dropped_without_retry(self, mock_run):
        mock_run.side_effect = WhatsAppBadRequestError("malformed request")

        with self._patch_retry() as mock_retry:
            result = process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-4")

        self.assertIsNone(result)
        mock_retry.assert_not_called()

    @patch("care_im_wrapper.tasks.run_state_machine")
    def test_generic_exception_triggers_retry(self, mock_run):
        mock_run.side_effect = RuntimeError("unexpected boom")

        with self._patch_retry() as mock_retry:
            with self.assertRaises(RuntimeError):
                process_inbound_message(PHONE, "hello", CHANNEL, raw_id="wamid-5")

        mock_retry.assert_called_once()
        self.assertIsInstance(mock_retry.call_args.kwargs["exc"], RuntimeError)
