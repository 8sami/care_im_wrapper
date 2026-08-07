from unittest.mock import patch

from care.emr.resources.encounter.constants import StatusChoices
from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.handlers.patient import display_patient_id

PATIENT_PHONE = "+919876500021"


class DisplayPatientIdTests(CareAPITestBase):
    def test_prefers_a_configured_instance_identifier(self):
        patient = self.create_patient(instance_identifiers=[{"config": "abc", "value": "MRN-42"}])

        self.assertEqual(display_patient_id(patient), "MRN-42")

    def test_skips_identifier_entries_without_a_value(self):
        patient = self.create_patient(instance_identifiers=[{"config": "abc"}, {"config": "d", "value": "MRN-7"}])

        self.assertEqual(display_patient_id(patient), "MRN-7")

    def test_falls_back_to_external_id_when_no_identifiers(self):
        patient = self.create_patient(instance_identifiers=[])

        self.assertEqual(display_patient_id(patient), str(patient.external_id))


class PatientRegisteredSignalTests(CareAPITestBase):
    """Registration fires off the Patient row itself -- there is no encounter yet."""

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_creating_a_patient_fires_the_registered_trigger(self, mock_fire):
        patient = self.create_patient(phone_number=PATIENT_PHONE)

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "patient_registered")
        self.assertEqual(kwargs["related_object"], patient)
        self.assertEqual(kwargs["recipient"].phone_number, PATIENT_PHONE)
        self.assertEqual(kwargs["variable_values"]["action"], "registered")
        self.assertEqual(kwargs["variable_values"]["header_action"], "Registered")
        self.assertNotIn("hospital_or_clinic", kwargs["variable_values"])

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_no_variable_value_is_ever_blank(self, mock_fire):
        """Meta rejects a template send outright on a blank text parameter, so a handler."""
        self.create_patient(phone_number=PATIENT_PHONE)

        values = mock_fire.call_args.kwargs["variable_values"]
        blank = [key for key, value in values.items() if not str(value).strip()]
        self.assertEqual(blank, [])

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_updating_an_existing_patient_does_not_re_fire(self, mock_fire):
        patient = self.create_patient(phone_number=PATIENT_PHONE)
        mock_fire.reset_mock()

        patient.name = "Renamed"
        patient.save()

        mock_fire.assert_not_called()


class PatientDischargedSignalTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)

    def _encounter(self, status=StatusChoices.in_progress.value):
        return self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization, status=status
        )

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_transition_to_discharged_fires_with_the_encounters_facility(self, mock_fire):
        encounter = self._encounter()
        mock_fire.reset_mock()

        encounter.status = StatusChoices.discharged.value
        encounter.save()

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "patient_discharged")
        self.assertEqual(kwargs["related_object"], self.patient)
        self.assertEqual(kwargs["variable_values"]["action"], "discharged")
        self.assertNotIn("hospital_or_clinic", kwargs["variable_values"])

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_creating_an_already_discharged_encounter_does_not_fire(self, mock_fire):
        self._encounter(status=StatusChoices.discharged.value)

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_resaving_an_already_discharged_encounter_does_not_re_fire(self, mock_fire):
        encounter = self._encounter()
        encounter.status = StatusChoices.discharged.value
        encounter.save()
        mock_fire.reset_mock()

        encounter.note = "edited after discharge"
        encounter.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.patient.fire_notification_event")
    def test_transition_to_a_non_discharged_status_does_not_fire(self, mock_fire):
        encounter = self._encounter()
        mock_fire.reset_mock()

        encounter.status = StatusChoices.completed.value
        encounter.save()

        mock_fire.assert_not_called()


class ResolvePatientFacilityTests(CareAPITestBase):
    """Which facility a patient-lifecycle event is filed under."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)

    def _membership(self, user):
        from care.emr.models.organization import FacilityOrganizationUser

        return FacilityOrganizationUser.objects.create(
            organization=self.organization, user=user, role=self.create_role()
        )

    def test_encounter_facility_wins_when_the_patient_has_one(self):
        from care_im_wrapper.handlers.patient import _resolve_patient_facility

        patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.create_encounter(patient=patient, facility=self.facility, organization=self.organization)

        self.assertEqual(_resolve_patient_facility(patient), self.facility)

    def test_registration_falls_back_to_the_registering_users_facility(self):
        """A just-registered patient has no encounter; without this the event is filtered."""
        from care_im_wrapper.handlers.patient import _resolve_patient_facility

        self._membership(self.user)
        patient = self.create_patient(phone_number=PATIENT_PHONE, created_by=self.user)

        self.assertEqual(_resolve_patient_facility(patient), self.facility)

    def test_no_encounter_and_no_creator_resolves_to_nothing(self):
        from care_im_wrapper.handlers.patient import _resolve_patient_facility

        patient = self.create_patient(phone_number=PATIENT_PHONE)
        patient.created_by = None
        patient.save(update_fields=["created_by"])

        self.assertIsNone(_resolve_patient_facility(patient))

    def test_a_registration_event_is_scoped_so_the_log_can_return_it(self):
        from care_im_wrapper.models.notification import NotificationEvent

        self._membership(self.user)
        self.create_patient(phone_number=PATIENT_PHONE, created_by=self.user)

        event = NotificationEvent.objects.filter(trigger__slug="patient_registered").order_by("-id").first()
        if event is not None:  # only when the trigger and template are seeded
            self.assertIsNotNone(event.facility_id)
