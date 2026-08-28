from datetime import timedelta

from care.utils.tests.base import CareAPITestBase
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationEvent,
    NotificationRecipient,
    NotificationTemplate,
    NotificationTrigger,
    TemplateApprovalStatus,
)

PATIENT_PHONE = "+919876500042"

# The template every appointment status trigger renders. Migrations seed the triggers, but
# templates are synced from Meta, so the test database has none until one is created here.
APPOINTMENT_TEMPLATE_SLUG = "appointment_update"


class BookingNotificationTestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(first_name="Ada", last_name="Lovelace")
        self.patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.facility = self.create_facility(user=self.user)
        self.resource = self._create_resource()
        self.template = self._create_appointment_template()

    def _create_resource(self):
        from care.emr.models.scheduling.schedule import SchedulableResource

        return SchedulableResource.objects.create(facility=self.facility, resource_type="practitioner", user=self.user)

    def _create_appointment_template(self):
        return NotificationTemplate.objects.create(
            name="Appointment Update",
            slug=APPOINTMENT_TEMPLATE_SLUG,
            category=NotificationCategory.UTILITY,
            approval_status=TemplateApprovalStatus.ACTIVE,
            is_active=True,
            language_code="en",
        )

    def _create_booking(self, status="pending"):
        """Bookings start in a status that notifies nothing, so each test's transition is the
        only event in the table."""
        from care.emr.models.scheduling.booking import TokenBooking, TokenSlot

        start = timezone.now() + timedelta(days=1)
        slot = TokenSlot.objects.create(
            resource=self.resource, start_datetime=start, end_datetime=start + timedelta(minutes=30)
        )
        return TokenBooking.objects.create(token_slot=slot, patient=self.patient, status=status)

    def _transition(self, booking, status):
        booking.status = status
        booking.save()
        return booking

    def _only_event(self):
        events = list(NotificationEvent.objects.select_related("trigger").all())
        self.assertEqual(len(events), 1, f"expected exactly one event, got {[e.title for e in events]}")
        return events[0]


class BookingStatusTriggerTests(BookingNotificationTestBase):
    """Each status the maintainer asked for fires its own trigger, rendering the shared
    appointment_update template with the status word that template's body reads with."""

    def test_no_show_fires_its_trigger(self):
        booking = self._create_booking()

        self._transition(booking, "noshow")

        event = self._only_event()
        self.assertEqual(event.trigger.slug, "appointment_no_show")
        self.assertEqual(event.variable_values["status"], "marked as a no-show")
        self.assertEqual(event.title, f"Appointment marked no-show — {booking.external_id}")

    def test_checked_in_fires_its_trigger(self):
        booking = self._create_booking()

        self._transition(booking, "checked_in")

        event = self._only_event()
        self.assertEqual(event.trigger.slug, "appointment_checked_in")
        self.assertEqual(event.variable_values["status"], "checked in")
        self.assertEqual(event.title, f"Appointment checked in — {booking.external_id}")

    def test_in_consultation_fires_its_trigger(self):
        booking = self._create_booking()

        self._transition(booking, "in_consultation")

        event = self._only_event()
        self.assertEqual(event.trigger.slug, "appointment_in_consultation")
        self.assertEqual(event.variable_values["status"], "marked as in consultation")
        self.assertEqual(event.title, f"Appointment in consultation — {booking.external_id}")

    def test_fulfilled_fires_its_trigger(self):
        booking = self._create_booking()

        self._transition(booking, "fulfilled")

        event = self._only_event()
        self.assertEqual(event.trigger.slug, "appointment_fulfilled")
        self.assertEqual(event.variable_values["status"], "fulfilled")
        self.assertEqual(event.title, f"Appointment fulfilled — {booking.external_id}")

    def test_every_new_status_renders_the_shared_appointment_template(self):
        for status in ("noshow", "checked_in", "in_consultation", "fulfilled"):
            with self.subTest(status=status):
                NotificationEvent.objects.all().delete()
                self._transition(self._create_booking(), status)

                self.assertEqual(self._only_event().template.slug, APPOINTMENT_TEMPLATE_SLUG)


class BookingNotificationEventShapeTests(BookingNotificationTestBase):
    def test_event_carries_the_booking_and_its_facility(self):
        booking = self._transition(self._create_booking(), "fulfilled")

        event = self._only_event()
        self.assertEqual(event.related_object_content_type, ContentType.objects.get_for_model(type(booking)))
        self.assertEqual(event.related_object_id, booking.pk)
        self.assertEqual(event.facility_id, self.facility.id)

    def test_recipient_is_the_patient_on_their_own_number(self):
        booking = self._transition(self._create_booking(), "checked_in")

        recipient = NotificationRecipient.objects.get(event=self._only_event())
        self.assertEqual(recipient.recipient_object_id, booking.patient.pk)
        self.assertEqual(recipient.phone_number, PATIENT_PHONE)

    def test_doctor_name_is_merged_over_the_triggers_defaults(self):
        self._transition(self._create_booking(), "in_consultation")

        self.assertEqual(self._only_event().variable_values["doctor_name"], self.user.full_name)

    def test_patient_without_a_phone_number_is_skipped(self):
        self.patient.phone_number = ""
        self.patient.save()

        self._transition(self._create_booking(), "fulfilled")

        self.assertFalse(NotificationEvent.objects.exists())


class BookingNotificationGuardTests(BookingNotificationTestBase):
    def test_resaving_a_booking_already_in_the_status_does_not_refire(self):
        booking = self._transition(self._create_booking(), "fulfilled")
        self.assertEqual(NotificationEvent.objects.count(), 1)

        booking.save()

        self.assertEqual(NotificationEvent.objects.count(), 1)

    def test_status_without_a_trigger_fires_nothing(self):
        """`arrived` is a real BookingStatusChoices value the maintainer did not ask for."""
        self._transition(self._create_booking(), "arrived")

        self.assertFalse(NotificationEvent.objects.exists())

    def test_booking_created_directly_in_a_notifying_status_does_not_fire(self):
        """Only the transition notifies; `booked` remains the sole status that fires on create."""
        self._create_booking(status="fulfilled")

        self.assertFalse(NotificationEvent.objects.exists())

    def test_inactive_trigger_is_not_fired(self):
        NotificationTrigger.objects.filter(slug="appointment_no_show").update(is_active=False)

        self._transition(self._create_booking(), "noshow")

        self.assertFalse(NotificationEvent.objects.exists())

    def test_booking_created_as_booked_still_fires_the_confirmation(self):
        booking = self._create_booking(status="booked")

        event = self._only_event()
        self.assertEqual(event.trigger.slug, "appointment_confirmed")
        self.assertEqual(event.title, f"Appointment confirmed — {booking.external_id}")
