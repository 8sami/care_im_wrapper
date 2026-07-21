from types import SimpleNamespace
from unittest.mock import patch

from care.emr.models.patient import Patient
from care.users.models import User
from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.actor import Actor, resolve_actor
from care_im_wrapper.models import ConversationSession


def _make_session(user_type, user_id):
    return SimpleNamespace(user_type=user_type, user_id=user_id)


class ResolveActorTests(CareAPITestBase):
    def test_patient_session_returns_actor_wrapping_real_patient(self):
        patient = self.create_patient()
        session = _make_session(ConversationSession.UserType.PATIENT, patient.id)

        actor = resolve_actor(session)

        self.assertIsInstance(actor, Actor)
        self.assertEqual(actor.user_type, ConversationSession.UserType.PATIENT)
        self.assertEqual(actor.instance.id, patient.id)
        self.assertIsInstance(actor.instance, Patient)

    def test_staff_session_returns_actor_wrapping_real_user(self):
        user = self.create_user()
        session = _make_session(ConversationSession.UserType.STAFF, user.id)

        actor = resolve_actor(session)

        self.assertIsInstance(actor, Actor)
        self.assertEqual(actor.user_type, ConversationSession.UserType.STAFF)
        self.assertEqual(actor.instance.id, user.id)
        self.assertIsInstance(actor.instance, User)

    @patch("care_im_wrapper.auth.actor.logger")
    def test_unknown_user_type_logs_warning_and_returns_none(self, mock_logger):
        session = _make_session(ConversationSession.UserType.UNKNOWN, 999)

        actor = resolve_actor(session)

        self.assertIsNone(actor)
        mock_logger.warning.assert_called_once_with(
            "resolve_actor: %s id=%s not found", ConversationSession.UserType.UNKNOWN, 999
        )

    def test_patient_session_with_nonexistent_id_returns_none(self):
        # A patient deleted or merged after authenticating: callers only check for None,
        # so this must not surface as Patient.DoesNotExist.
        session = _make_session(ConversationSession.UserType.PATIENT, 999999)

        self.assertIsNone(resolve_actor(session))

    def test_staff_session_with_nonexistent_id_returns_none(self):
        session = _make_session(ConversationSession.UserType.STAFF, 999999)

        self.assertIsNone(resolve_actor(session))
