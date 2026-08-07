from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_authenticated
from care_im_wrapper.conversation.menus import Action, MenuOption
from care_im_wrapper.data.exceptions import DataFetchError, MissingContextError, NoDataError, PermissionDeniedError
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


def _make_actor():
    return SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))


class HandleAuthenticatedLogoutAndSessionTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_choice_zero_logs_out_without_resolving_actor(self, mock_resolve_actor):
        outbox: list[Outbound] = []
        _handle_authenticated(self.session, PHONE, "0", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.NEW)
        self.assertEqual(self.session.user_type, ConversationSession.UserType.UNKNOWN)
        mock_resolve_actor.assert_not_called()
        self.assertEqual(outbox, [Outbound(PHONE, "You have been logged out. Send any message to start again.")])

    @patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=None)
    def test_resolve_actor_returning_none_logs_out_with_session_expired(self, mock_resolve_actor):
        outbox: list[Outbound] = []
        _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.NEW)
        self.assertEqual(
            outbox,
            [Outbound(PHONE, "Your session has expired. Please send any message to re-authenticate.")],
        )


class HandleAuthenticatedMenuDispatchTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_choice_not_in_menu_sends_invalid_choice(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", {}, clear=True):
            _handle_authenticated(self.session, PHONE, "99", CHANNEL, outbox)

        self.assertEqual(outbox, [Outbound(PHONE, "Please reply with a valid number from the list.")])

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_entry_with_none_fetcher_moves_to_awaiting_patient_search(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        fake_entry = {"7": MenuOption(label="Patient lookup", action=Action.PATIENT_SEARCH)}
        outbox: list[Outbound] = []

        with patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", fake_entry, clear=True):
            _handle_authenticated(self.session, PHONE, "7", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_PATIENT_SEARCH)
        self.assertEqual(outbox, [Outbound(PHONE, "Enter the patient's phone number or name to search.")])


class HandleAuthenticatedExceptionBranchTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _patch_menu_with_fetcher(self, side_effect):
        fetcher = MagicMock(side_effect=side_effect)
        renderer = MagicMock()
        entry = {"1": MenuOption(label="Test Label", fetcher=fetcher, renderer=renderer)}
        return patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", entry, clear=True), fetcher, renderer

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_permission_denied_error_sends_permission_denied_prefix(self, mock_send_menu, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        patcher, _, _ = self._patch_menu_with_fetcher(PermissionDeniedError("no access"))
        outbox: list[Outbound] = []

        with patcher:
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        mock_send_menu.assert_called_once_with(
            self.session, PHONE, CHANNEL, outbox, prefix="You don't have permission to view this information."
        )
        self.assertEqual(outbox, [])

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_missing_context_error_uses_exception_message_as_prefix(self, mock_send_menu, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        patcher, _, _ = self._patch_menu_with_fetcher(MissingContextError("No active patient selected"))
        outbox: list[Outbound] = []

        with patcher:
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        mock_send_menu.assert_called_once_with(
            self.session, PHONE, CHANNEL, outbox, prefix="No active patient selected"
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_no_data_error_uses_lowercased_label_in_no_data_message(self, mock_send_menu, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        patcher, _, _ = self._patch_menu_with_fetcher(NoDataError("nothing found"))
        outbox: list[Outbound] = []

        with patcher:
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        mock_send_menu.assert_called_once_with(
            self.session, PHONE, CHANNEL, outbox, prefix="No test label found on record."
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers._send_menu")
    def test_data_fetch_error_sends_generic_fetch_error_prefix(self, mock_send_menu, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        patcher, _, _ = self._patch_menu_with_fetcher(DataFetchError("upstream failed"))
        outbox: list[Outbound] = []

        with patcher:
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        mock_send_menu.assert_called_once_with(
            self.session, PHONE, CHANNEL, outbox, prefix="Could not retrieve that information. Please try again."
        )
