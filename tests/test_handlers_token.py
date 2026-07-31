from datetime import timedelta
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from care_im_wrapper.handlers.token import (
    count_tokens_ahead,
    describe_service,
    estimate_wait,
    humanize_wait,
    scheduled_start,
)

PATIENT_PHONE = "+919876500041"


class HumanizeWaitTests(SimpleTestCase):
    def test_zero_reads_as_under_a_minute(self):
        self.assertEqual(humanize_wait(0), "under a minute")

    def test_negative_is_treated_as_zero(self):
        self.assertEqual(humanize_wait(-5), "under a minute")

    def test_singular_minute(self):
        self.assertEqual(humanize_wait(1), "1 minute")

    def test_plural_minutes(self):
        self.assertEqual(humanize_wait(45), "45 minutes")

    def test_exact_hour_omits_minutes(self):
        self.assertEqual(humanize_wait(60), "1 hour")

    def test_hours_and_minutes(self):
        self.assertEqual(humanize_wait(135), "2 hours 15 minutes")

    def test_hours_with_one_trailing_minute(self):
        self.assertEqual(humanize_wait(61), "1 hour 1 minute")

    def test_exact_day_omits_hours(self):
        self.assertEqual(humanize_wait(24 * 60), "1 day")

    def test_days_and_hours(self):
        self.assertEqual(humanize_wait(3 * 24 * 60 + 4 * 60), "3 days 4 hours")

    def test_days_drop_minutes_rather_than_reading_as_75_hours(self):
        """A three-day wait used to render as "75 hours 6 minutes"."""
        self.assertEqual(humanize_wait(75 * 60 + 6), "3 days 3 hours")

    def test_just_under_a_day_still_reads_in_hours(self):
        self.assertEqual(humanize_wait(23 * 60 + 59), "23 hours 59 minutes")


class TokenQueueTestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(first_name="Ada", last_name="Lovelace")
        self.patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.facility = self.create_facility(user=self.user)
        self.resource = self._create_resource()
        self.queue = self._create_queue()
        self.category = self._create_category()

    def _create_resource(self, resource_type="practitioner", **kwargs):
        from care.emr.models.scheduling.schedule import SchedulableResource

        data = {"facility": self.facility, "resource_type": resource_type, "user": self.user}
        data.update(kwargs)
        return SchedulableResource.objects.create(**data)

    def _create_queue(self):
        from care.emr.models.scheduling.token import TokenQueue

        return TokenQueue.objects.create(
            facility=self.facility, resource=self.resource, name="OP Queue", date=timezone.localdate()
        )

    def _create_category(self):
        from care.emr.models.scheduling.token import TokenCategory

        return TokenCategory.objects.create(
            facility=self.facility, resource_type="practitioner", name="General", shorthand="G"
        )

    def _create_booking(self, *, starts_in, status="booked"):
        from care.emr.models.scheduling.booking import TokenBooking, TokenSlot

        start = timezone.now() + starts_in
        slot = TokenSlot.objects.create(
            resource=self.resource, start_datetime=start, end_datetime=start + timedelta(minutes=30)
        )
        return TokenBooking.objects.create(token_slot=slot, patient=self.patient, status=status)

    def _create_token(self, number, status="CREATED", patient=None, queue=None, booking=None):
        from care.emr.models.scheduling.token import Token

        return Token.objects.create(
            facility=self.facility,
            patient=self.patient if patient is None else patient,
            queue=self.queue if queue is None else queue,
            category=self.category,
            number=number,
            status=status,
            booking=booking,
        )


class CountTokensAheadTests(TokenQueueTestBase):
    def test_counts_only_lower_numbered_pending_tokens(self):
        self._create_token(1)
        self._create_token(2, status="IN_PROGRESS")
        token = self._create_token(5)

        self.assertEqual(count_tokens_ahead(token), 2)

    def test_ignores_finished_and_cancelled_tokens(self):
        self._create_token(1, status="FULFILLED")
        self._create_token(2, status="CANCELLED")
        self._create_token(3, status="ENTERED_IN_ERROR")
        token = self._create_token(5)

        self.assertEqual(count_tokens_ahead(token), 0)

    def test_ignores_higher_numbered_tokens(self):
        token = self._create_token(1)
        self._create_token(9)

        self.assertEqual(count_tokens_ahead(token), 0)

    def test_ignores_tokens_in_another_queue(self):
        other_queue = self._create_queue()
        self._create_token(1, queue=other_queue)
        token = self._create_token(5)

        self.assertEqual(count_tokens_ahead(token), 0)


class EstimateWaitTests(TokenQueueTestBase):
    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    def test_multiplies_tokens_ahead_by_the_configured_allowance(self):
        self._create_token(1)
        self._create_token(2)
        token = self._create_token(3)

        self.assertEqual(estimate_wait(token), "10 minutes")

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    def test_first_in_queue_waits_under_a_minute(self):
        token = self._create_token(1)

        self.assertEqual(estimate_wait(token), "under a minute")

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    def test_booked_token_counts_down_to_its_slot_not_the_queue(self):
        """A token for a slot hours away has nobody ahead of it in the queue."""
        token = self._create_token(1, booking=self._create_booking(starts_in=timedelta(hours=4, minutes=20)))

        self.assertEqual(estimate_wait(token), "4 hours 20 minutes")

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    def test_booked_token_days_out_reads_in_days(self):
        token = self._create_token(1, booking=self._create_booking(starts_in=timedelta(days=3, hours=3)))

        self.assertEqual(estimate_wait(token), "3 days 3 hours")

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    def test_booked_token_whose_slot_already_started_falls_back_to_the_queue(self):
        """Late arrival: the appointed time has passed, so the patient really is queuing."""
        self._create_token(1)
        self._create_token(2)
        token = self._create_token(3, booking=self._create_booking(starts_in=-timedelta(minutes=30)))

        self.assertEqual(estimate_wait(token), "10 minutes")

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    def test_walk_in_token_still_uses_queue_position(self):
        self._create_token(1)
        token = self._create_token(2)

        self.assertIsNone(scheduled_start(token))
        self.assertEqual(estimate_wait(token), "5 minutes")


class DescribeServiceTests(TokenQueueTestBase):
    def test_practitioner_resource_names_the_practitioner(self):
        token = self._create_token(1)

        self.assertEqual(describe_service(token), self.user.full_name)

    def test_healthcare_service_resource_names_the_service(self):
        from care.emr.models.healthcare_service import HealthcareService

        service = HealthcareService.objects.create(facility=self.facility, name="OP")
        resource = self._create_resource(resource_type="healthcare_service", user=None, healthcare_service=service)
        queue = self._create_queue()
        queue.resource = resource
        queue.save()
        token = self._create_token(1, queue=queue)

        self.assertEqual(describe_service(token), "OP")

    def test_falls_back_to_the_queue_name_when_the_resource_names_nothing(self):
        resource = self._create_resource(user=None)
        queue = self._create_queue()
        queue.resource = resource
        queue.save()
        token = self._create_token(1, queue=queue)

        self.assertEqual(describe_service(token), queue.name)


class TokenIssuedSignalTests(TokenQueueTestBase):
    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"WAIT_TIME_MINUTES_PER_TOKEN": 5}})
    @patch("care_im_wrapper.handlers.token.fire_notification_event")
    def test_issuing_a_token_fires_with_the_estimate(self, mock_fire):
        self._create_token(1)
        self._create_token(2)
        mock_fire.reset_mock()

        token = self._create_token(3)

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "wait_time_update")
        self.assertEqual(kwargs["related_object"], token)
        self.assertEqual(kwargs["recipient"].phone_number, PATIENT_PHONE)
        self.assertEqual(kwargs["variable_values"]["event"], "token #3")
        self.assertEqual(kwargs["variable_values"]["waiting_time"], "10 minutes")

    @patch("care_im_wrapper.handlers.token.fire_notification_event")
    def test_updating_an_existing_token_does_not_re_fire(self, mock_fire):
        token = self._create_token(1)
        mock_fire.reset_mock()

        token.status = "IN_PROGRESS"
        token.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.token.fire_notification_event")
    def test_token_without_a_patient_is_skipped(self, mock_fire):
        from care.emr.models.scheduling.token import Token

        Token.objects.create(
            facility=self.facility,
            patient=None,
            queue=self.queue,
            category=self.category,
            number=1,
            status="CREATED",
        )

        mock_fire.assert_not_called()
