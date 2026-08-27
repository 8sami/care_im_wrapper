from types import SimpleNamespace
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.core.cache import cache
from django.test import SimpleTestCase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.exceptions import MissingContextError, NoDataError
from care_im_wrapper.data.procedures import _extract_service_name, fetch_procedures
from care_im_wrapper.models import ConversationSession


class ExtractServiceNameTests(SimpleTestCase):
    def test_title_wins_over_the_snomed_coding(self):
        sr = SimpleNamespace(
            title="CBC bloood",
            activity_definition=None,
            code={"display": "Percutaneous ligation of left atrial appendage"},
        )
        self.assertEqual(_extract_service_name(sr), "CBC bloood")

    def test_falls_back_to_the_activity_definition_title(self):
        sr = SimpleNamespace(
            title="",
            activity_definition=SimpleNamespace(title="CBC bloood"),
            code={"display": "Percutaneous ligation of left atrial appendage"},
        )
        self.assertEqual(_extract_service_name(sr), "CBC bloood")

    def test_falls_back_to_the_coding_when_nothing_names_it(self):
        sr = SimpleNamespace(title=None, activity_definition=None, code={"display": "Blood Test"})
        self.assertEqual(_extract_service_name(sr), "Blood Test")

    def test_unnamed_and_uncoded_request_still_renders(self):
        sr = SimpleNamespace(title=None, activity_definition=None, code=None)
        self.assertEqual(_extract_service_name(sr), "Unspecified procedure")


class FetchProceduresTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )

    def _patient_actor(self):
        return Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)

    def _session(self, patient_external_id=None, encounter=None):
        """A stand-in for the session fields the fetcher reads. The encounter defaults to
        the one setUp created -- procedures are encounter-scoped, as care_fe's tab is."""
        target = self.encounter if encounter is None else encounter
        return SimpleNamespace(
            active_patient_external_id=patient_external_id,
            active_encounter_external_id=str(target.external_id) if target else "",
            active_prescription_external_id="",
        )

    def test_no_service_requests_raises_no_data_error(self):
        session = self._session()

        with self.assertRaises(NoDataError):
            fetch_procedures(self._patient_actor(), session)

    def test_returns_own_procedure_with_humanized_status_and_name(self):
        self.create_service_request(
            patient=self.patient,
            facility=self.facility,
            encounter=self.encounter,
            status="in_progress",
            title="Blood Test",
        )
        session = self._session()

        records = fetch_procedures(self._patient_actor(), session)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.name, "Blood Test")
        self.assertEqual(record.status, "In Progress")

    def test_results_are_capped_at_data_fetch_limit_of_ten(self):
        for _ in range(12):
            self.create_service_request(patient=self.patient, facility=self.facility, encounter=self.encounter)
        session = self._session()

        records = fetch_procedures(self._patient_actor(), session)

        self.assertEqual(len(records), 10)

    def test_staff_actor_with_permission_returns_active_patient_procedures(self):
        self.create_service_request(
            patient=self.patient,
            facility=self.facility,
            encounter=self.encounter,
            title="Staff Visible Procedure",
        )
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        session = self._session(patient_external_id=str(self.patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=True):
            records = fetch_procedures(actor, session)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Staff Visible Procedure")

    def test_another_encounters_procedures_are_not_returned(self):
        other_encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )
        self.create_service_request(
            patient=self.patient, facility=self.facility, encounter=self.encounter, title="This visit"
        )
        self.create_service_request(
            patient=self.patient, facility=self.facility, encounter=other_encounter, title="Other visit"
        )

        records = fetch_procedures(self._patient_actor(), self._session())

        self.assertEqual([r.name for r in records], ["This visit"])

    def test_no_encounter_selected_raises_missing_context(self):
        session = SimpleNamespace(
            active_patient_external_id=None,
            active_encounter_external_id="",
            active_prescription_external_id="",
        )

        with self.assertRaises(MissingContextError):
            fetch_procedures(self._patient_actor(), session)

    def test_encounter_belonging_to_another_patient_is_not_reachable(self):
        """A forged external_id must not cross the patient boundary."""
        other_patient = self.create_patient()
        other_encounter = self.create_encounter(
            patient=other_patient, facility=self.facility, organization=self.organization
        )
        self.create_service_request(
            patient=other_patient, facility=self.facility, encounter=other_encounter, code={"display": "Not yours"}
        )
        session = self._session(encounter=other_encounter)

        with self.assertRaises(MissingContextError):
            fetch_procedures(self._patient_actor(), session)
