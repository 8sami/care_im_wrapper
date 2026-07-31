from datetime import timedelta
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.utils import timezone

from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationEvent,
    NotificationTemplate,
    NotificationTrigger,
    TemplateApprovalStatus,
)
from care_im_wrapper.tasks import send_appointment_reminders

PATIENT_PHONE = "+919876500051"
CHANNEL = "whatsapp"


class SendAppointmentRemindersTests(CareAPITestBase):
    """The reminder is time-driven; an existing event is what stops a re-send."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(first_name="Ada", last_name="Lovelace")
        self.patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.facility = self.create_facility(user=self.user)
        self.resource = self._create_resource()

    def _create_resource(self):
        from care.emr.models.scheduling.schedule import SchedulableResource

        return SchedulableResource.objects.create(facility=self.facility, resource_type="practitioner", user=self.user)

    def _create_booking(self, *, starts_in, status="booked"):
        from care.emr.models.scheduling.booking import TokenBooking, TokenSlot

        start = timezone.now() + starts_in
        slot = TokenSlot.objects.create(
            resource=self.resource, start_datetime=start, end_datetime=start + timedelta(minutes=30)
        )
        return TokenBooking.objects.create(token_slot=slot, patient=self.patient, status=status)

    @patch("care_im_wrapper.tasks.fire_notification_event")
    def test_booking_inside_the_window_is_reminded(self, mock_fire):
        booking = self._create_booking(starts_in=timedelta(hours=3))

        send_appointment_reminders()

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "appointment_reminder")
        self.assertEqual(kwargs["related_object"], booking)
        self.assertEqual(kwargs["recipient"].phone_number, PATIENT_PHONE)
        self.assertEqual(
            kwargs["variable_values"],
            {
                "event": "appointment",
                "event_header": "appointment",
                "doctor_name": "Ada Lovelace",
            },
        )

    @patch("care_im_wrapper.tasks.fire_notification_event")
    def test_booking_beyond_the_window_is_left_alone(self, mock_fire):
        self._create_booking(starts_in=timedelta(days=3))

        send_appointment_reminders()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.tasks.fire_notification_event")
    def test_booking_already_started_is_not_reminded(self, mock_fire):
        self._create_booking(starts_in=timedelta(hours=-1))

        send_appointment_reminders()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.tasks.fire_notification_event")
    def test_cancelled_booking_is_not_reminded(self, mock_fire):
        self._create_booking(starts_in=timedelta(hours=3), status="cancelled")

        send_appointment_reminders()

        mock_fire.assert_not_called()

    @override_settings(PLUGIN_CONFIGS={"care_im_wrapper": {"APPOINTMENT_REMINDER_LEAD_SECONDS": 3600}})
    @patch("care_im_wrapper.tasks.fire_notification_event")
    def test_lead_window_is_configurable(self, mock_fire):
        self._create_booking(starts_in=timedelta(hours=3))

        send_appointment_reminders()

        mock_fire.assert_not_called()


class AppointmentReminderDedupTests(CareAPITestBase):
    """Dedup uses a real NotificationEvent, since that row is what suppresses a re-send."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.facility = self.create_facility(user=self.user)
        self.trigger = NotificationTrigger.objects.get(slug="appointment_reminder")
        self.template = NotificationTemplate.objects.create(
            name="event_reminder",
            slug="event_reminder",
            provider=CHANNEL,
            category=NotificationCategory.UTILITY,
            approval_status=TemplateApprovalStatus.ACTIVE,
            is_active=True,
        )
        self.resource = self._create_resource()
        self.booking = self._create_booking()

    def _create_resource(self):
        from care.emr.models.scheduling.schedule import SchedulableResource

        return SchedulableResource.objects.create(facility=self.facility, resource_type="practitioner", user=self.user)

    def _create_booking(self):
        from care.emr.models.scheduling.booking import TokenBooking, TokenSlot

        start = timezone.now() + timedelta(hours=3)
        slot = TokenSlot.objects.create(
            resource=self.resource, start_datetime=start, end_datetime=start + timedelta(minutes=30)
        )
        return TokenBooking.objects.create(token_slot=slot, patient=self.patient, status="booked")

    def _reminder_events(self):
        from care.emr.models.scheduling.booking import TokenBooking

        return NotificationEvent.objects.filter(
            trigger=self.trigger,
            related_object_content_type=ContentType.objects.get_for_model(TokenBooking),
            related_object_id=self.booking.pk,
        )

    def test_running_the_sweep_twice_reminds_only_once(self):
        send_appointment_reminders()
        self.assertEqual(self._reminder_events().count(), 1)

        send_appointment_reminders()

        self.assertEqual(self._reminder_events().count(), 1)

    def test_a_second_booking_is_still_reminded_after_the_first(self):
        send_appointment_reminders()
        second_booking = self._create_booking()

        send_appointment_reminders()

        from care.emr.models.scheduling.booking import TokenBooking

        self.assertEqual(
            NotificationEvent.objects.filter(
                trigger=self.trigger,
                related_object_content_type=ContentType.objects.get_for_model(TokenBooking),
                related_object_id=second_booking.pk,
            ).count(),
            1,
        )
