import datetime
from types import SimpleNamespace
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.test import SimpleTestCase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.patient_summary import _format_dob_or_yob, fetch_summary
from care_im_wrapper.models import ConversationSession
from tests.utils import OverrideCache


class FormatDobOrYobTests(SimpleTestCase):
    def test_date_of_birth_present_formats_as_short_date(self):
        patient = SimpleNamespace(date_of_birth=datetime.date(1990, 6, 15), year_of_birth=None)
        self.assertEqual(_format_dob_or_yob(patient), "15 Jun 1990")

    def test_no_date_of_birth_falls_back_to_year_of_birth(self):
        patient = SimpleNamespace(date_of_birth=None, year_of_birth=1985)
        self.assertEqual(_format_dob_or_yob(patient), "Year of birth: 1985")

    def test_date_of_birth_takes_priority_over_year_of_birth(self):
        patient = SimpleNamespace(date_of_birth=datetime.date(1990, 6, 15), year_of_birth=1985)
        self.assertEqual(_format_dob_or_yob(patient), "15 Jun 1990")

    def test_neither_date_of_birth_nor_year_of_birth_returns_none(self):
        patient = SimpleNamespace(date_of_birth=None, year_of_birth=None)
        self.assertIsNone(_format_dob_or_yob(patient))

    def test_missing_attributes_entirely_returns_none(self):
        patient = SimpleNamespace()
        self.assertIsNone(_format_dob_or_yob(patient))


@OverrideCache
class FetchSummaryTests(CareAPITestBase):
    def test_patient_actor_returns_own_summary_with_humanized_fields_and_raw_phone(self):
        patient = self.create_patient(
            name="Jane Doe",
            date_of_birth=datetime.date(1990, 6, 15),
            year_of_birth=None,
            blood_group="A_positive",
            gender="female",
            phone_number="+919876543210",
        )
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=patient)
        session = SimpleNamespace(active_patient_external_id=None)

        summary = fetch_summary(actor, session)

        self.assertEqual(summary.name, "Jane Doe")
        self.assertEqual(summary.date_of_birth, "15 Jun 1990")
        self.assertEqual(summary.blood_group, "A Positive")
        self.assertEqual(summary.gender, "Female")
        # NOTE: unlike patient_lookup.search_patients, this phone number is NOT masked.
        self.assertEqual(summary.phone, "+919876543210")

    def test_staff_actor_with_permission_returns_active_patient_summary(self):
        patient = self.create_patient(
            name="John Roe",
            date_of_birth=None,
            year_of_birth=1988,
            blood_group="O_negative",
            gender="male",
            phone_number="+911111111111",
        )
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=True):
            summary = fetch_summary(actor, session)

        self.assertEqual(summary.name, "John Roe")
        self.assertEqual(summary.date_of_birth, "Year of birth: 1988")
        self.assertEqual(summary.blood_group, "O Negative")
        self.assertEqual(summary.gender, "Male")
        self.assertEqual(summary.phone, "+911111111111")
