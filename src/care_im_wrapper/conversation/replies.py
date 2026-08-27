"""Turning what a handler wants to say into the messages a provider will actually take.

A reply is prose plus one way to act on it: selectable list rows, or reply buttons. An
interactive message carries one or the other but never both, and its body is capped far below
the plain-text limit -- so composing a reply is mostly deciding how to split it. Every send
site used to make that decision for itself, which is how they drifted apart. It is made here
once, against `ChannelLimits`, so it holds for any provider; the handlers only say what they
want shown.

Two shapes cover every reply:

``menu_reply``    the rows are chrome -- the menu, offered next to whatever was just shown.
``picker_reply``  the rows are the point -- an encounter, a prescription, a report to choose.

Either way paging rides in the rows, above whatever the list already holds, never as a
message of its own.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from care_im_wrapper.conversation.messages import (
    InteractivePayload,
    InteractiveType,
    Outbound,
    OutboundMessage,
)
from care_im_wrapper.conversation.renderers import numbered_block, titled
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data.pagination import Page
from care_im_wrapper.messaging.limits import ChannelLimits

logger = logging.getLogger(__name__)

Row = dict[str, str]

PAGE_NEXT_ID = "page_next"
PAGE_PREV_ID = "page_prev"
BACK_ID = "0"


def _fits_body(limits: ChannelLimits, text: str) -> bool:
    return len(text) <= limits.interactive_body


@dataclass(frozen=True)
class Choice:
    """One selectable item, in the two forms it has to exist in at once.

    `row_id` is what an interactive provider posts back; `token` is the number printed beside
    the row for a reader who types instead. Both are minted here alongside the payload the
    handler will need when the choice comes back, so the rows on screen and the candidates
    stored on the session can never come to describe different things.
    """

    row_id: str
    token: str
    title: str
    description: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def row(self) -> Row:
        """The interactive row this choice is offered as."""
        return row(self.row_id, self.title, self.description)

    @property
    def candidate(self) -> dict[str, Any]:
        """What the session stores: how to find it again, and what it looked like on screen.

        Carrying the title and description means a reply that keeps the picker open can redraw
        the same rows without re-fetching the records behind them.
        """
        return {
            **self.payload,
            "row_id": self.row_id,
            "token": self.token,
            "title": self.title,
            "description": self.description,
        }

    @classmethod
    def from_candidate(cls, candidate: dict[str, Any]) -> Choice:
        """Rebuilds an offered choice from what the session stored."""
        return cls(
            row_id=candidate["row_id"],
            token=candidate["token"],
            title=candidate["title"],
            description=candidate.get("description", ""),
            payload=candidate,
        )

    @property
    def line(self) -> str:
        """The plain-text form, for a provider that cannot draw rows."""
        return titled(self.title, self.description or None)


def enumerate_choices(items: Iterable[tuple[str, str, dict[str, Any]]], *, prefix: str, start: int) -> list[Choice]:
    """Numbers a picker's items: the row id by position, the printed token by page offset.

    Row ids are positional because that is all the provider has to hand back; tokens continue
    across pages because that is what the reader sees printed beside the row.
    """
    return [
        Choice(
            row_id=f"{prefix}_{index}", token=str(start + index), title=title, description=description, payload=payload
        )
        for index, (title, description, payload) in enumerate(items)
    ]


def row(row_id: str, title: str, description: str = "") -> Row:
    """One list row or reply button. An empty description is left out entirely, so the
    provider drops the line rather than rendering a blank one."""
    built = {"id": row_id, "title": title}
    if description:
        built["description"] = description
    return built


_PREVIOUS = (PAGE_PREV_ID, "prev_page")
_NEXT = (PAGE_NEXT_ID, "next_page")


def _moves(page: Page | None, order: tuple[tuple[str, str], ...]) -> list[Row]:
    """The paging moves this page actually has, in the given order."""
    if page is None or not page.is_paginated:
        return []
    available = {_PREVIOUS: page.has_previous, _NEXT: page.has_next}
    return [row(row_id, _msg(key)) for row_id, key in order if available[(row_id, key)]]


def paging_rows(page: Page | None) -> list[Row]:
    """Paging as list rows, which is the only place it goes. Forward first: a reader on page
    one is far likelier to want the next page than a previous one."""
    return _moves(page, (_NEXT, _PREVIOUS))


def paging_hint(page: Page | None) -> str:
    """Which page this is and the typed commands that move off it. Works on any provider."""
    if page is None or not page.is_paginated:
        return ""
    parts = [_msg("page_indicator", page=page.display_number)]
    if page.has_next:
        parts.append(_msg("page_hint_next"))
    if page.has_previous:
        parts.append(_msg("page_hint_prev"))
    return "\n".join(parts)


def fit_to_rows(page: Page, limits: ChannelLimits, *, reserved_rows: int) -> Page:
    """Trims `page` to the records the provider's list has rows left for.

    `reserved_rows` is what the non-record rows take out of the budget -- Back, and any
    leading row such as All. Paging comes out of the same budget, since it is rows in this
    same list.
    """
    max_records = max(1, limits.max_rows - reserved_rows - int(page.has_previous))
    # Trimming a page is what gives it a next page, so the row that move needs costs the same
    # either way -- charge for it before deciding how many records are left affordable.
    if page.has_next or len(page.records) > max_records:
        max_records = max(1, max_records - 1)
    if len(page.records) <= max_records:
        return page
    return replace(
        page,
        records=page.records[:max_records],
        source_weights=page.source_weights[:max_records],
        has_next=True,
    )


def rows_as_text(rows: Sequence[Row]) -> str:
    """Menu rows as the plain text a non-interactive provider shows instead. Menu row ids
    are the numbers the reader types, so they double as the list markers."""
    return "\n".join(f"{item['id']}. {item['title']}" for item in rows)


def join(*parts: str) -> str:
    """Blocks separated by a blank line, skipping the ones that are empty."""
    return "\n\n".join(part for part in parts if part)


def _list_payload(body: str, rows: Sequence[Row], button_label: str, section_title: str) -> InteractivePayload:
    return InteractivePayload(
        type=InteractiveType.LIST,
        body=body,
        button_label=button_label,
        action_data=[{"title": section_title, "rows": list(rows)}],
    )


def menu_reply(
    phone_number: str,
    limits: ChannelLimits,
    *,
    prompt: str,
    rows: Sequence[Row],
    button_label: str,
    section_title: str,
    content: str = "",
    page: Page | None = None,
    pace: bool = True,
) -> list[Outbound]:
    """A reply carrying the menu: whatever was just fetched, then how to move on.

    `content` is that fetched data, or the explanation for why there is none. `prompt` is the
    line above the controls -- the scope the reader is in, and the invitation to choose.
    """
    hint = paging_hint(page)
    with_paging = [*paging_rows(page), *rows]
    rows = with_paging if len(with_paging) <= limits.max_rows else list(rows)

    body = join(content, hint, prompt)
    full_text = join(body, rows_as_text(rows))
    payload = _list_payload(body, rows, button_label, section_title)

    if _fits_body(limits, body):
        return [Outbound(phone_number, OutboundMessage(text=full_text, interactive=payload), pace=pace)]

    # Over the body limit the send degrades to plain text and the rows would go with it, so
    # the data leaves on its own first and the menu follows behind a prompt that always fits.
    trailing = _list_payload(prompt, rows, button_label, section_title)
    return [
        Outbound(phone_number, OutboundMessage(text=join(content, hint)), pace=pace),
        Outbound(
            phone_number, OutboundMessage(text=join(prompt, rows_as_text(rows)), interactive=trailing), pace=False
        ),
    ]


def choices_as_text(header: str, choices: Sequence[Choice], max_chars: int) -> str:
    """The offered choices written out, numbered from the token they were offered under.

    Derived from the same choices the rows are built from, so the two can never come to
    describe different things -- and a caller cannot forget to supply it.
    """
    if not choices:
        return ""
    return numbered_block(header, [choice.line for choice in choices], max_chars, int(choices[0].token))


def picker_reply(
    phone_number: str,
    limits: ChannelLimits,
    *,
    prompt: str,
    choices: Sequence[Choice],
    button_label: str,
    section_title: str,
    leading_rows: Sequence[Row] = (),
    content: str = "",
    options_text: str | None = None,
    page: Page | None = None,
) -> list[Outbound]:
    """A reply whose rows are the answer to it.

    A provider that cannot draw rows gets the options written out instead, derived from the
    choices by default so a picker can never go out as a question with no visible answers.
    `options_text` overrides that only where a caller has a richer rendering to offer.
    `content` is anything shown above them, such as the medications a prescription filter is
    being applied to. Paging, when there is any, is rows in the same list as the choices.
    """
    if options_text is None:
        options_text = choices_as_text(section_title, choices, limits.text_body)

    rows = [*paging_rows(page), *leading_rows, *(choice.row for choice in choices)]
    # Back is the only way out of a picker, so it has to survive the provider's row cap.
    # Reaching here over the cap means the caller sized its page without `fit_to_rows`, and
    # choices are about to be dropped that the reader can then only reach by typing.
    if len(rows) >= limits.max_rows:
        logger.warning(
            "picker_reply: %d rows for a list that takes %d; %d choice(s) will have no row",
            len(rows) + 1,
            limits.max_rows,
            len(rows) + 1 - limits.max_rows,
        )
        rows = rows[: max(0, limits.max_rows - 1)]
    rows.append(row(BACK_ID, _msg("back")))

    hint = paging_hint(page)
    body = join(content, hint, prompt)
    if not _fits_body(limits, body):
        body = prompt
    payload = _list_payload(body, rows, button_label, section_title)

    return [
        Outbound(phone_number, OutboundMessage(text=join(content, options_text, hint, prompt), interactive=payload))
    ]
