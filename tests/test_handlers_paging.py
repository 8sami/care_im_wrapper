"""Paging commands in the conversation layer."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import (
    _handle_authenticated,
    _paging_step,
)
from care_im_wrapper.conversation.menus import MenuOption
from care_im_wrapper.conversation.replies import paging_rows
from care_im_wrapper.data.pagination import Page, current_offset, current_record_offset
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"


def _page(records, *, number=0, has_next=False, offset=0, weights=()):
    return Page(
        records=records,
        number=number,
        page_size=10,
        has_next=has_next,
        offset=offset,
        source_weights=tuple(weights),
    )


def _option(fetcher):
    return MenuOption(
        label="Meds",
        fetcher=fetcher,
        renderer=lambda r, m, start=1, *, header="": SimpleNamespace(text="Meds"),
    )


class PagingStepTests(TestCase):
    def test_next_tokens(self):
        for token in ("n", "N", "next", "Next", "page_next", " n "):
            with self.subTest(token=token):
                self.assertEqual(_paging_step(token), 1)

    def test_previous_tokens(self):
        for token in ("p", "prev", "previous", "page_prev"):
            with self.subTest(token=token):
                self.assertEqual(_paging_step(token), -1)

    def test_menu_digits_are_not_paging_commands(self):
        for token in ("0", "1", "7", "", "nope", "patient_0"):
            with self.subTest(token=token):
                self.assertEqual(_paging_step(token), 0)


class NavigationControlsTests(TestCase):
    """Paging is rows in the list it pages, on every provider."""

    def test_no_controls_when_there_is_nowhere_to_go(self):
        self.assertEqual(paging_rows(_page([1])), [])

    def test_the_first_page_offers_only_the_move_it_has(self):
        self.assertEqual([r["id"] for r in paging_rows(_page([1], has_next=True))], ["page_next"])

    def test_a_middle_page_offers_both_directions_forward_first(self):
        middle = _page([1], number=1, has_next=True)

        self.assertEqual([r["id"] for r in paging_rows(middle)], ["page_next", "page_prev"])


class MenuPagingTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=7,
        )
        self.actor = SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))

    def _run(self, text, outbox=None):
        outbox = [] if outbox is None else outbox
        _handle_authenticated(self.session, PHONE, text, CHANNEL, outbox)
        self.session.refresh_from_db()
        return outbox

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_paging_before_opening_anything_is_rejected(self, mock_actor):
        mock_actor.return_value = self.actor

        outbox = self._run("n")

        self.assertIn("Pick something from the menu first", outbox[0].message)
        self.assertEqual(self.session.data_page, 0)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_previous_on_the_first_page_is_rejected(self, mock_actor):
        mock_actor.return_value = self.actor
        self.session.open_data_list("2")

        outbox = self._run("p")

        self.assertIn("already on the first page", outbox[0].message)
        self.assertEqual(self.session.data_page, 0)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_selecting_a_menu_option_records_it_and_starts_at_page_one(self, mock_actor):
        mock_actor.return_value = self.actor
        self.session.open_data_list("2")
        self.session.advance_page(40)

        with patch.dict(
            "care_im_wrapper.conversation.menus._MAIN_MENU",
            {"2": _option(lambda a, s: _page(["m"]))},
        ):
            self._run("2")

        self.assertEqual(self.session.data_menu_choice, "2")
        self.assertEqual(self.session.data_page, 0)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_next_advances_the_page_and_re_runs_the_open_option(self, mock_actor):
        mock_actor.return_value = self.actor
        self.session.open_data_list("2")
        seen = {}

        def fetcher(actor, session):
            seen["offset"] = current_offset(session)
            return _page(["m"], number=session.data_page, has_next=True, offset=current_offset(session))

        with patch.dict(
            "care_im_wrapper.conversation.menus._MAIN_MENU",
            {"2": _option(fetcher)},
        ):
            self._run("n")

        self.assertEqual(seen["offset"], 0)
        self.assertEqual(self.session.data_page, 1)
        self.assertEqual(self.session.data_menu_choice, "2")

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_paging_past_the_end_steps_back_and_says_so(self, mock_actor):
        mock_actor.return_value = self.actor
        self.session.open_data_list("2")
        self.session.advance_page(10)

        def fetcher(actor, session):
            return _page([], number=session.data_page, offset=current_offset(session))

        with patch.dict(
            "care_im_wrapper.conversation.menus._MAIN_MENU",
            {"2": _option(fetcher)},
        ):
            outbox = self._run("n")

        self.assertIn("already on the last page", outbox[0].message)
        # Stepped back to the page that does exist, rather than stranding the session.
        self.assertEqual(self.session.data_page, 1)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_logout_clears_paging_state(self, mock_actor):
        mock_actor.return_value = self.actor
        self.session.open_data_list("2")
        self.session.advance_page(30)

        self._run("0")

        self.assertEqual(self.session.data_page, 0)
        self.assertEqual(self.session.data_menu_choice, "")


class RecordNumberingAcrossPagesTests(TestCase):
    """The two stacks that page a list: source rows to resume the fetch, records to number it.

    They differ only for a grouped fetcher, where one record carries several source rows.
    """

    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _shown(self, rows: int, records: int) -> None:
        self.session.record_shown(rows, records)
        self.session.advance_page(self.session.next_offset())

    def test_the_offset_follows_rows_and_the_numbering_follows_records(self):
        """One prescription of four medications is four rows but a single printed entry."""
        self._shown(rows=5, records=2)

        self.assertEqual(current_offset(self.session), 5)
        self.assertEqual(current_record_offset(self.session), 2)

    def test_the_count_accumulates_over_several_pages(self):
        self._shown(rows=5, records=2)
        self._shown(rows=3, records=3)

        self.assertEqual(current_record_offset(self.session), 5)

    def test_paging_back_restores_the_earlier_count(self):
        self._shown(rows=5, records=2)
        self._shown(rows=3, records=3)

        self.session.back_page()

        self.assertEqual(current_offset(self.session), 5)
        self.assertEqual(current_record_offset(self.session), 2)

    def test_opening_a_list_starts_the_count_over(self):
        self._shown(rows=5, records=2)

        self.session.open_data_list("2")

        self.assertEqual(current_record_offset(self.session), 0)
        self.assertEqual(self.session.data_records_shown, 0)

    def test_an_ungrouped_page_keeps_the_two_in_step(self):
        """`record_shown` defaults the record count to the row count, so a flat list is
        numbered exactly as it was before the record stack existed."""
        self.session.record_shown(4)
        self.session.advance_page(self.session.next_offset())

        self.assertEqual(current_offset(self.session), 4)
        self.assertEqual(current_record_offset(self.session), 4)
