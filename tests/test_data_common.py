import uuid
from types import SimpleNamespace
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import MissingContextError, PermissionDeniedError
from care_im_wrapper.models import ConversationSession


class ResolveTargetPatientTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.patient = self.create_patient()

    def test_patient_actor_returns_own_instance_without_permission_check(self):
        actor = Actor(user_type=ConversationSession.UserType.PATIENT, instance=self.patient)
        session = SimpleNamespace(active_patient_external_id=None)

        with patch("care_im_wrapper.data.common.AuthorizationController.call") as mock_call:
            result = resolve_target_patient(actor, session)

        self.assertEqual(result, self.patient)
        mock_call.assert_not_called()

    def test_staff_actor_with_no_active_patient_raises_missing_context_error(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=None)

        with self.assertRaises(MissingContextError):
            resolve_target_patient(actor, session)

    def test_staff_actor_with_nonexistent_patient_external_id_raises_missing_context_error(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(uuid.uuid4()))

        with self.assertRaises(MissingContextError):
            resolve_target_patient(actor, session)

    def test_staff_actor_with_permission_returns_patient(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(self.patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=True) as mock_call:
            result = resolve_target_patient(actor, session)

        self.assertEqual(result, self.patient)
        mock_call.assert_called_once_with("can_view_patient_obj", staff_user, self.patient)

    def test_staff_actor_without_permission_raises_permission_denied_error(self):
        staff_user = self.create_user()
        actor = Actor(user_type=ConversationSession.UserType.STAFF, instance=staff_user)
        session = SimpleNamespace(active_patient_external_id=str(self.patient.external_id))

        with patch("care_im_wrapper.data.common.AuthorizationController.call", return_value=False):
            with self.assertRaises(PermissionDeniedError):
                resolve_target_patient(actor, session)
