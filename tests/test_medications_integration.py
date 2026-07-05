from types import SimpleNamespace
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.medications import fetch_medications
from care_im_wrapper.models import ConversationSession
from tests.utils import OverrideCache  # noqa: F401 # pyright: ignore


@OverrideCache
class FetchMedicationsTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )

    def _create_medication_request(self, **kwargs):
        from care.emr.models.medication_request import MedicationRequest

        data = {
            "patient": self.patient,
            "encounter": self.encounter,
            "do_not_perform": False,
            "status": "active",
            "medication": {"display": "Test Medication"},
            "dosage_instruction": [],
            "note": None,
        }
        data.update(kwargs)
        return MedicationRequest.objects.create(**data)

    def _patient_actor(self):
        return Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)

    def test_no_medications_raises_no_data_error(self):
        session = SimpleNamespace(active_patient_external_id=None)

        with self.assertRaises(NoDataError):
            fetch_medications(self._patient_actor(), session)

    def test_returns_medication_with_humanized_status_and_name_and_note(self):
        self._create_medication_request(
            status="in_progress",
            medication={"display": "Paracetamol 500mg"},
            note="Take after food",
        )
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_medications(self._patient_actor(), session)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.name, "Paracetamol 500mg")
        self.assertEqual(record.status, "In Progress")
        self.assertEqual(record.note, "Take after food")
        self.assertIsNone(record.dosage)

    def test_entered_in_error_status_is_excluded(self):
        self._create_medication_request(status="active", medication={"display": "Kept Medication"})
        self._create_medication_request(status="entered_in_error", medication={"display": "Excluded Medication"})
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_medications(self._patient_actor(), session)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Kept Medication")

    def test_dosage_instruction_list_with_display_joins_with_pipe(self):
        self._create_medication_request(
            dosage_instruction=[{"display": "1 tablet twice daily"}, {"text": "After meals"}]
        )
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_medications(self._patient_actor(), session)

        self.assertEqual(records[0].dosage, "1 tablet twice daily | After meals")

    def test_dosage_instruction_falls_back_to_duration_when_no_display_or_text(self):
        self._create_medication_request(dosage_instruction=[{"timing": {"repeat": {"bounds_duration": "7 days"}}}])
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_medications(self._patient_actor(), session)

        self.assertEqual(records[0].dosage, "(Duration: 7 days)")

    def test_dosage_instruction_as_plain_string_is_used_directly(self):
        self._create_medication_request(dosage_instruction="Once daily before bed")
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_medications(self._patient_actor(), session)

        self.assertEqual(records[0].dosage, "Once daily before bed")

    def test_results_are_capped_at_data_fetch_limit_of_ten(self):
        for _ in range(12):
            self._create_medication_request()
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_medications(self._patient_actor(), session)

        self.assertEqual(len(records), 10)

    def test_staff_actor_with_permission_returns_active_patient_medications(self):
        self._create_medication_request(medication={"display": "Staff Visible Medication"})
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(self.patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=True):
            records = fetch_medications(actor, session)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Staff Visible Medication")
