"""The shared pagination core every data fetcher goes through."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.pagination import (
    Page,
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
