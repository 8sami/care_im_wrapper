from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.data.exceptions import InvalidQueryError, NoDataError, PermissionDeniedError
from care_im_wrapper.data.patient_lookup import search_patients
from care_im_wrapper.models import ConversationSession


def _identity_filter(_name, qs, _user):
    """Stand-in for get_filtered_patients that grants access to every match."""
    return qs


def _empty_filter(_name, qs, _user):
    """Stand-in for get_filtered_patients that grants access to nothing."""
    return qs.none()


class SearchPatientsTests(CareAPITestBase):
    def test_non_staff_actor_raises_permission_denied_without_checking_authorization(self):
        patient = self.create_patient()
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=patient)

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call") as mock_call:
            with self.assertRaises(PermissionDeniedError):
                search_patients(actor, "Jane")

        mock_call.assert_not_called()

    def test_short_query_raises_invalid_query_without_checking_authorization(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call") as mock_call:
            with self.assertRaises(InvalidQueryError):
                search_patients(actor, "Ja")

        mock_call.assert_not_called()

    def test_single_char_query_raises_invalid_query(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)

        with self.assertRaises(InvalidQueryError):
            search_patients(actor, "+")

    def test_staff_actor_with_no_accessible_patients_raises_no_data_error(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        self.create_patient(phone_number="+919876543210", name="Jane Doe")

        target = "care_im_wrapper.data.patient_lookup.AuthorizationController.call"
        with patch(target, side_effect=_empty_filter) as mock_call:
            with self.assertRaises(NoDataError):
                search_patients(actor, "Jane")

        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args.args[0], "get_filtered_patients")
        self.assertEqual(mock_call.call_args.args[2], staff_user)

    def test_no_matching_patients_raises_no_data_error(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", side_effect=_identity_filter):
            with self.assertRaises(NoDataError):
                search_patients(actor, "NoSuchPatientNameAtAll")

    def test_digit_query_searches_by_phone_number(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        self.create_patient(phone_number="+919876543210", name="Jane Doe")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", side_effect=_identity_filter):
            results = search_patients(actor, "9876543210")

        self.assertEqual(len(results), 1)
        self.assertNotIn("id", results[0])
        self.assertEqual(results[0]["name"], "Jane Doe")
        self.assertEqual(results[0]["phone_number"], mask_phone_number("+919876543210"))

    def test_plus_prefixed_query_searches_by_phone_number(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        self.create_patient(phone_number="+919876543210", name="Jane Doe")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", side_effect=_identity_filter):
            results = search_patients(actor, "+919876543210")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Jane Doe")

    def test_non_digit_non_plus_query_searches_by_name(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        patient = self.create_patient(phone_number="+919876543210", name="Jane Doe")
        self.create_patient(phone_number="+911111111111", name="Someone Else")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", side_effect=_identity_filter):
            results = search_patients(actor, "Jane")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["external_id"], str(patient.external_id))

    def test_results_are_capped_at_data_fetch_limit_of_ten(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF.value, instance=staff_user)
        for i in range(12):
            self.create_patient(phone_number=f"+9198765432{i:02d}", name=f"Common Name {i}")

        with patch("care_im_wrapper.data.patient_lookup.AuthorizationController.call", side_effect=_identity_filter):
            results = search_patients(actor, "Common Name")

        self.assertEqual(len(results), 10)
