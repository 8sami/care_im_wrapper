"""Composing a reply into the messages a provider will take.

The rules under test are the ones every send site used to re-decide for itself: where paging
goes, when a reply splits in two, and that a picker's rows and its plain-text fallback always
describe the same things.
"""

from django.test import SimpleTestCase

from care_im_wrapper.conversation.messages import InteractiveType
from care_im_wrapper.conversation.replies import (
    Choice,
    choices_as_text,
    enumerate_choices,
    fit_to_rows,
    menu_reply,
    picker_reply,
    row,
)
from care_im_wrapper.data.pagination import Page
from tests.utils import channel_limits

PHONE = "+919876543210"

MENU_ROWS = [row("1", "Appointments"), row("0", "Logout", "End this session")]


def _page(*, number=0, has_next=False, offset=0):
    return Page(records=["a"], number=number, page_size=10, has_next=has_next, offset=offset)


def _choices(count=2, start=1):
    return enumerate_choices(
        [(f"Name {i}", f"Detail {i}", {"id": i}) for i in range(count)], prefix="thing", start=start
    )


class ChoiceTests(SimpleTestCase):
    def test_a_choice_is_offered_and_stored_as_the_same_thing(self):
        choice = _choices(1)[0]

        self.assertEqual(choice.row, {"id": "thing_0", "title": "Name 0", "description": "Detail 0"})
        self.assertEqual(choice.candidate["row_id"], "thing_0")
        self.assertEqual(choice.candidate["token"], "1")
        self.assertEqual(choice.candidate["title"], "Name 0")

    def test_a_stored_candidate_round_trips_back_into_a_choice(self):
        original = _choices(1)[0]

        rebuilt = Choice.from_candidate(original.candidate)

        self.assertEqual(rebuilt.row, original.row)
        self.assertEqual(rebuilt.line, original.line)

    def test_an_empty_description_is_left_off_the_row_entirely(self):
        self.assertEqual(row("x", "Title"), {"id": "x", "title": "Title"})

    def test_row_ids_restart_per_page_but_numbers_carry_on(self):
        second_page = _choices(2, start=3)

        self.assertEqual([c.row_id for c in second_page], ["thing_0", "thing_1"])
        self.assertEqual([c.token for c in second_page], ["3", "4"])

    def test_the_written_out_options_are_numbered_from_the_same_tokens(self):
        text = choices_as_text("Things", _choices(2, start=3), 4096)

        self.assertIn("3.  Name 0", text)
        self.assertIn("4.  Name 1", text)


class FitToRowsTests(SimpleTestCase):
    """How long a page of a picker may be: as long as the list has rows to draw it with."""

    def _page(self, count, *, number=0, has_next=False):
        return Page(records=list(range(count)), number=number, page_size=10, has_next=has_next)

    def test_a_page_that_fits_is_left_alone(self):
        page = self._page(4)

        self.assertIs(fit_to_rows(page, channel_limits(max_rows=10), reserved_rows=1), page)

    def test_the_surplus_moves_to_the_next_page_instead_of_off_the_end(self):
        """Ten records cannot be ten rows: Back takes one, and the move to what was trimmed
        takes another."""
        fitted = fit_to_rows(self._page(10), channel_limits(max_rows=10), reserved_rows=1)

        self.assertEqual(len(fitted.records), 8)
        self.assertTrue(fitted.has_next)

    def test_a_second_page_pays_for_the_way_back_to_the_first(self):
        fitted = fit_to_rows(self._page(10, number=1, has_next=True), channel_limits(max_rows=10), reserved_rows=1)

        self.assertEqual(len(fitted.records), 7)

    def test_the_rows_a_picker_owns_come_out_of_the_same_budget(self):
        """All and Back, or whatever else the caller puts in the list beside the records."""
        fitted = fit_to_rows(self._page(10), channel_limits(max_rows=10), reserved_rows=3)

        self.assertEqual(len(fitted.records), 6)

    def test_a_list_too_small_to_hold_anything_still_offers_one_record(self):
        fitted = fit_to_rows(self._page(10), channel_limits(max_rows=2), reserved_rows=3)

        self.assertEqual(len(fitted.records), 1)
        self.assertTrue(fitted.has_next)


class MenuReplyTests(SimpleTestCase):
    def _send(self, limits=None, **kwargs):
        return menu_reply(
            PHONE,
            limits or channel_limits(),
            prompt="Please choose an option:",
            rows=MENU_ROWS,
            button_label="View Menu",
            section_title="Menu",
            **kwargs,
        )

    def test_an_unpaged_reply_keeps_the_menu_list(self):
        [sent] = self._send(content="DATA")

        self.assertEqual(sent.message.interactive.type, InteractiveType.LIST)
        self.assertEqual([r["id"] for r in sent.message.interactive.action_data[0]["rows"]], ["1", "0"])

    def test_a_paged_reply_leads_the_menu_rows_with_paging(self):
        [sent] = self._send(content="DATA", page=_page(has_next=True))

        self.assertEqual(sent.message.interactive.type, InteractiveType.LIST)
        self.assertEqual([r["id"] for r in sent.message.interactive.action_data[0]["rows"]], ["page_next", "1", "0"])
        self.assertIn("Send *n*", sent.message.text)

    def test_paging_never_leaves_on_a_message_of_its_own(self):
        sent = self._send(content="DATA", page=_page(number=1, has_next=True))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].message.interactive.type, InteractiveType.LIST)

    def test_paging_rows_give_way_to_the_menu_when_the_list_is_full(self):
        limits = channel_limits(max_rows=2)

        [sent] = self._send(content="DATA", page=_page(number=1, has_next=True), limits=limits)

        rows = [r["id"] for r in sent.message.interactive.action_data[0]["rows"]]
        self.assertEqual(rows, ["1", "0"])
        self.assertIn("Send *n*", sent.message.text)

    def test_a_body_over_the_limit_splits_and_the_menu_survives_in_both_halves(self):
        limits = channel_limits(interactive_body=40)

        first, second = self._send(content="A" * 60, limits=limits)

        self.assertIsNone(first.message.interactive)
        self.assertTrue(first.pace)
        self.assertEqual(second.message.interactive.type, InteractiveType.LIST)
        self.assertIn("1. Appointments", second.message.text)
        self.assertFalse(second.pace)


class PickerReplyTests(SimpleTestCase):
    def _send(self, limits=None, **kwargs):
        return picker_reply(
            PHONE,
            limits or channel_limits(),
            prompt="Which one?",
            choices=_choices(),
            button_label="Select",
            section_title="Things",
            **kwargs,
        )

    def test_the_rows_are_the_choices_plus_a_way_back(self):
        [sent] = self._send()

        self.assertEqual(
            [r["id"] for r in sent.message.interactive.action_data[0]["rows"]], ["thing_0", "thing_1", "0"]
        )

    def test_the_fallback_lists_the_options_without_being_told_to(self):
        """A picker whose plain text lost its options is a question with no visible answers."""
        [sent] = self._send()

        self.assertIn("1.  Name 0", sent.message.text)
        self.assertIn("2.  Name 1", sent.message.text)
        self.assertIn("Which one?", sent.message.text)

    def test_a_page_sized_to_the_body_is_not_thrown_away_by_the_paging_hint(self):
        """The regression: a page trimmed to fit beside its prompt, then pushed over the cap
        by the hint joined in after it, used to leave the reader a prompt and no records."""
        limits = channel_limits(interactive_body=200)
        content = "D" * (limits.interactive_body - len("Which one?") - 4)

        [sent] = self._send(limits=limits, content=content, page=_page(has_next=True))

        body = sent.message.interactive.body
        self.assertIn(content, body)
        self.assertIn("Which one?", body)
        self.assertLessEqual(len(body), limits.interactive_body)
        # The hint is what gives way, and it is still in the plain-text half.
        self.assertNotIn("Send *n*", body)
        self.assertIn("Send *n*", sent.message.text)

    def test_the_typed_commands_give_way_before_the_page_number_does(self):
        """A reader deep in a list with no position has no idea where they are, and the
        commands are duplicated by the rows. Sized so only the full hint is over the cap."""
        limits = channel_limits(interactive_body=200)
        content = "D" * (limits.interactive_body - len("Which one?") - len("Page 2") - 8)

        [sent] = self._send(limits=limits, content=content, page=_page(number=1, has_next=True))

        body = sent.message.interactive.body
        self.assertIn(content, body)
        self.assertIn("Page 2", body)
        self.assertNotIn("Send *n*", body)
        self.assertLessEqual(len(body), limits.interactive_body)

    def test_a_body_over_the_cap_even_without_the_hint_falls_back_to_the_prompt(self):
        limits = channel_limits(interactive_body=200)

        [sent] = self._send(limits=limits, content="D" * 500, page=_page(has_next=True))

        self.assertEqual(sent.message.interactive.body, "Which one?")

    def test_the_options_are_not_duplicated_into_the_interactive_body(self):
        [sent] = self._send()

        self.assertEqual(sent.message.interactive.body, "Which one?")

    def test_paging_leads_the_rows_and_back_still_ends_them(self):
        [sent] = self._send(page=_page(number=1, has_next=True))

        rows = [r["id"] for r in sent.message.interactive.action_data[0]["rows"]]
        self.assertEqual(rows, ["page_next", "page_prev", "thing_0", "thing_1", "0"])

    def test_a_paged_picker_is_still_one_message(self):
        sent = self._send(page=_page(has_next=True))

        self.assertEqual(len(sent), 1)
        rows = [r["id"] for r in sent[0].message.interactive.action_data[0]["rows"]]
        self.assertEqual(rows, ["page_next", "thing_0", "thing_1", "0"])

    def test_the_typed_paging_hint_rides_along_with_the_rows(self):
        [sent] = self._send(page=_page(has_next=True))

        self.assertIn("Page 1", sent.message.text)
        self.assertIn("Send *n*", sent.message.text)

    def test_content_above_the_options_shares_the_body_when_it_fits(self):
        [sent] = self._send(content="Your medications:")

        self.assertIn("Your medications:", sent.message.interactive.body)
        self.assertIn("Which one?", sent.message.interactive.body)
        # The written-out options stay in the fallback rather than doubling the body.
        self.assertNotIn("Name 0", sent.message.interactive.body)
        self.assertIn("Name 0", sent.message.text)

    def test_content_too_long_for_the_body_leaves_only_the_prompt_behind(self):
        [sent] = self._send(content="A" * 60, limits=channel_limits(interactive_body=40))

        self.assertEqual(sent.message.interactive.body, "Which one?")
        self.assertIn("A" * 60, sent.message.text)
