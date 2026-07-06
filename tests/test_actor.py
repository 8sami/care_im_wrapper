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

    def test_patient_session_with_nonexistent_id_raises_does_not_exist(self):
        # NOTE: the docstring claims this returns None for a deleted/merged record,
        # but the current implementation has no try/except around Patient.objects.get() —
        # it raises Patient.DoesNotExist instead. This test documents actual behavior,
        # not the documented intent. Flag to the team: this may be a real bug, since every
        # caller of resolve_actor only checks `if actor is None`, not for this exception.
        session = _make_session(ConversationSession.UserType.PATIENT, 999999)

        with self.assertRaises(Patient.DoesNotExist):
            resolve_actor(session)

    def test_staff_session_with_nonexistent_id_raises_does_not_exist(self):
        # Same discrepancy as above, for the staff/User path.
        session = _make_session(ConversationSession.UserType.STAFF, 999999)

        with self.assertRaises(User.DoesNotExist):
            resolve_actor(session)
