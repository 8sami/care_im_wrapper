from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.data.exceptions import NoDataError, PermissionDeniedError
from care_im_wrapper.data.patient_lookup import search_patients
from care_im_wrapper.models import ConversationSession


class SearchPatientsTests(CareAPITestBase):
    def test_non_staff_actor_raises_permission_denied_without_checking_authorization(self):
        patient = self.create_patient()
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=patient)

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call") as mock_call:
            with self.assertRaises(PermissionDeniedError):
                search_patients(actor, "Jane")

        mock_call.assert_not_called()

    def test_staff_actor_without_permission_raises_permission_denied(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", return_value=False) as mock_call:
            with self.assertRaises(PermissionDeniedError):
                search_patients(actor, "Jane")

        mock_call.assert_called_once_with("can_create_patient", staff_user)

    def test_no_matching_patients_raises_no_data_error(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", return_value=True):
            with self.assertRaises(NoDataError):
                search_patients(actor, "NoSuchPatientNameAtAll")

    def test_digit_query_searches_by_phone_number(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        patient = self.create_patient(phone_number="+919876543210", name="Jane Doe")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", return_value=True):
            results = search_patients(actor, "9876543210")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], patient.id)
        self.assertEqual(results[0]["external_id"], str(patient.external_id))
        self.assertEqual(results[0]["name"], "Jane Doe")
        self.assertEqual(results[0]["phone_number"], mask_phone_number("+919876543210"))

    def test_plus_prefixed_query_searches_by_phone_number(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        self.create_patient(phone_number="+919876543210", name="Jane Doe")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", return_value=True):
            results = search_patients(actor, "+919876543210")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Jane Doe")

    def test_non_digit_non_plus_query_searches_by_name(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        patient = self.create_patient(phone_number="+919876543210", name="Jane Doe")
        self.create_patient(phone_number="+911111111111", name="Someone Else")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", return_value=True):
            results = search_patients(actor, "Jane")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], patient.id)

    def test_results_are_capped_at_data_fetch_limit_of_ten(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        for i in range(12):
            self.create_patient(phone_number=f"+9198765432{i:02d}", name=f"Common Name {i}")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", return_value=True):
            results = search_patients(actor, "Common Name")

        self.assertEqual(len(results), 10)
