"""The shared pagination core every data fetcher goes through."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.pagination import (
    Page,
    current_record_offset,
    fit_to_budget,
    map_page,
    paginate,
    paginate_or_raise,
    scan_bound,
)

ROWS = list(range(25))


def _session(offsets: list[int] | None = None) -> SimpleNamespace:
    """Paging state is a stack of absolute offsets; its length is the page number."""
    return SimpleNamespace(data_offsets=list(offsets or []), data_shown=0)


class PaginateTests(SimpleTestCase):
    def test_first_page_window(self):
        page = paginate(ROWS, 0, 10)

        self.assertEqual(page.records, list(range(10)))
        self.assertEqual(page.number, 0)
        self.assertTrue(page.has_next)
        self.assertFalse(page.has_previous)

    def test_middle_page_window(self):
        page = paginate(ROWS, 1, 10)

        self.assertEqual(page.records, list(range(10, 20)))
        self.assertTrue(page.has_next)
        self.assertTrue(page.has_previous)

    def test_last_partial_page(self):
        page = paginate(ROWS, 2, 10)

        self.assertEqual(page.records, list(range(20, 25)))
        self.assertFalse(page.has_next)

    def test_last_page_of_an_exact_multiple_does_not_claim_a_next_page(self):
        """20 rows at a size of 10: page 1 is full but there is nothing after it."""
        page = paginate(list(range(20)), 1, 10)

        self.assertEqual(len(page), 10)
        self.assertFalse(page.has_next)

    def test_page_past_the_end_is_empty_rather_than_an_error(self):
        page = paginate(ROWS, 9, 10)

        self.assertEqual(page.records, [])
        self.assertFalse(page.has_next)

    def test_negative_page_clamps_to_the_first(self):
        self.assertEqual(paginate(ROWS, -5, 10).number, 0)

    def test_fetches_exactly_one_row_beyond_the_page(self):
        """The whole optimisation: has_next comes from a sentinel row, never a COUNT."""
        seen = {}

        class Recorder(list):
            def __getitem__(self, item):
                seen["slice"] = item
                return list.__getitem__(self, item)

        paginate(Recorder(ROWS), 2, 10)

        self.assertEqual(seen["slice"], slice(20, 31))

    def test_display_number_is_one_based(self):
        self.assertEqual(paginate(ROWS, 0, 10).display_number, 1)
        self.assertEqual(paginate(ROWS, 3, 10).display_number, 4)

    def test_is_paginated_only_when_there_is_somewhere_to_go(self):
        self.assertFalse(paginate([1, 2], 0, 10).is_paginated)
        self.assertTrue(paginate(ROWS, 0, 10).is_paginated)
        self.assertTrue(paginate(ROWS, 2, 10).is_paginated)


class PageSequenceProtocolTests(SimpleTestCase):
    """A Page stands in for the plain list it replaced, so existing callers keep working."""

    def test_indexing(self):
        self.assertEqual(paginate(ROWS, 0, 10)[0], 0)

    def test_slicing(self):
        self.assertEqual(paginate(ROWS, 0, 10)[:3], [0, 1, 2])

    def test_len_and_iteration(self):
        page = paginate(ROWS, 2, 10)
        self.assertEqual(len(page), 5)
        self.assertEqual(list(page), list(range(20, 25)))

    def test_truthiness_follows_the_records(self):
        self.assertTrue(paginate(ROWS, 0, 10))
        self.assertFalse(paginate(ROWS, 9, 10))


class PaginateOrRaiseTests(SimpleTestCase):
    def test_empty_first_page_means_no_data(self):
        with self.assertRaises(NoDataError):
            paginate_or_raise([], _session())

    def test_empty_later_page_returns_empty_instead_of_raising(self):
        """Paging past the end is a navigation problem, not "you have no records" -- the."""
        page = paginate_or_raise([], _session([10, 20, 30]))

        self.assertEqual(page.records, [])
        self.assertEqual(page.number, 3)

    def test_reads_the_page_off_the_session(self):
        page = paginate_or_raise(ROWS, _session([10]), 10)

        self.assertEqual(page.number, 1)
        self.assertEqual(page.offset, 10)
        self.assertEqual(page.records, list(range(10, 20)))


class MapPageTests(SimpleTestCase):
    def test_rebuilds_records_and_keeps_metadata(self):
        page = paginate(ROWS, 1, 10)

        mapped = map_page(page, lambda r: f"r{r}")

        self.assertEqual(mapped.records[0], "r10")
        self.assertEqual(mapped.number, page.number)
        self.assertEqual(mapped.has_next, page.has_next)
        self.assertEqual(mapped.page_size, page.page_size)

    def test_drops_rows_the_builder_rejects(self):
        page = paginate(list(range(10)), 0, 10)

        mapped = map_page(page, lambda r: None if r % 2 else r)

        self.assertEqual(mapped.records, [0, 2, 4, 6, 8])

    def test_a_fully_rejected_page_still_reports_its_position(self):
        page = paginate(ROWS, 1, 10)

        mapped = map_page(page, lambda _r: None)

        self.assertEqual(mapped.records, [])
        self.assertEqual(mapped.number, 1)
        self.assertTrue(mapped.has_next)


class ScanBoundTests(SimpleTestCase):
    """Fetchers that post-process before paginating must widen their scan as the reader."""

    def test_grows_with_the_page(self):
        first = scan_bound(_session(), 5, 10)
        second = scan_bound(_session([10]), 5, 10)
        third = scan_bound(_session([10, 20]), 5, 10)

        self.assertLess(first, second)
        self.assertLess(second, third)

    def test_covers_every_page_up_to_the_current_one(self):
        self.assertGreaterEqual(scan_bound(_session([10, 20]), 1, 10), 30)


class PageConstructionTests(SimpleTestCase):
    def test_has_previous_is_derived_not_stored(self):
        self.assertFalse(Page(records=[], number=0, page_size=10, has_next=False).has_previous)
        self.assertTrue(Page(records=[], number=1, page_size=10, has_next=False).has_previous)


def _render(rows: list[int]) -> str:
    """Two lines per record, so a line budget and a character budget can be told apart."""
    return "\n".join(f"record {r}\n  detail" for r in rows)


class FitToBudgetTests(SimpleTestCase):
    def _page(self, count: int, *, weights: tuple[int, ...] = (), offset: int = 0) -> Page:
        return Page(
            records=list(range(count)),
            number=0,
            page_size=10,
            has_next=False,
            offset=offset,
            source_weights=weights,
        )

    def test_a_page_that_fits_is_left_alone(self):
        page = self._page(3)

        self.assertIs(fit_to_budget(page, _render, 10**6, 100), page)

    def test_the_surplus_moves_to_the_next_page(self):
        fitted = fit_to_budget(self._page(10), _render, len(_render([0, 1])), 100)

        self.assertEqual(len(fitted.records), 2)
        self.assertTrue(fitted.has_next)

    def test_the_line_budget_binds_an_ungrouped_page(self):
        """A flat record is a line or two, so keeping the page above the fold costs little."""
        fitted = fit_to_budget(self._page(10), _render, 10**6, 6)

        self.assertEqual(len(fitted.records), 3)

    def test_the_line_budget_is_dropped_for_a_grouped_page(self):
        """A grouped record clears the fold on its own; holding to the budget would spend a
        page per record with the character budget barely touched."""
        fitted = fit_to_budget(self._page(10, weights=(4,) + (1,) * 9), _render, 10**6, 6)

        self.assertEqual(len(fitted.records), 10)

    def test_a_grouped_page_still_obeys_the_character_budget(self):
        fitted = fit_to_budget(self._page(10, weights=(4,) + (1,) * 9), _render, len(_render([0, 1])), 6)

        self.assertEqual(len(fitted.records), 2)

    def test_one_record_over_budget_lands_alone_rather_than_dragging_a_second_along(self):
        """The floor is 1: a record too big for the page is clamped by the renderer, and it
        is the only one there. A floor of 2 put a second record on and cut both."""
        fitted = fit_to_budget(self._page(10), _render, 1, 100, min_records=1)

        self.assertEqual(len(fitted.records), 1)


class DisplayStartTests(SimpleTestCase):
    """The number printed beside the first record of a page."""

    def test_an_ungrouped_page_continues_from_the_offset(self):
        page = Page(records=[1], number=1, page_size=5, has_next=False, offset=5)

        self.assertEqual(page.display_start, 6)

    def test_a_grouped_page_continues_from_the_records_shown(self):
        """`offset` counts source rows there -- medications, not the prescriptions on screen --
        so numbering follows the records actually printed."""
        page = Page(
            records=[1],
            number=1,
            page_size=5,
            has_next=False,
            offset=5,
            record_offset=2,
            source_weights=(4, 1),
        )

        self.assertEqual(page.display_start, 3)

    def test_a_grouped_first_page_starts_at_one(self):
        page = Page(records=[1], number=0, page_size=5, has_next=True, source_weights=(4,))

        self.assertEqual(page.display_start, 1)


class CurrentRecordOffsetTests(SimpleTestCase):
    def test_no_history_means_nothing_has_been_shown(self):
        self.assertEqual(current_record_offset(SimpleNamespace(data_record_offsets=[])), 0)

    def test_the_current_page_is_the_top_of_the_stack(self):
        self.assertEqual(current_record_offset(SimpleNamespace(data_record_offsets=[1, 4])), 4)

    def test_a_session_predating_the_field_numbers_from_the_start(self):
        """An in-flight session upgraded mid-conversation has no stack yet."""
        self.assertEqual(current_record_offset(SimpleNamespace()), 0)
