"""The Celery tasks that actually put a notification on the wire.

These cover the paths a patient feels when something goes wrong: a send that must not be
retried, one that must, a duplicate worker racing for the same recipient, and the sweep
that picks up whatever real-time dispatch dropped.
"""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from care_im_wrapper.conversation.messages import SentTemplate
from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppTemplateNotConfiguredError,
)
from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationEvent,
    NotificationRecipient,
    NotificationStatus,
    NotificationStatusState,
    NotificationTemplate,
    NotificationTrigger,
    TriggerType,
)
from care_im_wrapper.tasks import (
    dispatch_notification_recipient,
    dispatch_pending_notification_recipients,
    process_status_update,
    sync_notification_templates,
)

SEND = "care_im_wrapper.tasks.send_template_message"


class DispatchTestBase(TestCase):
    def setUp(self):
        # Pacing is exercised on its own in DispatchPacingTests; everywhere else it would
        # short-circuit the send under test before it ever reaches the provider.
        pacing = patch("care_im_wrapper.tasks.is_outbound_rate_limited", return_value=False)
        pacing.start()
        self.addCleanup(pacing.stop)

        self.template = NotificationTemplate.objects.create(
            name="Appointment confirmed", slug="im_test_template", category=NotificationCategory.UTILITY
        )
        self.trigger = NotificationTrigger.objects.create(
            name="Appointment confirmed",
            slug="im_test_trigger",
            trigger_type=TriggerType.SIGNAL,
            template_slug=self.template.slug,
        )
        self.event = NotificationEvent.objects.create(
            template=self.template, trigger=self.trigger, title="Your appointment is confirmed"
        )

    def make_recipient(self, **kwargs):
        from django.contrib.contenttypes.models import ContentType

        data = {
            "event": self.event,
            # The generic target is irrelevant to dispatch, which sends to phone_number.
            "recipient_content_type": ContentType.objects.get_for_model(NotificationEvent),
            "recipient_object_id": self.event.pk,
            "phone_number": "+919876543210",
            "provider": "whatsapp",
        }
        data.update(kwargs)
        return NotificationRecipient.objects.create(**data)


class DispatchSuccessTests(DispatchTestBase):
    def test_a_successful_send_records_the_tracking_id_and_marks_it_sent(self):
        recipient = self.make_recipient()

        with patch(SEND, return_value=SentTemplate(tracking_id="wamid.1", parameters={"1": "Jane"})):
            dispatch_notification_recipient(recipient.pk)

        recipient.refresh_from_db()
        self.assertEqual(recipient.tracking_id, "wamid.1")
        self.assertEqual(recipient.latest_status, NotificationStatusState.SENT)
        self.assertEqual(recipient.status_events.get().state, NotificationStatusState.SENT)

    def test_the_resolved_parameters_are_kept_for_auditing_what_was_sent(self):
        recipient = self.make_recipient()

        with patch(SEND, return_value=SentTemplate(tracking_id="wamid.1", parameters={"1": "Jane Doe"})):
            dispatch_notification_recipient(recipient.pk)

        recipient.refresh_from_db()
        self.assertEqual(recipient.message_payload["sent_parameters"], {"1": "Jane Doe"})
        self.assertEqual(recipient.message_payload["template_slug"], self.template.slug)

    def test_a_recipient_that_already_has_a_status_is_not_sent_again(self):
        recipient = self.make_recipient(latest_status=NotificationStatusState.SENT)

        with patch(SEND) as mock_send:
            dispatch_notification_recipient(recipient.pk)

        mock_send.assert_not_called()

    def test_a_recipient_another_worker_has_claimed_is_left_alone(self):
        recipient = self.make_recipient(dispatch_started_at=timezone.now())

        with patch(SEND) as mock_send:
            dispatch_notification_recipient(recipient.pk)

        mock_send.assert_not_called()


class DispatchFailureTests(DispatchTestBase):
    def test_an_unconfigured_template_fails_immediately_rather_than_retrying(self):
        """Retrying cannot make an unapproved template exist; it only burns the budget."""
        recipient = self.make_recipient()

        with patch(SEND, side_effect=WhatsAppTemplateNotConfiguredError("no such template")):
            dispatch_notification_recipient(recipient.pk)

        recipient.refresh_from_db()
        self.assertEqual(recipient.latest_status, NotificationStatusState.FAILED)
        self.assertEqual(recipient.status_events.get().payload["error_type"], "WhatsAppTemplateNotConfiguredError")

    def test_a_bad_request_is_also_permanent(self):
        recipient = self.make_recipient()

        with patch(SEND, side_effect=WhatsAppBadRequestError("malformed")):
            dispatch_notification_recipient(recipient.pk)

        recipient.refresh_from_db()
        self.assertEqual(recipient.latest_status, NotificationStatusState.FAILED)

    def test_the_failure_payload_carries_the_error_and_a_traceback(self):
        recipient = self.make_recipient()

        with patch(SEND, side_effect=WhatsAppBadRequestError("malformed")):
            dispatch_notification_recipient(recipient.pk)

        payload = recipient.status_events.get().payload
        self.assertIn("malformed", payload["error"])
        self.assertIn("WhatsAppBadRequestError", payload["traceback"])

    def test_a_transient_error_retries_rather_than_failing(self):
        recipient = self.make_recipient()

        with (
            patch(SEND, side_effect=WhatsAppNetworkError("timeout")),
            patch("care_im_wrapper.tasks.dispatch_notification_recipient.retry") as mock_retry,
        ):
            mock_retry.side_effect = RuntimeError("retry called")
            with self.assertRaises(RuntimeError):
                dispatch_notification_recipient(recipient.pk)

        mock_retry.assert_called_once()
        recipient.refresh_from_db()
        self.assertIsNone(recipient.latest_status)

    def test_a_send_with_no_tracking_id_is_failed_because_delivery_cannot_be_tracked(self):
        recipient = self.make_recipient()

        with patch(SEND, return_value=SentTemplate(tracking_id=None)):
            dispatch_notification_recipient(recipient.pk)

        recipient.refresh_from_db()
        self.assertEqual(recipient.latest_status, NotificationStatusState.FAILED)
        self.assertEqual(recipient.status_events.get().payload["error_type"], "MissingTrackingId")


class DispatchPacingTests(DispatchTestBase):
    def test_a_paced_recipient_retries_instead_of_sending(self):
        recipient = self.make_recipient()

        with (
            patch("care_im_wrapper.tasks.is_outbound_rate_limited", return_value=True),
            patch(SEND) as mock_send,
            patch("care_im_wrapper.tasks.dispatch_notification_recipient.retry") as mock_retry,
        ):
            mock_retry.side_effect = RuntimeError("retry called")
            with self.assertRaises(RuntimeError):
                dispatch_notification_recipient(recipient.pk)

        mock_send.assert_not_called()

    def test_exhausting_retries_while_paced_releases_the_claim_for_the_sweep(self):
        from celery.exceptions import MaxRetriesExceededError

        recipient = self.make_recipient()

        with (
            patch("care_im_wrapper.tasks.is_outbound_rate_limited", return_value=True),
            patch(
                "care_im_wrapper.tasks.dispatch_notification_recipient.retry",
                side_effect=MaxRetriesExceededError,
            ),
        ):
            dispatch_notification_recipient(recipient.pk)

        recipient.refresh_from_db()
        self.assertIsNone(recipient.dispatch_started_at)
        self.assertIsNone(recipient.latest_status)


class ProcessStatusUpdateTests(DispatchTestBase):
    def setUp(self):
        super().setUp()
        self.recipient = self.make_recipient(tracking_id="wamid.1", latest_status=NotificationStatusState.SENT)

    def _payload(self, state):
        return {"tracking_id": "wamid.1", "state": state}

    def _run(self, state, tracking_id="wamid.1"):
        from care_im_wrapper.messaging.normalize import StatusUpdate

        update = StatusUpdate(tracking_id=tracking_id, state=state, raw_payload={"raw": True})
        with patch("care_im_wrapper.tasks.normalize_status_update", return_value=update):
            process_status_update({}, "whatsapp")

    def test_a_later_state_advances_the_cached_status(self):
        self._run(NotificationStatusState.DELIVERED)

        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.latest_status, NotificationStatusState.DELIVERED)

    def test_an_out_of_order_update_does_not_walk_the_status_backwards(self):
        """Meta does not guarantee webhook ordering; a late 'sent' must not undo 'read'."""
        self.recipient.latest_status = NotificationStatusState.READ
        self.recipient.save(update_fields=["latest_status"])

        self._run(NotificationStatusState.SENT)

        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.latest_status, NotificationStatusState.READ)

    def test_every_update_is_recorded_even_when_the_cache_does_not_move(self):
        self.recipient.latest_status = NotificationStatusState.READ
        self.recipient.save(update_fields=["latest_status"])

        self._run(NotificationStatusState.SENT)

        self.assertEqual(self.recipient.status_events.count(), 1)

    def test_an_unnormalizable_payload_is_dropped(self):
        with patch("care_im_wrapper.tasks.normalize_status_update", return_value=None):
            process_status_update({}, "whatsapp")

        self.assertEqual(NotificationStatus.objects.count(), 0)

    def test_an_update_for_an_unknown_tracking_id_is_dropped(self):
        self._run(NotificationStatusState.DELIVERED, tracking_id="wamid.unknown")

        self.assertEqual(NotificationStatus.objects.count(), 0)


class SyncNotificationTemplatesTests(TestCase):
    def test_every_template_capable_provider_is_synced(self):
        from unittest.mock import MagicMock

        client = MagicMock()
        with patch("care_im_wrapper.tasks.get_template_capable_providers", return_value=[("whatsapp", client)]):
            sync_notification_templates()

        client.sync_templates.assert_called_once()

    def test_one_providers_failure_does_not_stop_the_others(self):
        from unittest.mock import MagicMock

        broken, working = MagicMock(), MagicMock()
        broken.sync_templates.side_effect = RuntimeError("provider down")
        with patch(
            "care_im_wrapper.tasks.get_template_capable_providers",
            return_value=[("broken", broken), ("whatsapp", working)],
        ):
            sync_notification_templates()

        working.sync_templates.assert_called_once()


class DispatchSweepTests(DispatchTestBase):
    def test_an_unclaimed_undelivered_recipient_is_queued(self):
        recipient = self.make_recipient()

        with patch("care_im_wrapper.tasks.dispatch_notification_recipient.delay") as mock_delay:
            dispatch_pending_notification_recipients()

        mock_delay.assert_called_once_with(recipient.pk)

    def test_an_already_sent_recipient_is_left_alone(self):
        self.make_recipient(latest_status=NotificationStatusState.SENT)

        with patch("care_im_wrapper.tasks.dispatch_notification_recipient.delay") as mock_delay:
            dispatch_pending_notification_recipients()

        mock_delay.assert_not_called()

    def test_a_freshly_claimed_recipient_is_left_to_its_worker(self):
        self.make_recipient(dispatch_started_at=timezone.now())

        with patch("care_im_wrapper.tasks.dispatch_notification_recipient.delay") as mock_delay:
            dispatch_pending_notification_recipients()

        mock_delay.assert_not_called()

    def test_a_stale_claim_is_reclaimed_so_a_dead_workers_recipient_still_sends(self):
        from datetime import timedelta

        from care_im_wrapper.settings import plugin_settings

        stale = timezone.now() - timedelta(seconds=int(plugin_settings.DISPATCH_CLAIM_STALE_SECONDS) + 60)
        recipient = self.make_recipient(dispatch_started_at=stale)

        with patch("care_im_wrapper.tasks.dispatch_notification_recipient.delay") as mock_delay:
            dispatch_pending_notification_recipients()

        recipient.refresh_from_db()
        self.assertIsNone(recipient.dispatch_started_at)
        mock_delay.assert_called_once_with(recipient.pk)
