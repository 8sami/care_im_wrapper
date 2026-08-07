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

    def test_a_paged_reply_hands_the_rows_over_to_buttons(self):
        [sent] = self._send(content="DATA", page=_page(has_next=True))

        self.assertEqual(sent.message.interactive.type, InteractiveType.REPLY_BUTTONS)
        self.assertEqual([b["id"] for b in sent.message.interactive.action_data], ["page_next", "page_menu"])

    def test_paging_falls_back_to_rows_when_the_provider_has_no_buttons(self):
        [sent] = self._send(content="DATA", page=_page(has_next=True), limits=channel_limits(max_buttons=0))

        self.assertEqual(sent.message.interactive.type, InteractiveType.LIST)
        self.assertEqual([r["id"] for r in sent.message.interactive.action_data[0]["rows"]][0], "page_next")
        # ...and the typed commands appear, since there is no button to press instead.
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

    def test_the_options_are_not_duplicated_into_the_interactive_body(self):
        [sent] = self._send()

        self.assertEqual(sent.message.interactive.body, "Which one?")

    def test_paging_arrives_as_its_own_buttons_message(self):
        first, second = self._send(page=_page(number=1, has_next=True))

        self.assertEqual(first.message.interactive.type, InteractiveType.LIST)
        self.assertEqual([r["id"] for r in first.message.interactive.action_data[0]["rows"]][-1], "0")
        self.assertEqual(second.message.interactive.type, InteractiveType.REPLY_BUTTONS)
        # No Menu button: the rows already end in Back.
        self.assertEqual([b["id"] for b in second.message.interactive.action_data], ["page_prev", "page_next"])
        self.assertFalse(second.pace)

    def test_without_buttons_the_paging_rejoins_the_rows(self):
        [sent] = self._send(page=_page(has_next=True), limits=channel_limits(max_buttons=0))

        rows = [r["id"] for r in sent.message.interactive.action_data[0]["rows"]]
        self.assertEqual(rows, ["page_next", "thing_0", "thing_1", "0"])

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
