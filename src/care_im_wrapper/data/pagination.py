"""Shared pagination for every patient-data fetcher."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.settings import plugin_settings


@dataclass(frozen=True)
class Page:
    """One window of records plus just enough context to navigate."""

    records: list[Any]
    number: int  # 0-based, for display only
    page_size: int
    has_next: bool
    offset: int = 0
    # Source rows per record, for grouped fetchers. Empty means one row per record.
    source_weights: tuple[int, ...] = ()
    # The row after this page, so a grouping fetcher can tell if the window split a group.
    next_record: Any = None

    def consumed(self, count: int | None = None) -> int:
        """Source rows behind the first `count` records -- what the offset advances by."""
        limit = len(self.records) if count is None else count
        if not self.source_weights:
            return limit
        return sum(self.source_weights[:limit])

    @property
    def has_previous(self) -> bool:
        return self.number > 0

    @property
    def display_number(self) -> int:
        """1-based, for anything a human reads."""
        return self.number + 1

    @property
    def is_paginated(self) -> bool:
        """Whether paging controls are worth showing at all."""
        return self.has_next or self.has_previous

    # Sequence protocol, so a Page is a drop-in for the list it replaced.
    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def __getitem__(self, index: int | slice) -> Any:
        return self.records[index]

    def __bool__(self) -> bool:
        return bool(self.records)


def default_page_size() -> int:
    return int(plugin_settings.DATA_FETCH_LIMIT)


def current_offset(session: Any) -> int:
    """Where the session's current page starts. An absolute offset, not a page index, because
    trimmed pages vary in size."""
    offsets = getattr(session, "data_offsets", None) or []
    return max(0, int(offsets[-1])) if offsets else 0


def current_page_number(session: Any) -> int:
    """1-based position in the reader's paging history, for display."""
    return max(0, len(getattr(session, "data_offsets", None) or []))


def paginate(source: Sequence[Any] | Any, number: int, page_size: int | None = None, offset: int | None = None) -> Page:
    """Slices `source` -- a queryset or an already-materialised sequence -- into one page."""
    size = page_size or default_page_size()
    number = max(0, int(number))
    start = number * size if offset is None else max(0, int(offset))
    # +1 sentinel: present means there is at least one more record after this page.
    window = list(source[start : start + size + 1])
    return Page(
        records=window[:size],
        number=number,
        page_size=size,
        has_next=len(window) > size,
        offset=start,
        next_record=window[size] if len(window) > size else None,
    )


def paginate_or_raise(source: Sequence[Any] | Any, session: Any, page_size: int | None = None) -> Page:
    """`paginate` for the session's current page. Only an empty *first* page is NoDataError;
    an empty later page means the reader walked off the end."""
    start = current_offset(session)
    page = paginate(source, current_page_number(session), page_size, offset=start)
    if not page.records and start == 0:
        raise NoDataError
    return page


def fit_to_budget(
    page: Page,
    render: Callable[[list[Any]], str],
    budget: int,
    max_lines: int | None = None,
    min_records: int = 1,
) -> Page:
    """Trims `page` to the leading records that fit in `budget` characters."""

    def fits(count: int) -> bool:
        text = render(page.records[:count])
        if len(text) > budget:
            return False
        return max_lines is None or text.count("\n") + 1 <= max_lines

    records = page.records
    if not records or fits(len(records)):
        return page

    # Floor, never more than what is actually there.
    floor = max(1, min(min_records, len(records)))
    low, high = floor, len(records)  # low is the floor, taken whether it fits or not
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1

    return replace(
        page,
        records=records[:low],
        source_weights=page.source_weights[:low],
        has_next=page.has_next or low < len(records),
    )


def map_page(page: Page, builder: Callable[[Any], Any | None]) -> Page:
    """Same window, records rebuilt through `builder`; rows mapping to None are dropped."""
    records = [built for built in (builder(row) for row in page.records) if built is not None]
    return replace(page, records=records, source_weights=())


def scan_bound(session: Any, factor: int, page_size: int | None = None) -> int:
    """Rows to scan for a fetcher that post-processes before paginating (dedupe, drops)."""
    size = page_size or default_page_size()
    return (current_offset(session) + size) * factor + 1
