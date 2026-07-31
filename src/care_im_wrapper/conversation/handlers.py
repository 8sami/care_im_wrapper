from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from care_im_wrapper.auth.actor import resolve_actor
from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.conversation.menus import _PATIENT_MENU, _STAFF_MENU
from care_im_wrapper.conversation.messages import InteractivePayload, InteractiveType, OutboundMessage
from care_im_wrapper.conversation.renderers import numbered_block, render_patient_search_results
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data import patient_lookup
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import (
    DataFetchError,
    InvalidQueryError,
    MissingContextError,
    NoDataError,
    PermissionDeniedError,
)
from care_im_wrapper.data.pagination import Page, current_offset, fit_to_budget
from care_im_wrapper.documents.delivery import build_document_message
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.documents.service import build_document_url, get_or_create_document_link
from care_im_wrapper.messaging.exceptions import OutboundRateLimitedError
from care_im_wrapper.messaging.registry import (
    get_interactive_body_char_limit,
    get_max_chars,
    get_max_interactive_rows,
    get_max_reply_buttons,
    send_message,
)
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outbound:
    """A message a handler wants delivered, sent after the transaction commits."""

    phone_number: str
    message: OutboundMessage | str
    pace: bool = True


_PAGE_NEXT_TOKENS = frozenset({"n", "next", "page_next"})
_PAGE_PREV_TOKENS = frozenset({"p", "prev", "previous", "page_prev"})


def _paging_step(choice: str) -> int:
    """+1 / -1 for a paging command, 0 for anything else."""
    lowered = choice.strip().lower()
    if lowered in _PAGE_NEXT_TOKENS:
        return 1
    if lowered in _PAGE_PREV_TOKENS:
        return -1
    return 0


_UNBOUNDED_CHARS = 10**9


def _list_line_budget(channel: str) -> int:
    """Lines a paged list may occupy before the client folds it behind a "Read more"."""
    del channel  # single-provider today; kept in the signature so a second one can differ
    reserve = int(plugin_settings.PAGING_FOOTER_RESERVE_LINES)
    return max(1, int(plugin_settings.WHATSAPP_PREVIEW_LINE_LIMIT) - reserve)


def _list_budget(channel: str) -> int:
    """Characters a paged list may occupy, less the paging footer."""
    reserve = int(plugin_settings.PAGING_FOOTER_RESERVE_CHARS)
    return max(1, get_max_chars(channel) - reserve)


def _paging_text(page: Page) -> str:
    """Textual affordance; works on every provider."""
    parts = [_msg("page_indicator", page=page.display_number)]
    if page.has_next:
        parts.append(_msg("page_hint_next"))
    if page.has_previous:
        parts.append(_msg("page_hint_prev"))
    return "\n".join(parts)


def _rows_with_paging(page: Page, menu_rows: list[dict[str, str]], channel: str) -> list[dict[str, str]]:
    """Menu rows with Next/Previous in front, when the provider has room for them."""
    paging_rows = []
    if page.has_next:
        paging_rows.append({"id": "page_next", "title": _msg("next_page")})
    if page.has_previous:
        paging_rows.append({"id": "page_prev", "title": _msg("prev_page")})
    if not paging_rows:
        return menu_rows

    combined = paging_rows + menu_rows
    return combined if len(combined) <= get_max_interactive_rows(channel) else menu_rows


def _menu_rows(menu: dict[str, Any]) -> list[dict[str, str]]:
    """Builds interactive list rows from a menu dict, plus the trailing Logout row."""
    rows = [{"id": key, "title": entry[0]} for key, entry in menu.items()]
    rows.append({"id": "0", "title": _msg("logout")})
    return rows


def _menu_text(rows: list[dict[str, str]]) -> str:
    """Renders menu rows as a numbered plain-text fallback for non-interactive display."""
    return "\n".join(f"{r['id']}. {r['title']}" for r in rows)


def _parse_selection_index(choice: str, prefix: str, *, prefixed_base: int, display_start: int = 1) -> int | None:
    """Resolves a selection reply to a 0-based index, or None if it doesn't parse."""
    if choice.startswith(prefix):
        try:
            return int(choice.removeprefix(prefix)) - prefixed_base
        except ValueError:
            return None
    if choice.isdigit():
        return int(choice) - display_start
    return None


def run_state_machine(phone_number: str, text: str, channel: str) -> None:
    outbox: list[Outbound] = []
    with transaction.atomic():  # pyright: ignore[reportGeneralTypeIssues]
        session, created = ConversationSession.objects.select_for_update().get_or_create(  # pyright: ignore[reportAttributeAccessIssue]
            phone_number=phone_number,
            provider=channel,
        )
        session.record_activity()

        if session.is_in_cooldown():
            outbox.append(Outbound(phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes())))
        else:
            dispatch = {
                ConversationSession.State.NEW: _handle_new,
                ConversationSession.State.AWAITING_YOB: _handle_awaiting_yob,
                ConversationSession.State.AMBIGUOUS: _handle_ambiguous,
                ConversationSession.State.AUTHENTICATED: _handle_authenticated,
                ConversationSession.State.AWAITING_PATIENT_SEARCH: _handle_awaiting_patient_search,
                ConversationSession.State.SELECTING_PATIENT: _handle_selecting_patient,
                ConversationSession.State.SELECTING_DOCUMENT: _handle_selecting_document,
            }
            handler = dispatch.get(session.state)  # pyright: ignore[reportArgumentType]
            if handler:
                handler(session, phone_number, text, channel, outbox)
            else:
                logger.error("run_state_machine: unhandled state %s", session.state)
    _flush(channel, outbox)


def _flush(channel: str, outbox: list[Outbound]) -> None:
    """Sends every queued message now that state is committed and durable."""
    for index, item in enumerate(outbox):
        try:
            send_message(channel, item.phone_number, item.message, pace=item.pace)
        except OutboundRateLimitedError:
            if index == 0:
                raise
            logger.warning(
                "_flush: rate-limited sending item %d/%d to %s on %s after earlier sends; "
                "dropping the rest of this turn.",
                index + 1,
                len(outbox),
                item.phone_number,
                channel,
            )
            return
        except Exception:
            logger.exception(
                "_flush: failed to send item %d/%d to %s on %s; dropping the rest of this turn.",
                index + 1,
                len(outbox),
                item.phone_number,
                channel,
            )
            return


def _handle_new(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found:
        outbox.append(Outbound(phone_number, _msg("not_found")))
        return

    # Serialise all candidates to JSON-safe dicts for storage between turns
    candidates_list: list[dict[str, Any]] = [
        {
            "user_type": i.user_type,
            "user_id": i.user_id,
            "year_of_birth": i.year_of_birth,
            "full_name": i.full_name,
            "phone_number": i.phone_number,
        }
        for i in result.identities
    ]
    session.candidates = candidates_list
    session.state = ConversationSession.State.AWAITING_YOB
    session.save(update_fields=["state", "candidates"])
    outbox.append(Outbound(phone_number, _msg("yob_prompt")))


def _handle_awaiting_yob(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    stripped = text.strip()
    if not stripped.isdigit() or len(stripped) != 4:
        outbox.append(Outbound(phone_number, _msg("yob_invalid")))
        return

    year = int(stripped)
    shortlist = [
        c
        for c in session.candidates  # pyright: ignore[reportGeneralTypeIssues]
        if c.get("year_of_birth") is not None and int(c["year_of_birth"]) == year
    ]
    if not shortlist:
        session.increment_failed_attempt()
        if session.state == ConversationSession.State.COOLDOWN:
            outbox.append(Outbound(phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes())))
        else:
            remaining = int(plugin_settings.MAX_FAILED_ATTEMPTS) - int(session.failed_attempts)  # pyright: ignore[reportOperatorIssue, reportArgumentType]
            outbox.append(Outbound(phone_number, _msg("yob_wrong", remaining=remaining)))
        return

    if len(shortlist) == 1:
        match = shortlist[0]
        session.authenticate(
            user_type=match["user_type"],
            user_id=match["user_id"],
            name=match["full_name"],
            phone=match["phone_number"],
        )
        _send_main_menu(phone_number, match["user_type"], channel, outbox, name=match["full_name"])
        return

    session.candidates = shortlist
    session.state = ConversationSession.State.AMBIGUOUS
    session.save(update_fields=["state", "candidates"])
    _send_candidate_menu(phone_number, shortlist, channel, outbox)


def _handle_ambiguous(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    # candidate_ ids are 1-based (candidate_1 is the first row).
    index = _parse_selection_index(choice, "candidate_", prefixed_base=1)
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore[reportAssignmentType]
    if index is None or not (0 <= index < len(candidates)):
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    match = candidates[index]
    session.authenticate(
        user_type=match["user_type"],
        user_id=match["user_id"],
        name=match["full_name"],
        phone=match["phone_number"],
    )
    _send_main_menu(phone_number, str(match["user_type"]), channel, outbox, name=match["full_name"])


def _handle_authenticated(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    if choice == "0":
        session.logout()
        outbox.append(Outbound(phone_number, _msg("logout_confirm")))
        return

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    menu = _STAFF_MENU if session.user_type == ConversationSession.UserType.STAFF.value else _PATIENT_MENU

    # A paging command re-runs the open option one page along.
    step = _paging_step(choice)
    pending_advance = False
    if step:
        if not session.data_menu_choice:
            outbox.append(Outbound(phone_number, _msg("page_nothing_open")))
            return
        if step < 0 and session.data_page == 0:
            outbox.append(Outbound(phone_number, _msg("page_first")))
            return
        choice = session.data_menu_choice
        if step < 0:
            session.back_page()
        else:
            pending_advance = True

    entry = menu.get(choice)

    if not entry:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    label, fetcher, renderer, document_resolver = entry

    if fetcher is None:
        session.reset_data_page()
        session.state = ConversationSession.State.AWAITING_PATIENT_SEARCH
        session.save(update_fields=["state"])
        outbox.append(Outbound(phone_number, _msg("patient_search_prompt")))
        return

    if not step:
        # Picking an option from the menu restarts at the first page.
        session.open_data_list(choice)

    try:
        if pending_advance:
            session.advance_page(session.next_offset())

        data = fetcher(actor, session)
        page = data if isinstance(data, Page) else None
        if page is not None:
            page = fit_to_budget(
                page,
                lambda rows: renderer(rows, _UNBOUNDED_CHARS, page.offset + 1).text,
                _list_budget(channel),
                _list_line_budget(channel),
                int(plugin_settings.DATA_PAGE_MIN_RECORDS),
            )
            # Source rows, not records: a grouped fetcher folds several rows into one.
            session.record_shown(page.consumed())
        records = page.records if page is not None else data

        if page is not None and not records and page.number > 0:
            # Paged past the end; step back to a page that exists.
            session.back_page()
            outbox.append(Outbound(phone_number, _msg("page_last")))
            return

        if page is None:
            start = 1
            renderer_msg = renderer(records, get_max_chars(channel))
        else:
            start = page.offset + 1
            renderer_msg = renderer(records, _list_budget(channel), start)

        if document_resolver is not None and _enter_document_selection(
            session, choice, records, renderer, phone_number, channel, outbox, start
        ):
            return

        summary = renderer_msg.text
        menu_rows = _menu_rows(menu)
        if page is not None and page.is_paginated:
            summary = f"{summary}\n\n{_paging_text(page)}"
            menu_rows = _rows_with_paging(page, menu_rows, channel)

        greeting = _msg("choose_option")
        menu_items_text = _menu_text(menu_rows)
        full_text = f"{summary}\n\n{greeting}\n\n{menu_items_text}"

        interactive_payload = InteractivePayload(
            type=InteractiveType.LIST,
            body=greeting,
            button_label=_msg("view_menu"),
            action_data=[{"title": _msg("menu_title"), "rows": menu_rows}],
        )

        limit = get_interactive_body_char_limit(channel)

        if len(summary) + len(greeting) > limit:
            # Fallback: Send data as plain text, then menu separately.
            outbox.append(Outbound(phone_number, OutboundMessage(text=summary)))
            outbox.append(
                Outbound(phone_number, OutboundMessage(text=greeting, interactive=interactive_payload), pace=False)
            )
        else:
            # Single message: data + greeting in interactive body (avoiding redundant menu list)
            interactive_payload = InteractivePayload(
                type=interactive_payload.type,
                body=f"{summary}\n\n{greeting}",
                action_data=interactive_payload.action_data,
                button_label=interactive_payload.button_label,
                footer=interactive_payload.footer,
            )
            outbox.append(Outbound(phone_number, OutboundMessage(text=full_text, interactive=interactive_payload)))

    except PermissionDeniedError:
        logger.warning(
            "PermissionDenied: %s id=%s action=%s",
            actor.user_type,
            actor.instance.id,
            label,
        )
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel,
            outbox,
            prefix=_msg("permission_denied"),
        )
    except MissingContextError as exc:
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel,
            outbox,
            prefix=str(exc),
        )
    except NoDataError:
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel,
            outbox,
            prefix=_msg("no_data", label=label.lower()),
        )
    except DataFetchError as exc:
        logger.error("DataFetchError %s: %s", label, exc)
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel,
            outbox,
            prefix=_msg("fetch_error"),
        )


def _handle_awaiting_patient_search(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    # A fresh query always starts at the first page.
    session.open_search(text.strip())
    _run_patient_search(session, phone_number, channel, outbox, actor)


def _run_patient_search(
    session: ConversationSession,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    actor: Any,
) -> None:
    """Runs the stored query at the session's current page and offers the results."""
    try:
        page = patient_lookup.search_patients(actor, session.search_query, session)
    except PermissionDeniedError:
        outbox.append(Outbound(phone_number, _msg("permission_denied")))
        session.state = ConversationSession.State.AUTHENTICATED
        session.save(update_fields=["state"])
        return
    except InvalidQueryError as exc:
        # Stay in AWAITING_PATIENT_SEARCH so the next message is retried as a search query.
        outbox.append(Outbound(phone_number, str(exc)))
        return
    except NoDataError:
        outbox.append(Outbound(phone_number, _msg("no_patients_found")))
        return

    page = fit_to_budget(
        page,
        lambda rows: (
            render_patient_search_results(
                _msg("patient_search_results"),
                [f"{r['name']} — {r['phone_number']}" for r in rows],
                _UNBOUNDED_CHARS,
            ).text
        ),
        _list_budget(channel),
        _list_line_budget(channel),
        int(plugin_settings.DATA_PAGE_MIN_RECORDS),
    )
    session.record_shown(len(page.records))
    results = page.records
    if not results and page.number > 0:
        session.back_page()
        outbox.append(Outbound(phone_number, _msg("page_last")))
        return

    session.candidates = results
    session.state = ConversationSession.State.SELECTING_PATIENT
    session.save(update_fields=["state", "candidates"])

    prompt = _msg("patient_search_results")
    start = page.offset + 1
    plain_options = [f"{r['name']} — {r['phone_number']}" for r in results]
    body = f"{prompt}\n\n{_paging_text(page)}" if page.is_paginated else prompt
    msg = render_patient_search_results(body, plain_options, get_max_chars(channel), start)

    rows = [{"id": f"patient_{i}", "title": r["name"], "description": r["phone_number"]} for i, r in enumerate(results)]
    # Reply buttons have no room for paging controls, so a paginated set uses the list.
    if not page.is_paginated and len(results) <= get_max_reply_buttons(channel):
        interactive = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body=prompt,
            action_data=[{"id": f"patient_{i}", "title": r["name"]} for i, r in enumerate(results)],
        )
    else:
        interactive = InteractivePayload(
            type=InteractiveType.LIST,
            body=body,
            button_label=_msg("select_patient"),
            action_data=[{"title": _msg("patients_title"), "rows": _rows_with_paging(page, rows, channel)}],
        )

    outbox.append(Outbound(phone_number, OutboundMessage(text=msg.text, interactive=interactive)))


def _handle_selecting_patient(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    step = _paging_step(choice)
    if step:
        if not session.search_query:
            outbox.append(Outbound(phone_number, _msg("page_nothing_open")))
            return
        if step < 0 and session.data_page == 0:
            outbox.append(Outbound(phone_number, _msg("page_first")))
            return
        actor = resolve_actor(session)
        if actor is None:
            session.logout()
            outbox.append(Outbound(phone_number, _msg("session_expired")))
            return
        if step < 0:
            session.back_page()
        else:
            session.advance_page(session.next_offset())
        _run_patient_search(session, phone_number, channel, outbox, actor)
        return

    # patient_ ids are 0-based (patient_0 is the first row).
    index = _parse_selection_index(choice, "patient_", prefixed_base=0, display_start=current_offset(session) + 1)
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore[reportAssignmentType]
    if index is None or not (0 <= index < len(candidates)):
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    selected = candidates[index]
    session.active_patient_external_id = selected["external_id"]
    session.state = ConversationSession.State.AUTHENTICATED
    session.candidates = []
    # A new patient's lists start at the top.
    session.data_menu_choice = ""
    session.data_offsets = []
    session.data_shown = 0
    session.search_query = ""
    session.save(
        update_fields=[
            "state",
            "active_patient_external_id",
            "candidates",
            "data_menu_choice",
            "data_offsets",
            "data_shown",
            "search_query",
        ]
    )
    _send_main_menu(
        phone_number,
        str(session.user_type),
        channel,
        outbox,
        prefix=_msg("patient_selected", name=selected["name"]),
    )


def _enter_document_selection(
    session: ConversationSession,
    menu_key: str,
    records: Any,
    renderer: Any,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    start: int = 1,
) -> bool:
    """Offers the selectable records as a pick-list and parks the session in SELECTING_DOCUMENT."""
    # One row is spent on "Back", so the provider's list limit leaves this many records.
    max_records = get_max_interactive_rows(channel) - 1
    selectable = [record for record in records if getattr(record, "external_id", "")][:max_records]
    rows = [
        {
            "external_id": record.external_id,
            "title": record.name,
            "description": f"{record.date} ({record.status})",
            "menu_key": menu_key,
        }
        for record in selectable
    ]
    if not rows:
        return False

    session.candidates = rows
    session.state = ConversationSession.State.SELECTING_DOCUMENT
    session.save(update_fields=["state", "candidates"])

    prompt = _msg("select_document_prompt")
    interactive_rows = [
        {"id": f"document_{i}", "title": row["title"], "description": row["description"]} for i, row in enumerate(rows)
    ]
    interactive_rows.append({"id": "0", "title": _msg("back")})

    max_chars = get_max_chars(channel)
    interactive_body = f"{renderer(records, max_chars, start).text}\n\n{prompt}"
    fallback_text = f"{renderer(selectable, max_chars, start).text}\n\n{prompt}"
    # Over the body limit send_message degrades to plain text, which would drop the rows.
    body = interactive_body if len(interactive_body) <= get_interactive_body_char_limit(channel) else prompt
    outbox.append(
        Outbound(
            phone_number,
            OutboundMessage(
                text=fallback_text,
                interactive=InteractivePayload(
                    type=InteractiveType.LIST,
                    body=body,
                    button_label=_msg("select_document"),
                    action_data=[{"title": _msg("documents_title"), "rows": interactive_rows}],
                ),
            ),
        )
    )
    return True


def _handle_selecting_document(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    def _return_to_menu(prefix: str | None = None, pace: bool = True) -> None:
        session.state = ConversationSession.State.AUTHENTICATED
        session.candidates = []
        session.save(update_fields=["state", "candidates"])
        _send_main_menu(phone_number, str(session.user_type), channel, outbox, prefix=prefix, pace=pace)

    if choice == "0":
        _return_to_menu()
        return

    if _paging_step(choice):
        _handle_authenticated(session, phone_number, choice, channel, outbox)
        return

    # document_ ids are 0-based (document_0 is the first row).
    index = _parse_selection_index(choice, "document_", prefixed_base=0, display_start=current_offset(session) + 1)
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore[reportAssignmentType]
    if index is None or not (0 <= index < len(candidates)):
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    selected = candidates[index]
    menu = _STAFF_MENU if session.user_type == ConversationSession.UserType.STAFF.value else _PATIENT_MENU
    entry = menu.get(selected["menu_key"])
    if entry is None:
        logger.error("_handle_selecting_document: stale menu_key %s in session candidates", selected["menu_key"])
        _return_to_menu(prefix=_msg("fetch_error"))
        return
    _label, _fetcher, _renderer, document_resolver = entry
    if document_resolver is None:
        logger.error("_handle_selecting_document: menu entry %s has no document resolver", selected["menu_key"])
        _return_to_menu(prefix=_msg("fetch_error"))
        return

    try:
        patient = resolve_target_patient(actor, session)
        document_request = document_resolver(patient, selected["external_id"])
        if document_request is None:
            _return_to_menu(prefix=_msg("document_unavailable"))
            return
        link = get_or_create_document_link(actor, patient, document_request, provider=channel)
    except PermissionDeniedError:
        _return_to_menu(prefix=_msg("permission_denied"))
        return
    except MissingContextError as exc:
        _return_to_menu(prefix=str(exc))
        return
    except DocumentUnavailableError:
        logger.warning("_handle_selecting_document: document unavailable for %s", selected["external_id"])
        _return_to_menu(prefix=_msg("document_unavailable"))
        return

    outbox.append(
        Outbound(
            phone_number,
            build_document_message(
                f"{selected['title']}\n\n{_msg('document_text')}",
                build_document_url(link),
                footer=_msg("document_footer"),
            ),
        )
    )


def _send_main_menu(
    phone_number: str,
    user_type: str,
    channel: str,
    outbox: list[Outbound],
    name: str | None = None,
    prefix: str | None = None,
    pace: bool = True,
) -> None:
    menu = _STAFF_MENU if user_type == ConversationSession.UserType.STAFF.value else _PATIENT_MENU

    # ids match the existing menu keys so _handle_authenticated's menu.get(choice) works unchanged
    rows = _menu_rows(menu)

    greeting = _msg("greeting", name=name) if name else _msg("choose_option")
    menu_items_text = _menu_text(rows)

    if prefix:
        plain_text = f"{prefix}\n\n{greeting}\n\n{menu_items_text}"
        interactive_body = f"{prefix}\n\n{greeting}"
    else:
        plain_text = f"{greeting}\n\n{menu_items_text}"
        interactive_body = greeting

    msg = OutboundMessage(
        text=plain_text,
        interactive=InteractivePayload(
            type=InteractiveType.LIST,
            body=interactive_body,
            button_label=_msg("view_menu"),
            action_data=[{"title": _msg("menu_title"), "rows": rows}],
        ),
    )
    outbox.append(Outbound(phone_number, msg, pace=pace))


def _send_candidate_menu(
    phone_number: str, candidates: list[dict[str, Any]], channel: str, outbox: list[Outbound]
) -> None:
    prompt = _msg("select_account")
    plain_text = numbered_block(
        prompt,
        [_msg("account_line", name=c["full_name"], user_type=c["user_type"].capitalize()) for c in candidates],
        get_max_chars(channel),
    )

    if len(candidates) <= get_max_reply_buttons(channel):
        buttons = [{"id": f"candidate_{i + 1}", "title": c["full_name"]} for i, c in enumerate(candidates)]
        interactive = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body=prompt,
            action_data=buttons,
        )
    else:
        rows = [
            {
                "id": f"candidate_{i + 1}",
                "title": c["full_name"],
                "description": c["user_type"].capitalize(),
            }
            for i, c in enumerate(candidates)
        ]
        interactive = InteractivePayload(
            type=InteractiveType.LIST,
            body=prompt,
            button_label=_msg("select"),
            action_data=[{"title": _msg("accounts_title"), "rows": rows}],
        )

    outbox.append(Outbound(phone_number, OutboundMessage(text=plain_text, interactive=interactive)))
