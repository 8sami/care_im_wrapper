from types import SimpleNamespace
from unittest.mock import patch

from care.emr.resources.encounter.constants import ClassChoices, StatusChoices
from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.encounters import fetch_encounters, fmt_facility_name
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.models import ConversationSession
from tests.utils import OverrideCache  # noqa: F401 # pyright: ignore


class FmtFacilityNameTests(CareAPITestBase):
    def test_encounter_with_facility_returns_facility_name(self):
        enc = SimpleNamespace(facility=SimpleNamespace(name="City Hospital"))
        self.assertEqual(fmt_facility_name(enc), "City Hospital")

    def test_encounter_with_none_facility_returns_unknown_facility(self):
        enc = SimpleNamespace(facility=None)
        self.assertEqual(fmt_facility_name(enc), "Unknown facility")

    def test_encounter_without_facility_attribute_returns_unknown_facility(self):
        enc = SimpleNamespace()
        self.assertEqual(fmt_facility_name(enc), "Unknown facility")


@OverrideCache
class FetchEncountersTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)

    def test_patient_actor_with_no_encounters_raises_no_data_error(self):
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)
        session = SimpleNamespace(active_patient_external_id=None)

        with self.assertRaises(NoDataError):
            fetch_encounters(actor, session)

    def test_patient_actor_returns_own_encounter_with_humanized_fields(self):
        self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.in_progress.value,
            encounter_class=ClassChoices.imp.value,
        )
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_encounters(actor, session)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.facility, self.facility.name)
        self.assertEqual(record.status, "In Progress")
        self.assertEqual(record.encounter_class, "Inpatient")

    def test_records_ordered_most_recent_first(self):
        older = self.create_encounter(patient=self.patient, facility=self.facility, organization=self.organization)
        newer = self.create_encounter(patient=self.patient, facility=self.facility, organization=self.organization)
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_encounters(actor, session)

        self.assertEqual(len(records), 2)
        self.assertGreaterEqual(newer.created_date, older.created_date)

    def test_results_are_capped_at_data_fetch_limit_of_ten(self):
        for _ in range(12):
            self.create_encounter(patient=self.patient, facility=self.facility, organization=self.organization)
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)
        session = SimpleNamespace(active_patient_external_id=None)

        records = fetch_encounters(actor, session)

        self.assertEqual(len(records), 10)

    def test_staff_actor_with_permission_returns_active_patient_encounters(self):
        self.create_encounter(
            patient=self.patient,
            facility=self.facility,
            organization=self.organization,
            status=StatusChoices.in_progress.value,
        )
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(self.patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=True):
            records = fetch_encounters(actor, session)

        self.assertEqual(len(records), 1)
