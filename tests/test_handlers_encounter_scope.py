"""Encounter-scoped navigation: the two-level menu, its pickers, and paged replies.

care_fe splits clinical data into a patient level (PatientHome) and an encounter level
(EncounterShow); these tests pin the chat equivalent of that split.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import (
    Outbound,
    _handle_authenticated,
    _handle_selecting_encounter,
    _handle_selecting_prescription,
)
from care_im_wrapper.conversation.menus import Action, MenuOption, Scope
from care_im_wrapper.conversation.messages import InteractiveType, OutboundMessage
from care_im_wrapper.conversation.renderers import render_lab_reports, render_procedures
from care_im_wrapper.data.common import ALL_PRESCRIPTIONS
from care_im_wrapper.data.pagination import Page
from care_im_wrapper.data.records import EncounterRecord, PrescriptionChoiceRecord
from care_im_wrapper.models import ConversationSession
from tests.utils import patched_limits

PHONE = "+919876543210"
CHANNEL = "whatsapp"


def _make_actor():
    return SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))


def _page(records, *, number=0, has_next=False, offset=0):
    return Page(records=records, number=number, page_size=10, has_next=has_next, offset=offset)


def _encounter(external_id, facility="City Hospital", date="12 Jul 2026"):
    return EncounterRecord(
        date=date, facility=facility, status="Completed", encounter_class="Inpatient", external_id=external_id
    )


def _prescription(external_id, prescribed_by="Dr. Anita Rao", prescribed_on="12 Jul 2026, 10:30 am"):
    return PrescriptionChoiceRecord(
        prescribed_on=prescribed_on, prescribed_by=prescribed_by, name=None, external_id=external_id
    )


def _rows(message):
    return message.interactive.action_data[0]["rows"]


class EncounterPickerTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _run(self, text, encounters_page, outbox=None):
        outbox = [] if outbox is None else outbox
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch(
                "care_im_wrapper.conversation.handlers.encounters_data.fetch_encounters",
                return_value=encounters_page,
            ),
        ):
            _handle_authenticated(self.session, PHONE, text, CHANNEL, outbox)
        self.session.refresh_from_db()
        return outbox

    def test_many_encounters_offer_a_picker_and_park_the_session(self):
        page = _page([_encounter("enc-1", facility="City Hospital"), _encounter("enc-2", facility="Rural Clinic")])

        outbox = self._run("1", page)

        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_ENCOUNTER)
        self.assertEqual([c["external_id"] for c in self.session.candidates], ["enc-1", "enc-2"])
        rows = _rows(outbox[0].message)
        self.assertEqual([r["id"] for r in rows], ["encounter_0", "encounter_1", "0"])
        self.assertEqual(rows[-1]["title"], "Back to menu")

    def test_a_single_encounter_is_opened_without_asking(self):
        outbox = self._run("1", _page([_encounter("enc-1")]))

        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.menu_context, ConversationSession.MenuContext.ENCOUNTER)
        self.assertEqual(self.session.active_encounter_external_id, "enc-1")
        self.assertIn("City Hospital — 12 Jul 2026", outbox[0].message.text)

    def test_no_encounters_returns_to_the_main_menu_with_an_explanation(self):
        from care_im_wrapper.data.exceptions import NoDataError

        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch(
                "care_im_wrapper.conversation.handlers.encounters_data.fetch_encounters",
                side_effect=NoDataError,
            ),
        ):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertIn("No encounters found", outbox[0].message.text)

    def test_selecting_a_row_opens_that_encounter_and_shows_the_sub_menu(self):
        self._run("1", _page([_encounter("enc-1"), _encounter("enc-2", facility="Rural Clinic")]))

        outbox: list[Outbound] = []
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            _handle_selecting_encounter(self.session, PHONE, "encounter_1", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.active_encounter_external_id, "enc-2")
        # The label carries the status too, so the sub-menu header shows it.
        self.assertEqual(self.session.active_encounter_label, "Rural Clinic — 12 Jul 2026 (Completed)")
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual([r["title"] for r in _rows(outbox[0].message)][:2], ["Medications", "Procedures"])

    def test_typed_number_selects_the_same_row_as_the_interactive_id(self):
        self._run("1", _page([_encounter("enc-1"), _encounter("enc-2")]))

        outbox: list[Outbound] = []
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            _handle_selecting_encounter(self.session, PHONE, "2", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.active_encounter_external_id, "enc-2")

    def test_zero_backs_out_without_changing_the_scope(self):
        self._run("1", _page([_encounter("enc-1"), _encounter("enc-2")]))

        outbox: list[Outbound] = []
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            _handle_selecting_encounter(self.session, PHONE, "0", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertEqual(self.session.active_encounter_external_id, "")

    def test_next_pages_the_picker_and_keeps_its_rows_selectable(self):
        first = _page([_encounter(f"enc-{i}") for i in range(2)], has_next=True)
        self._run("1", first)

        second = _page([_encounter("enc-9")], number=1, offset=2, has_next=True)
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch(
                "care_im_wrapper.conversation.handlers.encounters_data.fetch_encounters",
                return_value=second,
            ),
        ):
            _handle_selecting_encounter(self.session, PHONE, "n", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.data_page, 1)
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_ENCOUNTER)
        self.assertIn("encounter_0", [r["id"] for r in _rows(outbox[0].message)])
        # Paging is a buttons message of its own, so the rows stay entirely selectable.
        self.assertEqual([b["id"] for b in outbox[1].message.interactive.action_data], ["page_prev", "page_next"])

    def test_numbering_continues_across_pages(self):
        """Row ids restart per page, but the printed numbers do not -- and a typed number has
        to pick the row it is printed against."""
        self._run("1", _page([_encounter(f"enc-{i}") for i in range(2)], has_next=True))

        second = _page([_encounter("enc-9")], number=1, offset=2)
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch(
                "care_im_wrapper.conversation.handlers.encounters_data.fetch_encounters",
                return_value=second,
            ),
        ):
            _handle_selecting_encounter(self.session, PHONE, "n", CHANNEL, [])
        self.session.refresh_from_db()
        self.assertEqual([c["token"] for c in self.session.candidates], ["3"])

        outbox: list[Outbound] = []
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            _handle_selecting_encounter(self.session, PHONE, "3", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.active_encounter_external_id, "enc-9")

    def test_a_single_encounter_sub_menu_omits_change_encounter(self):
        # One encounter auto-opens; "Change encounter" would just reopen it, so it's gone.
        outbox = self._run("1", _page([_encounter("enc-1")]))

        self.assertFalse(self.session.active_encounter_has_alternatives)
        titles = [r["title"] for r in _rows(outbox[0].message)]
        self.assertNotIn("Change encounter", titles)

    def test_change_encounter_reopens_the_picker_when_alternatives_exist(self):
        # Two encounters -> picker -> pick one -> alternatives recorded.
        self._run("1", _page([_encounter("enc-1"), _encounter("enc-2")]))
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            _handle_selecting_encounter(self.session, PHONE, "encounter_0", CHANNEL, [])
        self.session.refresh_from_db()
        self.assertTrue(self.session.active_encounter_has_alternatives)

        self._run("5", _page([_encounter("enc-1"), _encounter("enc-2")]))

        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_ENCOUNTER)


class EncounterSubMenuTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
            menu_context=ConversationSession.MenuContext.ENCOUNTER,
            active_encounter_external_id="enc-1",
            active_encounter_label="City Hospital — 12 Jul 2026",
        )

    def test_zero_leaves_the_encounter_rather_than_logging_out(self):
        outbox: list[Outbound] = []
        _handle_authenticated(self.session, PHONE, "0", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertEqual(self.session.active_encounter_external_id, "")
        self.assertEqual([r["title"] for r in _rows(outbox[0].message)][-1], "Logout")

    def test_lab_reports_still_opens_the_document_picker(self):
        """Routing lab reports through the real encounter sub-menu must still park in the
        document pick-list -- not just show the list."""
        from care_im_wrapper.data.records import LabReportRecord
        from care_im_wrapper.documents import resolvers as document_resolvers

        reports = [
            LabReportRecord(name="CBC", date="20 Jul 2026", status="Final", external_id="rep-1"),
            LabReportRecord(name="Lipid panel", date="20 Jul 2026", status="Final", external_id="rep-2"),
        ]
        # Real Lab reports option (key "3"), only its fetcher stubbed so no DB is needed --
        # the document_resolver that makes it a pick-list is the genuine one.
        lab_reports_option = MenuOption(
            label="Lab reports",
            fetcher=MagicMock(return_value=_page(reports)),
            renderer=render_lab_reports,
            document_resolver=document_resolvers.resolve_diagnostic_report_document,
            scope=Scope.ENCOUNTER,
        )
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict("care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"3": lab_reports_option}, clear=True),
        ):
            _handle_authenticated(self.session, PHONE, "3", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_DOCUMENT)
        self.assertEqual([c["external_id"] for c in self.session.candidates], ["rep-1", "rep-2"])
        self.assertEqual([c["menu_key"] for c in self.session.candidates], ["3", "3"])
        row_ids = [r["id"] for r in _rows(outbox[0].message)]
        self.assertEqual(row_ids, ["document_0", "document_1", "0"])

    def test_the_records_are_headed_by_the_scope_they_belong_to(self):
        """The scope heads the records rather than trailing them, and takes the place of the
        fetcher's own "Your recent procedures:" line rather than repeating it."""
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict(
                "care_im_wrapper.conversation.menus._ENCOUNTER_MENU",
                {
                    "2": MenuOption(
                        label="Procedures",
                        fetcher=MagicMock(return_value=_page(["x"])),
                        renderer=render_procedures,
                        scope=Scope.ENCOUNTER,
                    )
                },
                clear=True,
            ),
        ):
            _handle_authenticated(self.session, PHONE, "2", CHANNEL, outbox)

        text = outbox[0].message.text
        self.assertTrue(text.startswith("Viewing *procedures* for encounter City Hospital — 12 Jul 2026"))
        self.assertNotIn("Your recent procedures:", text)

    def test_an_unscoped_list_keeps_the_fetchers_own_header(self):
        """A patient reading their own records from the main menu has no scope to report, so
        the list still has to say what it is."""
        session = ConversationSession.objects.create(
            phone_number="+919000000000",
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict(
                "care_im_wrapper.conversation.menus._MAIN_MENU",
                {
                    "2": MenuOption(
                        label="Procedures", fetcher=MagicMock(return_value=_page([])), renderer=render_procedures
                    )
                },
                clear=True,
            ),
        ):
            _handle_authenticated(session, "+919000000000", "2", CHANNEL, outbox)

        self.assertIn("Your recent procedures:", outbox[0].message.text)
        self.assertNotIn("Viewing", outbox[0].message.text)

    def test_discharge_summary_resolves_the_open_encounter_without_a_pick_list(self):
        resolver = MagicMock(return_value=object())
        option = MenuOption(
            label="Discharge summary",
            document_resolver=resolver,
            scope=Scope.ENCOUNTER,
            action=Action.ENCOUNTER_DOCUMENT,
        )
        encounter = SimpleNamespace(patient=object(), external_id="enc-1")
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch("care_im_wrapper.conversation.handlers.resolve_target_encounter", return_value=encounter),
            patch("care_im_wrapper.conversation.handlers.get_or_create_document_link", return_value=object()),
            patch("care_im_wrapper.conversation.handlers.build_document_url", return_value="https://x/doc/"),
            patch.dict("care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"4": option}, clear=True),
        ):
            _handle_authenticated(self.session, PHONE, "4", CHANNEL, outbox)

        self.session.refresh_from_db()
        # The resolver is handed the encounter resolve_target_encounter already vetted
        # against the patient, not a raw id off the session.
        resolver.assert_called_once_with(encounter.patient, "enc-1")
        # No pick-list: the session never leaves the sub-menu.
        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)

    def test_a_stale_encounter_drops_back_to_the_main_menu(self):
        """Redisplaying the sub-menu would point every option at the same dead encounter."""
        from care_im_wrapper.data.exceptions import MissingContextError

        option = MenuOption(
            label="Procedures",
            fetcher=MagicMock(side_effect=MissingContextError("That encounter is no longer available.")),
            renderer=MagicMock(),
            scope=Scope.ENCOUNTER,
        )
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict("care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"2": option}, clear=True),
        ):
            _handle_authenticated(self.session, PHONE, "2", CHANNEL, outbox)

        self.session.refresh_from_db()
        self.assertEqual(self.session.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertEqual(self.session.active_encounter_external_id, "")
        self.assertIn("no longer available", outbox[0].message.text)


class PrescriptionPickerTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
            menu_context=ConversationSession.MenuContext.ENCOUNTER,
            active_encounter_external_id="enc-1",
            active_encounter_label="City Hospital — 12 Jul 2026",
        )
        self.fetcher = MagicMock(return_value=_page(["med"]))
        self.medications_option = MenuOption(
            label="Medications",
            fetcher=self.fetcher,
            renderer=MagicMock(return_value=OutboundMessage(text="Your medications:")),
            scope=Scope.PRESCRIPTION,
        )

    def _open_medications(self, choices, text="1", outbox=None):
        outbox = [] if outbox is None else outbox
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch(
                "care_im_wrapper.conversation.handlers.medications_data.fetch_prescription_choices",
                return_value=choices,
            ),
            patch.dict(
                "care_im_wrapper.conversation.menus._ENCOUNTER_MENU",
                {"1": self.medications_option},
                clear=True,
            ),
        ):
            _handle_authenticated(self.session, PHONE, text, CHANNEL, outbox)
        self.session.refresh_from_db()
        return outbox

    def test_many_prescriptions_offer_a_picker_led_by_all(self):
        outbox = self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))

        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PRESCRIPTION)
        message = outbox[0].message
        rows = _rows(message)
        self.assertEqual(rows[0]["id"], "prescription_all")
        self.assertEqual(rows[0]["title"], "All prescriptions")
        self.assertEqual(rows[0]["description"], "View all medications")
        self.assertEqual([r["id"] for r in rows[1:]], ["prescription_0", "prescription_1", "0"])
        # The rendered prescription list is NOT duplicated into the interactive body -- the
        # rows already show it; it lives only in the plain-text fallback.
        self.assertNotIn("Prescribed by", message.interactive.body)
        self.assertIn("Which prescription", message.interactive.body)
        self.assertIn("Prescribed by", message.text)

    def test_a_single_prescription_skips_the_picker_and_shows_everything(self):
        self._open_medications(_page([_prescription("rx-1")]))

        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.active_prescription_external_id, ALL_PRESCRIPTIONS)
        self.fetcher.assert_called_once()

    def test_no_prescriptions_still_lists_the_unlinked_medications(self):
        from care_im_wrapper.data.exceptions import NoDataError

        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch(
                "care_im_wrapper.conversation.handlers.medications_data.fetch_prescription_choices",
                side_effect=NoDataError,
            ),
            patch.dict(
                "care_im_wrapper.conversation.menus._ENCOUNTER_MENU",
                {"1": self.medications_option},
                clear=True,
            ),
        ):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.active_prescription_external_id, ALL_PRESCRIPTIONS)
        self.fetcher.assert_called_once()

    def _select(self, text):
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict(
                "care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"1": self.medications_option}, clear=True
            ),
        ):
            _handle_selecting_prescription(self.session, PHONE, text, CHANNEL, outbox)
        self.session.refresh_from_db()
        return outbox

    def test_a_selects_every_prescription_and_keeps_the_picker(self):
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))

        self._select("a")

        self.assertEqual(self.session.active_prescription_external_id, ALL_PRESCRIPTIONS)
        # Stays in the picker so the reader can switch again without backing out.
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PRESCRIPTION)

    def test_all_prescriptions_medications_paginate_alongside_the_picker(self):
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))
        paged = MenuOption(
            label="Medications",
            fetcher=MagicMock(return_value=_page(["m"], has_next=True)),
            renderer=MagicMock(return_value=OutboundMessage(text="Your medications:")),
            scope=Scope.PRESCRIPTION,
        )

        def select(text):
            outbox: list[Outbound] = []
            with (
                patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
                patch.dict("care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"1": paged}, clear=True),
            ):
                _handle_selecting_prescription(self.session, PHONE, text, CHANNEL, outbox)
            self.session.refresh_from_db()
            return outbox

        outbox = select("a")

        self.assertEqual(self.session.active_prescription_external_id, ALL_PRESCRIPTIONS)
        # The rows stay purely selectable: paging rides on a second, buttons-only message.
        row_ids = [r["id"] for r in _rows(outbox[0].message)]
        self.assertEqual(row_ids, ["prescription_all", "prescription_0", "prescription_1", "0"])
        self.assertEqual([b["id"] for b in outbox[1].message.interactive.action_data], ["page_next"])
        self.assertFalse(outbox[1].pace)

        # Paging advances the medications, not the prescription list, and stays in the picker.
        select("n")
        self.assertEqual(self.session.data_page, 1)
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PRESCRIPTION)

    def test_paging_the_medications_never_renumbers_the_prescriptions(self):
        """The picker's numbers are fixed when it is offered. Paging the medications beneath
        it moves a different cursor, and a typed number must still mean the same prescription."""
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))
        paged = MenuOption(
            label="Medications",
            fetcher=MagicMock(return_value=_page(["m"], has_next=True)),
            renderer=MagicMock(return_value=OutboundMessage(text="Your medications:")),
            scope=Scope.PRESCRIPTION,
        )

        def select(text):
            outbox: list[Outbound] = []
            with (
                patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
                patch.dict("care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"1": paged}, clear=True),
            ):
                _handle_selecting_prescription(self.session, PHONE, text, CHANNEL, outbox)
            self.session.refresh_from_db()
            return outbox

        select("a")
        select("n")
        self.assertEqual(self.session.data_page, 1)

        # "2" is still the second prescription, not an index into the medications page.
        select("2")
        self.assertEqual(self.session.active_prescription_external_id, "rx-2")

    def test_the_plain_text_fallback_keeps_the_prescription_options(self):
        """The body asks which prescription; a provider that cannot draw rows must still be
        shown what there is to choose from."""
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))

        outbox = self._select("a")

        text = outbox[0].message.text
        self.assertIn("Your medications:", text)
        self.assertIn("Prescribed by", text)
        self.assertIn("1.", text)
        self.assertIn("2.", text)

    def test_selecting_a_prescription_shows_meds_with_the_picker_as_the_menu(self):
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))

        outbox = self._select("prescription_1")

        self.assertEqual(self.session.active_prescription_external_id, "rx-2")
        self.fetcher.assert_called_once()
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PRESCRIPTION)
        # The menu on the medications reply is the prescription picker, not the encounter
        # sub-menu -- the reader can switch prescriptions in place.
        row_ids = [r["id"] for r in _rows(outbox[0].message)]
        self.assertEqual(row_ids, ["prescription_all", "prescription_0", "prescription_1", "0"])
        self.assertIn("Your medications:", outbox[0].message.interactive.body)

    def test_switching_prescriptions_in_place_updates_the_filter(self):
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))

        self._select("prescription_0")
        self.assertEqual(self.session.active_prescription_external_id, "rx-1")

        self._select("prescription_1")
        self.assertEqual(self.session.active_prescription_external_id, "rx-2")
        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PRESCRIPTION)

    def test_back_from_the_picker_returns_to_the_encounter_sub_menu(self):
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))
        self._select("prescription_0")

        # "0" leaves the picker for the encounter sub-menu.
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            outbox: list[Outbound] = []
            _handle_selecting_prescription(self.session, PHONE, "0", CHANNEL, outbox)
        self.session.refresh_from_db()

        self.assertEqual(self.session.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(self.session.menu_context, ConversationSession.MenuContext.ENCOUNTER)
        self.assertIn("Procedures", [r["title"] for r in _rows(outbox[0].message)])

    def test_re_entering_medications_asks_again(self):
        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict(
                "care_im_wrapper.conversation.menus._ENCOUNTER_MENU", {"1": self.medications_option}, clear=True
            ),
        ):
            _handle_selecting_prescription(self.session, PHONE, "prescription_0", CHANNEL, [])
        self.session.refresh_from_db()
        self.assertEqual(self.session.active_prescription_external_id, "rx-1")

        self._open_medications(_page([_prescription("rx-1"), _prescription("rx-2")]))

        self.assertEqual(self.session.state, ConversationSession.State.SELECTING_PRESCRIPTION)
        self.assertEqual(self.session.active_prescription_external_id, "")

    def test_changing_encounter_drops_the_prescription_filter(self):
        self.session.set_prescription_scope("rx-1", "12 Jul 2026")

        self.session.open_encounter("enc-2", "Rural Clinic — 01 Aug 2026")

        self.assertEqual(self.session.active_prescription_external_id, "")


class PaginatedDataListTests(TestCase):
    """A data list carries navigation as reply buttons: Previous/Next when paged, and always
    Menu. Paging is on the buttons, not the list rows or the body text."""

    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _buttons(self, message):
        return [b["id"] for b in message.interactive.action_data]

    def _run(self, page):
        option = MenuOption(
            label="Appointments",
            fetcher=MagicMock(return_value=page),
            renderer=MagicMock(return_value=OutboundMessage(text="Your recent appointments:")),
        )
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", {"2": option}, clear=True),
        ):
            _handle_authenticated(self.session, PHONE, "2", CHANNEL, outbox)
        self.session.refresh_from_db()
        return outbox

    def test_first_page_offers_next_then_menu_and_no_previous(self):
        outbox = self._run(_page(["a"], has_next=True))

        self.assertEqual(len(outbox), 1)
        message = outbox[0].message
        self.assertEqual(message.interactive.type, InteractiveType.REPLY_BUTTONS)
        self.assertEqual(self._buttons(message), ["page_next", "page_menu"])
        # Paging affordance is the buttons, not a hint dumped in the body.
        self.assertNotIn("Send", message.interactive.body)

    def test_a_middle_page_offers_previous_next_and_menu(self):
        self.session.open_data_list("2")
        self.session.advance_page(10)

        option = MenuOption(
            label="Appointments",
            fetcher=MagicMock(return_value=_page(["a"], number=1, has_next=True, offset=10)),
            renderer=MagicMock(return_value=OutboundMessage(text="Your recent appointments:")),
        )
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", {"2": option}, clear=True),
        ):
            _handle_authenticated(self.session, PHONE, "n", CHANNEL, outbox)

        self.assertEqual(self._buttons(outbox[0].message), ["page_prev", "page_next", "page_menu"])

    def test_an_unpaginated_list_keeps_the_view_menu_list(self):
        """No pagination -> no buttons; the reply is the View Menu interactive list, with
        the menu option and Logout in its rows."""
        outbox = self._run(_page(["a"]))

        message = outbox[0].message
        self.assertEqual(message.interactive.type, InteractiveType.LIST)
        self.assertEqual([r["id"] for r in _rows(message)], ["2", "0"])

    def test_the_menu_button_reopens_the_menu(self):
        self._run(_page(["a"], has_next=True))

        outbox: list[Outbound] = []
        with patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()):
            _handle_authenticated(self.session, PHONE, "page_menu", CHANNEL, outbox)

        self.assertEqual(outbox[0].message.interactive.type, InteractiveType.LIST)
        self.assertIn("Please choose an option:", outbox[0].message.text)

    def test_a_provider_without_reply_buttons_falls_back_to_the_list(self):
        option = MenuOption(
            label="Appointments",
            fetcher=MagicMock(return_value=_page(["a"], has_next=True)),
            renderer=MagicMock(return_value=OutboundMessage(text="Your recent appointments:")),
        )
        outbox: list[Outbound] = []
        with (
            patch("care_im_wrapper.conversation.handlers.resolve_actor", return_value=_make_actor()),
            patched_limits(max_buttons=0),
            patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", {"2": option}, clear=True),
        ):
            _handle_authenticated(self.session, PHONE, "2", CHANNEL, outbox)

        message = outbox[0].message
        self.assertEqual(message.interactive.type, InteractiveType.LIST)
        # Fallback puts paging in the rows and the typed hint in the text.
        self.assertIn("page_next", [r["id"] for r in _rows(message)])
        self.assertIn("Page 1", message.text)


class SessionScopeFieldTests(TestCase):
    """The model owns which fields a navigation move resets; handlers just name the move."""

    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="staff",
            user_id=42,
            active_patient_external_id="patient-1",
        )
        self.session.open_encounter("enc-1", "City Hospital — 12 Jul 2026")
        self.session.set_prescription_scope("rx-1", "12 Jul 2026")
        self.session.open_data_list("1")
        self.session.advance_page(10)

    def _reload(self):
        self.session.refresh_from_db()
        return self.session

    def test_open_encounter_persists_scope_state_and_paging_in_one_write(self):
        self.session.open_encounter("enc-2", "Rural Clinic — 01 Aug 2026")

        stored = self._reload()
        self.assertEqual(stored.menu_context, ConversationSession.MenuContext.ENCOUNTER)
        self.assertEqual(stored.active_encounter_external_id, "enc-2")
        self.assertEqual(stored.active_prescription_external_id, "")
        self.assertEqual(stored.state, ConversationSession.State.AUTHENTICATED)
        self.assertEqual(stored.candidates, [])
        self.assertEqual(stored.data_offsets, [])

    def test_clear_encounter_scope_drops_the_prescription_with_it(self):
        self.session.clear_encounter_scope()

        stored = self._reload()
        self.assertEqual(stored.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertEqual(stored.active_encounter_external_id, "")
        self.assertEqual(stored.active_encounter_label, "")
        self.assertEqual(stored.active_prescription_external_id, "")

    def test_switch_patient_leaves_nothing_scoped_to_the_previous_one(self):
        self.session.switch_patient("patient-2")

        stored = self._reload()
        self.assertEqual(stored.active_patient_external_id, "patient-2")
        self.assertEqual(stored.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertEqual(stored.active_encounter_external_id, "")
        self.assertEqual(stored.active_prescription_external_id, "")
        self.assertEqual(stored.data_menu_choice, "")
        self.assertEqual(stored.search_query, "")

    def test_logout_clears_the_encounter_scope_too(self):
        self.session.logout()

        stored = self._reload()
        self.assertEqual(stored.menu_context, ConversationSession.MenuContext.MAIN)
        self.assertEqual(stored.active_encounter_external_id, "")
        self.assertEqual(stored.active_prescription_external_id, "")
