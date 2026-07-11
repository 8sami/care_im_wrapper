from types import SimpleNamespace
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.test import SimpleTestCase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.procedures import _extract_service_name, fetch_procedures
from care_im_wrapper.models import ConversationSession
from tests.utils import OverrideCache  # noqa: F401 # pyright: ignore


class ExtractServiceNameTests(SimpleTestCase):
    def test_code_dict_with_display_returns_display(self):
        sr = SimpleNamespace(code={"display": "Blood Test", "text": "Blood Test (CBC)"})
        self.assertEqual(_extract_service_name(sr), "Blood Test")

    def test_code_dict_with_text_only_returns_text(self):
        sr = SimpleNamespace(code={"text": "X-Ray Chest"})
        self.assertEqual(_extract_service_name(sr), "X-Ray Chest")

    def test_code_dict_without_display_or_text_returns_unspecified(self):
        sr = SimpleNamespace(code={"system": "http://snomed.info"})
        self.assertEqual(_extract_service_name(sr), "Unspecified procedure")

    def test_code_as_plain_string_returns_string(self):
        sr = SimpleNamespace(code="MRI Brain")
        self.assertEqual(_extract_service_name(sr), "MRI Brain")

    def test_code_none_returns_unspecified(self):
        sr = SimpleNamespace(code=None)
        self.assertEqual(_extract_service_name(sr), "Unspecified procedure")

    def test_missing_code_attribute_returns_unspecified(self):
        sr = SimpleNamespace()
        self.assertEqual(_extract_service_name(sr), "Unspecified procedure")


@OverrideCache
class FetchProceduresTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )

    def _patient_actor(self):
        return Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)

    def test_no_service_requests_raises_no_data_error(self):
        session = SimpleNamespace(active_patient_external_id=None)

        with self.assertRaises(NoDataError):
            fetch_procedures(self._patient_actor(), session)

    def test_returns_own_procedure_with_humanized_status_and_name(self):
        self.create_service_request(
            patient=self.patient,
            facility=self.facility,
            encounter=self.encounter,
            status="in_progress",
            code={"display": "Blood Test"},
        )
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_procedures(self._patient_actor(), session)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.name, "Blood Test")
        self.assertEqual(record.status, "In Progress")

    def test_results_are_capped_at_data_fetch_limit_of_ten(self):
        for _ in range(12):
            self.create_service_request(patient=self.patient, facility=self.facility, encounter=self.encounter)
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_procedures(self._patient_actor(), session)

        self.assertEqual(len(records), 10)

    def test_staff_actor_with_permission_returns_active_patient_procedures(self):
        self.create_service_request(
            patient=self.patient,
            facility=self.facility,
            encounter=self.encounter,
            code={"display": "Staff Visible Procedure"},
        )
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(self.patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=True):
            records = fetch_procedures(actor, session)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Staff Visible Procedure")
