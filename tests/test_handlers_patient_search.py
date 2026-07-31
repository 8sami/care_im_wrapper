from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_awaiting_patient_search
from care_im_wrapper.conversation.messages import InteractiveType
from care_im_wrapper.conversation.renderers import render_patient_search_results
from care_im_wrapper.data.exceptions import NoDataError, PermissionDeniedError
from care_im_wrapper.data.pagination import Page
from care_im_wrapper.models import ConversationSession


def _page(records, *, has_next=False, number=0):
    """search_patients returns a Page now; these tests only care about its records."""
    return Page(records=records, number=number, page_size=10, has_next=has_next)


PHONE = "+919876543210"
CHANNEL = "whatsapp"


def _make_actor():
    return SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))


class HandleAwaitingPatientSearchTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AWAITING_PATIENT_SEARCH,
            user_type="staff",
            user_id=7,
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=None)
    @patch("care_im_wrapper.conversation.handlers.patient_lookup.search_patients")
    def test_actor_none_logs_out_without_calling_search(self, mock_search, mock_resolve_actor):
        outbox: list[Outbound] = []
        _handle_awaiting_patient_search(self.session, PHONE, "Jane", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.NEW)
        mock_search.assert_not_called()
        self.assertEqual(
            outbox,
            [Outbound(PHONE, "Your session has expired. Please send any message to re-authenticate.")],
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.patient_lookup.search_patients")
    def test_permission_denied_reverts_to_authenticated_state(self, mock_search, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        mock_search.side_effect = PermissionDeniedError("no access")
        outbox: list[Outbound] = []

        _handle_awaiting_patient_search(self.session, PHONE, "Jane", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(outbox, [Outbound(PHONE, "You don't have permission to view this information.")])

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.patient_lookup.search_patients")
    def test_no_data_error_sends_no_patients_found_and_leaves_state_unchanged(self, mock_search, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        mock_search.side_effect = NoDataError("nothing found")
        outbox: list[Outbound] = []

        _handle_awaiting_patient_search(self.session, PHONE, "Zzz", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AWAITING_PATIENT_SEARCH)
        self.assertEqual(outbox, [Outbound(PHONE, "No patients found matching that search.")])

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.patient_lookup.search_patients")
    def test_three_or_fewer_results_uses_reply_buttons_and_saves_candidates(
        self, mock_search, mock_resolve_actor, mock_max_chars
    ):
        mock_resolve_actor.return_value = _make_actor()
        results = [
            {"id": 1, "external_id": "ext-1", "name": "Jane Doe", "phone_number": "+919****3210"},
            {"id": 2, "external_id": "ext-2", "name": "John Roe", "phone_number": "+911****1111"},
        ]
        mock_search.return_value = _page(results)
        outbox: list[Outbound] = []

        _handle_awaiting_patient_search(self.session, PHONE, "Doe", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PATIENT)
        self.assertEqual(self.session.candidates, results)

        self.assertEqual(len(outbox), 1)
        item = outbox[0]
        self.assertEqual(item.phone_number, PHONE)
        call_msg = item.message

        expected_text = render_patient_search_results(
            "Search results. Reply with the number to select:",
            ["Jane Doe — +919****3210", "John Roe — +911****1111"],
            4096,
        ).text
        self.assertEqual(call_msg.text, expected_text)
        self.assertEqual(call_msg.interactive.type, InteractiveType.REPLY_BUTTONS)
        self.assertEqual(
            call_msg.interactive.action_data,
            [
                {"id": "patient_0", "title": "Jane Doe"},
                {"id": "patient_1", "title": "John Roe"},
            ],
        )

    @patch("care_im_wrapper.conversation.handlers.get_max_chars", return_value=4096)
    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.patient_lookup.search_patients")
    def test_more_than_three_results_uses_list_with_descriptions(self, mock_search, mock_resolve_actor, mock_max_chars):
        mock_resolve_actor.return_value = _make_actor()
        results = [
            {"id": i, "external_id": f"ext-{i}", "name": f"Patient {i}", "phone_number": f"+91********{i:02d}"}
            for i in range(4)
        ]
        mock_search.return_value = _page(results)
        outbox: list[Outbound] = []

        _handle_awaiting_patient_search(self.session, PHONE, "Patient", CHANNEL, outbox)

        call_msg = outbox[0].message
        self.assertEqual(call_msg.interactive.type, InteractiveType.LIST)
        self.assertEqual(call_msg.interactive.button_label, "Select Patient")
        rows = call_msg.interactive.action_data[0]["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {"id": "patient_0", "title": "Patient 0", "description": "+91********00"})
