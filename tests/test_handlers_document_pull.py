from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_authenticated, _handle_selecting_document
from care_im_wrapper.conversation.menus import MenuOption
from care_im_wrapper.conversation.messages import InteractiveType, OutboundMessage
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.models import ConversationSession
from tests.utils import patched_limits

PHONE = "+919876543210"
CHANNEL = "whatsapp"
DOCUMENT_URL = "https://example.com/api/care_im_wrapper/documents/tok/"


def _make_actor():
    return SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))


def _record(name, external_id, date="20 Jul 2026", status="Final"):
    return SimpleNamespace(name=name, date=date, status=status, external_id=external_id)


class EnterDocumentSelectionTests(TestCase):
    """A menu item whose records carry documents offers a pick-list."""

    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _patch_menu(self, records):
        fetcher = MagicMock(return_value=records)
        renderer = MagicMock(return_value=OutboundMessage(text="Your recent lab reports:\n\n1. Urine — 20 Jul 2026"))
        entry = {
            "5": MenuOption(label="Lab reports", fetcher=fetcher, renderer=renderer, document_resolver=MagicMock())
        }
        return patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", entry, clear=True)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_offers_one_row_per_record_and_parks_in_selecting_document(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        records = [_record("Urine", "uuid-1"), _record("Lipid panel", "uuid-2")]
        outbox: list[Outbound] = []

        with self._patch_menu(records):
            _handle_authenticated(self.session, PHONE, "5", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_DOCUMENT)
        self.assertEqual([c["external_id"] for c in self.session.candidates], ["uuid-1", "uuid-2"])
        self.assertEqual([c["menu_key"] for c in self.session.candidates], ["5", "5"])

        self.assertEqual(len(outbox), 1)
        rows = outbox[0].message.interactive.action_data[0]["rows"]
        self.assertEqual([r["id"] for r in rows], ["document_0", "document_1", "0"])
        self.assertEqual(rows[0]["title"], "Urine")
        self.assertEqual(rows[0]["description"], "20 Jul 2026 (Final)")

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_caps_rows_so_the_back_row_survives_the_providers_row_limit(self, mock_resolve_actor):
        """The cap comes from the provider, not a constant in the conversation layer."""
        mock_resolve_actor.return_value = _make_actor()
        records = [_record(f"Test {i}", f"uuid-{i}") for i in range(10)]
        outbox: list[Outbound] = []

        with self._patch_menu(records):
            _handle_authenticated(self.session, PHONE, "5", CHANNEL, outbox)

        rows = outbox[0].message.interactive.action_data[0]["rows"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[-1]["id"], "0")

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_records_without_an_external_id_fall_back_to_the_plain_text_reply(self, mock_resolve_actor):
        """Records cached before external_id existed come back without one. A pick-list
        with nothing in it would be a dead end."""
        mock_resolve_actor.return_value = _make_actor()
        records = [_record("Urine", ""), _record("Lipid panel", "")]
        outbox: list[Outbound] = []

        with self._patch_menu(records):
            _handle_authenticated(self.session, PHONE, "5", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(len(outbox), 1)
        # An unpaged data reply: the View Menu list, not a pick-list.
        rows = outbox[0].message.interactive.action_data[0]["rows"]
        self.assertEqual(rows[-1]["title"], "Logout")

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_summary_too_long_for_the_body_keeps_the_rows_and_drops_the_dump(self, mock_resolve_actor):
        """Over the body limit the send degrades to plain text and loses the pick-list, so the
        body stays the prompt alone and the records ride in the plain-text fallback."""
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu([_record("Urine", "uuid-1")]), patched_limits(interactive_body=40):
            _handle_authenticated(self.session, PHONE, "5", CHANNEL, outbox)

        msg = outbox[0].message
        self.assertEqual(msg.interactive.body, "Select from the list:")
        # The records survive in the plain text, written out from the same choices as the rows.
        self.assertIn("1.  Urine", msg.text)
        self.assertIn("20 Jul 2026 (Final)", msg.text)
        self.assertEqual([r["id"] for r in msg.interactive.action_data[0]["rows"]], ["document_0", "0"])


class HandleSelectingDocumentTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.SELECTING_DOCUMENT,
            user_type="patient",
            user_id=42,
            candidates=[
                {
                    "external_id": "uuid-1",
                    "title": "Urine",
                    "description": "20 Jul 2026 (Final)",
                    "menu_key": "5",
                    "row_id": "document_0",
                    "token": "1",
                },
            ],
        )

    def _patch_menu(self, document_resolver):
        entry = {
            "5": MenuOption(
                label="Lab reports",
                fetcher=MagicMock(),
                renderer=MagicMock(),
                document_resolver=document_resolver,
            )
        }
        return patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", entry, clear=True)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.resolve_target_patient")
    @patch("care_im_wrapper.conversation.handlers.get_or_create_document_link")
    @patch("care_im_wrapper.conversation.handlers.build_document_url", return_value=DOCUMENT_URL)
    def test_selecting_a_row_sends_a_cta_button_for_that_record(
        self, mock_build_url, mock_get_link, mock_resolve_patient, mock_resolve_actor
    ):
        mock_resolve_actor.return_value = _make_actor()
        fake_patient = SimpleNamespace()
        mock_resolve_patient.return_value = fake_patient
        document_resolver = MagicMock(return_value=object())
        outbox: list[Outbound] = []

        with self._patch_menu(document_resolver):
            _handle_selecting_document(self.session, PHONE, "document_0", CHANNEL, outbox)

        document_resolver.assert_called_once_with(fake_patient, "uuid-1")
        mock_build_url.assert_called_once_with(mock_get_link.return_value)

        # Only the document is sent -- no menu re-send. The session stays in the pick-list so
        # the user can select another record or send "0" to go back.
        self.assertEqual(len(outbox), 1)
        document_msg = outbox[0].message
        self.assertEqual(document_msg.interactive.type, InteractiveType.CTA_URL)
        self.assertIn("Urine", document_msg.interactive.body)
        self.assertEqual(document_msg.interactive.action_data[0]["url"], DOCUMENT_URL)
        self.assertIn(DOCUMENT_URL, document_msg.text)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_DOCUMENT)
        self.assertEqual(len(self.session.candidates), 1)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_typed_number_selects_the_same_row_as_the_interactive_id(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        document_resolver = MagicMock(return_value=None)
        outbox: list[Outbound] = []

        with (
            self._patch_menu(document_resolver),
            patch("care_im_wrapper.conversation.handlers.resolve_target_patient") as mock_patient,
        ):
            _handle_selecting_document(self.session, PHONE, "1", CHANNEL, outbox)

        document_resolver.assert_called_once_with(mock_patient.return_value, "uuid-1")

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_back_returns_to_the_main_menu_without_generating(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        document_resolver = MagicMock()
        outbox: list[Outbound] = []

        with self._patch_menu(document_resolver):
            _handle_selecting_document(self.session, PHONE, "0", CHANNEL, outbox)

        document_resolver.assert_not_called()
        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].message.interactive.type, InteractiveType.LIST)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.resolve_target_patient")
    def test_record_with_no_document_reports_back_and_returns_to_menu(self, mock_resolve_patient, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        document_resolver = MagicMock(return_value=None)
        outbox: list[Outbound] = []

        with self._patch_menu(document_resolver):
            _handle_selecting_document(self.session, PHONE, "document_0", CHANNEL, outbox)

        self.assertEqual(len(outbox), 1)
        self.assertIn("isn't available", outbox[0].message.text)
        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    @patch("care_im_wrapper.conversation.handlers.resolve_target_patient")
    @patch(
        "care_im_wrapper.conversation.handlers.get_or_create_document_link",
        side_effect=DocumentUnavailableError("no template"),
    )
    def test_generation_failure_reports_back_and_returns_to_menu(
        self, mock_get_link, mock_resolve_patient, mock_resolve_actor
    ):
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu(MagicMock(return_value=object())):
            _handle_selecting_document(self.session, PHONE, "document_0", CHANNEL, outbox)

        self.assertEqual(len(outbox), 1)
        self.assertIn("isn't available", outbox[0].message.text)
        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_out_of_range_choice_stays_in_selection(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu(MagicMock()):
            _handle_selecting_document(self.session, PHONE, "document_7", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_DOCUMENT)
