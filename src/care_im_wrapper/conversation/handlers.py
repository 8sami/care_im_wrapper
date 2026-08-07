from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from django.db import transaction

from care_im_wrapper.auth.actor import resolve_actor
from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.conversation.menus import ENCOUNTERS_LABEL, Action, MenuOption, Scope, menu_for
from care_im_wrapper.conversation.messages import (
    InteractivePayload,
    InteractiveType,
    Outbound,
    OutboundMessage,
)
from care_im_wrapper.conversation.renderers import NOT_RECORDED, numbered_block
from care_im_wrapper.conversation.replies import (
    BACK_ID,
    Choice,
    choices_as_text,
    enumerate_choices,
    join,
    menu_reply,
    picker_reply,
    row,
)
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.data import encounters as encounters_data
from care_im_wrapper.data import medications as medications_data
from care_im_wrapper.data import patient_lookup
from care_im_wrapper.data.common import ALL_PRESCRIPTIONS, resolve_target_encounter, resolve_target_patient
from care_im_wrapper.data.exceptions import (
    DataFetchError,
    InvalidQueryError,
    MissingContextError,
    NoDataError,
    PermissionDeniedError,
)
from care_im_wrapper.data.pagination import Page, fit_to_budget
from care_im_wrapper.documents.delivery import build_document_message
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.documents.service import build_document_url, get_or_create_document_link
from care_im_wrapper.messaging.limits import ChannelLimits
from care_im_wrapper.messaging.registry import get_channel_limits, send_message
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

_PAGE_NEXT_TOKENS = frozenset({"n", "next", "page_next"})
_PAGE_PREV_TOKENS = frozenset({"p", "prev", "previous", "page_prev"})
_MENU_TOKENS = frozenset({"menu", "page_menu"})
_ALL_TOKENS = frozenset({"a", "all", "prescription_all"})
_ENCOUNTER_PICKER_KEY = "enc"
_PRESCRIPTION_PICKER_KEY = "rx"
_MEDICATIONS_LIST_KEY = "meds"


def _paging_step(choice: str) -> int:
    """+1 / -1 for a paging command, 0 for anything else."""
    lowered = choice.strip().lower()
    if lowered in _PAGE_NEXT_TOKENS:
        return 1
    if lowered in _PAGE_PREV_TOKENS:
        return -1
    return 0


_UNBOUNDED_CHARS = 10**9


def _list_line_budget(limits: ChannelLimits) -> int:
    """Lines a paged list may occupy before the client folds it behind a "Read more"."""
    reserve = int(plugin_settings.PAGING_FOOTER_RESERVE_LINES)
    return max(1, limits.preview_lines - reserve)


def _list_budget(limits: ChannelLimits) -> int:
    """Characters a paged list may occupy, less the paging footer."""
    reserve = int(plugin_settings.PAGING_FOOTER_RESERVE_CHARS)
    return max(1, limits.text_body - reserve)


#: The blank line a page and the prompt below it are joined by.
_BODY_SEPARATOR_CHARS = 4


def _body_budget(limits: ChannelLimits, alongside: str) -> int:
    """Characters one data page may use and still share an interactive body with `alongside`.

    The interactive body is much smaller than a whole message, so pages are trimmed to it and
    not to the message limit -- otherwise a medium list is "one page" that then cannot fit
    beside its prompt and spills into a second message.
    """
    return max(1, limits.interactive_body - len(alongside) - _BODY_SEPARATOR_CHARS)


def _menu_rows(menu: dict[str, MenuOption], in_encounter: bool) -> list[dict[str, str]]:
    """Menu rows, each with a description of what it holds, plus the trailing 0 row:
    Logout on the main menu, Back in the sub-menu."""
    rows = [row(key, option.label, option.description) for key, option in menu.items()]
    if in_encounter:
        rows.append(row(BACK_ID, _msg("back_to_main_menu"), _msg("back_to_main_menu_hint")))
    else:
        rows.append(row(BACK_ID, _msg("logout"), _msg("logout_hint")))
    return rows


def _in_encounter(session: ConversationSession) -> bool:
    return session.menu_context == ConversationSession.MenuContext.ENCOUNTER


#: How one record of a picker reads: its row title, the line under it, and what selecting it
#: has to hand back. Every other form the record takes is derived from this one.
Describe = Callable[[Any], tuple[str, str, dict[str, Any]]]


def _choices_for(records: Sequence[Any], describe: Describe, start: int, *, prefix: str = "") -> list[Choice]:
    return enumerate_choices((describe(record) for record in records), prefix=prefix, start=start)


def _scope_line(session: ConversationSession, subject: str = "") -> str:
    """What the reader is looking at, and whose it is.

    Both halves are optional -- a patient reading their own records has no patient clause, the
    main menu has no encounter clause -- so the line is built from whichever apply. On a
    text-only medical channel the scope is what makes the data below it trustworthy, so it is
    never left implicit; with neither half there is nothing to say and the line is dropped.
    """
    clauses = []
    if _in_encounter(session) and session.active_encounter_label:
        clauses.append(_msg("viewing_encounter", encounter=session.active_encounter_label))
    if session.active_patient_label:
        clauses.append(_msg("viewing_patient", patient=session.active_patient_label))
    if not clauses:
        return ""
    return " ".join([_msg("viewing", subject=subject or _msg("subject_records")), *clauses])


def _menu_prompt(session: ConversationSession, name: str | None = None) -> str:
    """The scope the reader is in, then the invitation to choose.

    Only a bare menu carries the scope here. A reply with records under it heads them with the
    scope line instead, where it reads as their title rather than as a note underneath.
    """
    invitation = _msg("greeting", name=name) if name else _msg("choose_option")
    return join(_scope_line(session), invitation)


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
                ConversationSession.State.SELECTING_ENCOUNTER: _handle_selecting_encounter,
                ConversationSession.State.SELECTING_PRESCRIPTION: _handle_selecting_prescription,
            }
            handler = dispatch.get(session.state)  # pyright: ignore[reportArgumentType]
            if handler:
                handler(session, phone_number, text, channel, outbox)
            else:
                logger.error("run_state_machine: unhandled state %s", session.state)

        # Inside the transaction: a turn whose first message never left must not leave the
        # session advanced behind it. _flush re-raises only for that first message, so the
        # rollback undoes the turn and the retry replays it against unmoved state. Once
        # anything has been delivered the turn is committed, and _flush swallows the rest.
        _flush(channel, outbox)


def _flush(channel: str, outbox: list[Outbound]) -> None:
    """Sends every queued message the handler built for this turn.

    Any failure on the *first* message propagates, whatever its kind. Nothing was
    delivered, so the caller's transaction rolls back and the reader is not left advanced
    past a reply they never saw; process_inbound_message's exception taxonomy then decides
    whether a retry is worth spending. Catching a provider-side failure here instead would
    commit the turn silently -- the reader gets nothing, and no retry ever runs.

    Once something *has* been delivered the turn is committed, so a later failure is logged
    and the remainder dropped: replaying the turn would resend what already arrived.
    """
    for index, item in enumerate(outbox):
        try:
            send_message(channel, item.phone_number, item.message, pace=item.pace)
        except Exception:
            if index == 0:
                raise
            logger.warning(
                "_flush: failed to send item %d/%d to %s on %s after earlier sends; dropping the rest of this turn.",
                index + 1,
                len(outbox),
                mask_phone_number(item.phone_number),
                channel,
                exc_info=True,
            )
            return


def _handle_new(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found:
        outbox.append(Outbound(phone_number, _msg("not_found")))
        return

    # Serialise every identity to JSON-safe dicts; the year of birth narrows them next turn.
    identities: list[dict[str, Any]] = [
        {
            "user_type": i.user_type,
            "user_id": i.user_id,
            "year_of_birth": i.year_of_birth,
            "full_name": i.full_name,
            "phone_number": i.phone_number,
        }
        for i in result.identities
    ]
    session.offer(identities, ConversationSession.State.AWAITING_YOB)
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
        _send_menu(session, phone_number, channel, outbox, name=match["full_name"])
        return

    choices = enumerate_choices(
        ((c["full_name"], c["user_type"].capitalize(), c) for c in shortlist), prefix="candidate", start=1
    )
    session.offer([choice.candidate for choice in choices], ConversationSession.State.AMBIGUOUS)
    _send_candidate_menu(phone_number, choices, channel, outbox)


def _handle_ambiguous(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    match = session.select(choice)
    if match is None:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    session.authenticate(
        user_type=match["user_type"],
        user_id=match["user_id"],
        name=match["full_name"],
        phone=match["phone_number"],
    )
    _send_menu(session, phone_number, channel, outbox, name=match["full_name"])


@contextmanager
def _reporting_data_errors(
    session: ConversationSession,
    actor: Any,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    *,
    label: str,
    scope: Scope,
):
    """Turns any fetcher failure into a menu with an explanation on top.

    Every data path reports these four identically, so the clauses live here once rather
    than once per option and picker.
    """
    try:
        yield
    except PermissionDeniedError:
        logger.warning("PermissionDenied: %s id=%s action=%s", actor.user_type, actor.instance.id, label)
        prefix = _msg("permission_denied")
    except MissingContextError as exc:
        if scope is not Scope.PATIENT and _in_encounter(session):
            session.clear_encounter_scope()
        prefix = str(exc)
    except NoDataError:
        prefix = _msg("no_data", label=label.lower())
    except DataFetchError as exc:
        logger.error("DataFetchError %s: %s", label, exc)
        prefix = _msg("fetch_error")
    else:
        return

    _send_menu(session, phone_number, channel, outbox, prefix=prefix)


def _handle_authenticated(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    if choice == BACK_ID:
        if _in_encounter(session):
            session.clear_encounter_scope()
            _send_menu(session, phone_number, channel, outbox)
            return
        session.logout()
        outbox.append(Outbound(phone_number, _msg("logout_confirm")))
        return

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    if choice.lower() in _MENU_TOKENS:
        _send_menu(session, phone_number, channel, outbox)
        return

    menu = menu_for(session)

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

    option = menu.get(choice)

    if option is None:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    if option.action is Action.PATIENT_SEARCH:
        session.start_patient_search()
        outbox.append(Outbound(phone_number, _msg("patient_search_prompt")))
        return

    if option.action is Action.OPEN_ENCOUNTER:
        _enter_encounter_selection(session, actor, phone_number, channel, outbox)
        return

    if option.action is Action.ENCOUNTER_DOCUMENT:
        _send_encounter_document(session, actor, option, phone_number, channel, outbox)
        return

    if option.scope is Scope.PRESCRIPTION and not step:
        session.clear_prescription_scope()
        _enter_prescription_selection(session, actor, choice, option, phone_number, channel, outbox)
        return

    _run_option(session, actor, choice, option, phone_number, channel, outbox, advance=pending_advance)


def _run_option(
    session: ConversationSession,
    actor: Any,
    menu_key: str,
    option: MenuOption,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    advance: bool = False,
) -> None:
    """Runs one menu option's fetcher and sends its page, with the menu or paging buttons."""
    fetcher, renderer = option.fetcher, option.renderer
    if fetcher is None or renderer is None:
        logger.error("_run_option: option %s has no fetcher/renderer", menu_key)
        _send_menu(session, phone_number, channel, outbox, prefix=_msg("fetch_error"))
        return

    if not advance:
        session.open_data_list(menu_key)

    limits = get_channel_limits(channel)
    with _reporting_data_errors(session, actor, phone_number, channel, outbox, label=option.label, scope=option.scope):
        if advance:
            session.advance_page(session.next_offset())

        # The scope heads the records instead of the fetcher's own "Your recent X:" line --
        # it says the same thing and more. With nothing scoped, the fetcher's line stands.
        header = _scope_line(session, option.label.lower())
        prompt = _msg("choose_option")
        budget = _body_budget(limits, prompt)
        data = fetcher(actor, session)
        page = data if isinstance(data, Page) else None
        if page is not None:
            page = fit_to_budget(
                page,
                lambda rows: renderer(rows, _UNBOUNDED_CHARS, page.offset + 1, header=header).text,
                budget,
                _list_line_budget(limits),
                int(plugin_settings.DATA_PAGE_MIN_RECORDS),
            )
            session.record_shown(page.consumed())
        records = page.records if page is not None else data

        if page is not None and not records and page.number > 0:
            session.back_page()
            outbox.append(Outbound(phone_number, _msg("page_last")))
            return

        if page is None:
            start = 1
            renderer_msg = renderer(records, limits.text_body, header=header)
        else:
            start = page.offset + 1
            renderer_msg = renderer(records, budget, start, header=header)

        if option.document_resolver is not None and _enter_document_selection(
            session, menu_key, records, phone_number, channel, outbox, start
        ):
            return

        outbox.extend(
            menu_reply(
                phone_number,
                limits,
                prompt=prompt,
                rows=_menu_rows(menu_for(session), _in_encounter(session)),
                button_label=_msg("view_menu"),
                section_title=_menu_section_title(session),
                content=renderer_msg.text,
                page=page,
            )
        )


def _handle_awaiting_patient_search(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    session.open_search(text.strip())
    _run_patient_search(session, phone_number, channel, outbox, actor)


def _describe_patient(record: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """A search result as the picker offers it: who, and the number that identifies them."""
    return (record["name"], record["phone_number"], record)


def _run_patient_search(
    session: ConversationSession,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    actor: Any,
) -> None:
    """Runs the stored query at the session's current page and offers the results."""
    limits = get_channel_limits(channel)
    try:
        page = patient_lookup.search_patients(actor, session.search_query, session)
    except PermissionDeniedError:
        outbox.append(Outbound(phone_number, _msg("permission_denied")))
        session.return_to_menu()
        return
    except InvalidQueryError as exc:
        outbox.append(Outbound(phone_number, str(exc)))
        return
    except NoDataError:
        outbox.append(Outbound(phone_number, _msg("no_patients_found")))
        return

    page = fit_to_budget(
        page,
        lambda rows: choices_as_text(
            _msg("patients_title"), _choices_for(rows, _describe_patient, page.offset + 1), _UNBOUNDED_CHARS
        ),
        _list_budget(limits),
        _list_line_budget(limits),
        int(plugin_settings.DATA_PAGE_MIN_RECORDS),
    )
    session.record_shown(len(page.records))
    results = page.records
    if not results and page.number > 0:
        session.back_page()
        outbox.append(Outbound(phone_number, _msg("page_last")))
        return

    choices = _choices_for(results, _describe_patient, page.offset + 1, prefix="patient")
    session.offer([choice.candidate for choice in choices], ConversationSession.State.SELECTING_PATIENT)

    prompt = _msg("patient_search_results")
    section_title = _msg("patients_title")

    # A short, unpaged result set fits on buttons -- one tap, no list to open. Anything
    # longer, or anything paged, needs the list so every result stays selectable.
    if not page.is_paginated and len(results) <= limits.max_buttons:
        interactive = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body=prompt,
            action_data=[row(choice.row_id, choice.title) for choice in choices],
        )
        text = join(choices_as_text(section_title, choices, limits.text_body), prompt)
        outbox.append(Outbound(phone_number, OutboundMessage(text=text, interactive=interactive)))
        return

    outbox.extend(
        picker_reply(
            phone_number,
            limits,
            prompt=prompt,
            choices=choices,
            button_label=_msg("select_patient"),
            section_title=section_title,
            page=page,
        )
    )


def _handle_selecting_patient(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    if choice == BACK_ID:
        session.return_to_menu()
        _send_menu(session, phone_number, channel, outbox)
        return

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

    selected = session.select(choice)
    if selected is None:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    # The name rides along on the session: every later reply names whose records these are.
    session.switch_patient(selected["external_id"], selected["name"])
    _send_menu(session, phone_number, channel, outbox)


def _describe_document(record: Any, menu_key: str) -> tuple[str, str, dict[str, Any]]:
    return (record.name, f"{record.date} ({record.status})", {"external_id": record.external_id, "menu_key": menu_key})


def _enter_document_selection(
    session: ConversationSession,
    menu_key: str,
    records: Any,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    start: int = 1,
) -> bool:
    """Offers the selectable records as a pick-list and parks the session in SELECTING_DOCUMENT.

    Returns False when nothing on the page can be selected, leaving the caller to send its
    ordinary data reply -- records cached before `external_id` existed come back without one.
    """
    limits = get_channel_limits(channel)
    # One row is spent on "Back", so the provider's list limit leaves this many records.
    selectable = [record for record in records if getattr(record, "external_id", "")][: limits.max_rows - 1]
    if not selectable:
        return False

    choices = _choices_for(selectable, lambda record: _describe_document(record, menu_key), start, prefix="document")
    session.offer([choice.candidate for choice in choices], ConversationSession.State.SELECTING_DOCUMENT)

    outbox.extend(
        picker_reply(
            phone_number,
            limits,
            prompt=_msg("select_document_prompt"),
            choices=choices,
            button_label=_msg("select_document"),
            section_title=_msg("documents_title"),
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
        # close_selection, not return_to_menu: the data page the pick-list was drawn from is
        # still open behind it, and n/p must keep working on it.
        session.close_selection()
        _send_menu(session, phone_number, channel, outbox, prefix=prefix, pace=pace)

    if choice == BACK_ID:
        _return_to_menu()
        return

    if _paging_step(choice):
        # A paging command re-runs the list underneath one page along.
        _handle_authenticated(session, phone_number, choice, channel, outbox)
        return

    selected = session.select(choice)
    if selected is None:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    option = menu_for(session).get(selected["menu_key"])
    if option is None:
        logger.error("_handle_selecting_document: stale menu_key %s in session candidates", selected["menu_key"])
        _return_to_menu(prefix=_msg("fetch_error"))
        return
    if option.document_resolver is None:
        logger.error("_handle_selecting_document: menu entry %s has no document resolver", selected["menu_key"])
        _return_to_menu(prefix=_msg("fetch_error"))
        return

    try:
        patient = resolve_target_patient(actor, session)
        document_request = option.document_resolver(patient, selected["external_id"])
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


def _encounter_label(record: Any) -> str:
    """The one-line identity of an encounter, for the sub-menu header."""
    return _msg("encounter_label", facility=record.facility, date=record.date, status=record.status)


def _fetch_picker_page(
    session: ConversationSession,
    fetcher: Any,
    describe: Describe,
    actor: Any,
    channel: str,
    advance: int,
    list_key: str,
    section_title: str,
    reserved_rows: int,
) -> Page:
    """One page of a picker: the session's paging move applied, then the page trimmed to
    what the provider's list and the reader's screen can both hold.

    `reserved_rows` is what the non-record rows (Back, and any All) take out of the budget.
    Paging costs no rows of its own -- it rides on buttons -- unless the provider has none,
    which is the only case where it has to come out of the same budget.
    """
    limits = get_channel_limits(channel)
    if advance == 0:
        session.open_data_list(list_key)
    elif advance < 0:
        session.back_page()
    else:
        session.advance_page(session.next_offset())

    page = fetcher(actor, session)

    page = fit_to_budget(
        page,
        lambda rows: choices_as_text(section_title, _choices_for(rows, describe, page.offset + 1), _UNBOUNDED_CHARS),
        _list_budget(limits),
        _list_line_budget(limits),
        int(plugin_settings.DATA_PAGE_MIN_RECORDS),
    )
    paging_row_cost = 0 if limits.max_buttons else int(page.has_next) + int(page.has_previous)
    max_records = max(1, limits.max_rows - reserved_rows - paging_row_cost)
    if len(page.records) > max_records:
        page = replace(
            page,
            records=page.records[:max_records],
            source_weights=page.source_weights[:max_records],
            has_next=True,
        )
    session.record_shown(page.consumed())
    return page


def _send_picker(
    session: ConversationSession,
    page: Page,
    describe: Describe,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    *,
    state: str,
    prefix: str,
    prompt: str,
    button_label: str,
    section_title: str,
    leading_rows: list[dict[str, str]] | None = None,
) -> None:
    """Parks the session on a picker's choices and sends them.

    Rows, stored candidates and the plain-text fallback all come from `describe`, numbered
    from the page's own offset -- so a typed number and a tapped row always mean the same
    record, whichever page it was offered on.
    """
    choices = _choices_for(page.records, describe, page.offset + 1, prefix=prefix)
    session.offer([choice.candidate for choice in choices], state)
    outbox.extend(
        picker_reply(
            phone_number,
            get_channel_limits(channel),
            prompt=prompt,
            choices=choices,
            button_label=button_label,
            section_title=section_title,
            leading_rows=leading_rows or (),
            page=page,
        )
    )


def _paged_past_the_end(session: ConversationSession, page: Page, phone_number: str, outbox: list[Outbound]) -> bool:
    """An empty page beyond the first means the reader walked off the end; step back."""
    if page.records:
        return False
    session.back_page()
    outbox.append(Outbound(phone_number, _msg("page_last")))
    return True


def _describe_encounter(record: Any) -> tuple[str, str, dict[str, Any]]:
    """An encounter as the picker offers it: where it happened, when, and how it ended."""
    return (
        record.facility,
        f"{record.date} ({record.status})",
        {"external_id": record.external_id, "label": _encounter_label(record)},
    )


def _enter_encounter_selection(
    session: ConversationSession,
    actor: Any,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    advance: int = 0,
) -> None:
    """Offers the patient's encounters and parks the session in SELECTING_ENCOUNTER."""
    with _reporting_data_errors(
        session, actor, phone_number, channel, outbox, label=ENCOUNTERS_LABEL, scope=Scope.PATIENT
    ):
        page = _fetch_picker_page(
            session,
            encounters_data.fetch_encounters,
            _describe_encounter,
            actor,
            channel,
            advance,
            list_key=_ENCOUNTER_PICKER_KEY,
            section_title=_msg("encounters_title"),
            reserved_rows=1,
        )

        if _paged_past_the_end(session, page, phone_number, outbox):
            return

        if len(page.records) == 1 and not page.is_paginated:
            record = page.records[0]
            session.open_encounter(record.external_id, _encounter_label(record))
            _send_menu(session, phone_number, channel, outbox)
            return

        _send_picker(
            session,
            page,
            _describe_encounter,
            phone_number,
            channel,
            outbox,
            state=ConversationSession.State.SELECTING_ENCOUNTER,
            prefix="encounter",
            prompt=_msg("select_encounter_prompt"),
            button_label=_msg("select_encounter"),
            section_title=_msg("encounters_title"),
        )


def _handle_selecting_encounter(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    if choice == BACK_ID:
        session.return_to_menu()
        _send_menu(session, phone_number, channel, outbox)
        return

    step = _paging_step(choice)
    if step:
        if step < 0 and session.data_page == 0:
            outbox.append(Outbound(phone_number, _msg("page_first")))
            return
        _enter_encounter_selection(session, actor, phone_number, channel, outbox, advance=step)
        return

    selected = session.select(choice)
    if selected is None:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    # Reaching the picker at all means there was more than one to choose from.
    session.open_encounter(selected["external_id"], selected["label"], has_alternatives=True)
    _send_menu(session, phone_number, channel, outbox)


def _enter_prescription_selection(
    session: ConversationSession,
    actor: Any,
    menu_key: str,
    option: MenuOption,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    advance: int = 0,
) -> None:
    """Offers this encounter's prescriptions, or skips straight to the medications.

    care_fe's PrescriptionListSelector is a sidebar within the medicines tab, not a level of
    navigation -- so this is a filter chosen per viewing, not a scope that sticks.
    """
    describe = _prescription_describer(menu_key)
    with _reporting_data_errors(session, actor, phone_number, channel, outbox, label=option.label, scope=option.scope):
        try:
            page = _fetch_picker_page(
                session,
                medications_data.fetch_prescription_choices,
                describe,
                actor,
                channel,
                advance,
                list_key=_PRESCRIPTION_PICKER_KEY,
                section_title=_msg("prescriptions_title"),
                reserved_rows=2,
            )
        except NoDataError:
            _show_all_prescriptions(session, actor, menu_key, option, phone_number, channel, outbox)
            return

        if _paged_past_the_end(session, page, phone_number, outbox):
            return

        if len(page.records) == 1 and not page.is_paginated:
            _show_all_prescriptions(session, actor, menu_key, option, phone_number, channel, outbox)
            return

        _send_picker(
            session,
            page,
            describe,
            phone_number,
            channel,
            outbox,
            state=ConversationSession.State.SELECTING_PRESCRIPTION,
            prefix="prescription",
            prompt=_msg("select_prescription_prompt"),
            button_label=_msg("select_prescription"),
            section_title=_msg("prescriptions_title"),
            leading_rows=[_all_prescriptions_row()],
        )


def _show_all_prescriptions(
    session: ConversationSession,
    actor: Any,
    menu_key: str,
    option: MenuOption,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
) -> None:
    """Sets the "all prescriptions" filter and runs the medication list against the encounter
    menu -- used when there is nothing to pick from (zero or one prescription)."""
    session.set_prescription_scope(ALL_PRESCRIPTIONS, _msg("all_prescriptions"))
    _run_option(session, actor, menu_key, option, phone_number, channel, outbox)


def _prescription_describer(menu_key: str) -> Describe:
    """A prescription as care_fe's PrescriptionListSelector card reads it: when it was
    written, over who wrote it.

    `menu_key` rides along on each choice so the reply knows which menu option to re-run once
    a prescription is picked.
    """

    def describe(record: Any) -> tuple[str, str, dict[str, Any]]:
        return (
            record.name or record.prescribed_on,
            _msg("prescription_choice_by", prescribed_by=record.prescribed_by or NOT_RECORDED),
            {"external_id": record.external_id, "menu_key": menu_key},
        )

    return describe


def _all_prescriptions_row() -> dict[str, str]:
    """care_fe leads its sidebar with "All prescriptions"; `a` picks it, because the numbers
    belong to the prescriptions themselves."""
    return row("prescription_all", _msg("all_prescriptions"), _msg("view_all_medications"))


def _show_medications_keeping_picker(
    session: ConversationSession,
    actor: Any,
    option: MenuOption,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    advance: int = 0,
) -> None:
    """Renders the scoped medications while keeping the prescription picker open beside them,
    so the reader can switch prescriptions in place -- care_fe's PrescriptionListSelector
    stays next to the medicines, it is not a place you leave to change the filter.

    The choices are redrawn from what the session already stored, so paging the medications
    never renumbers the prescriptions: a row and a typed number keep meaning what they meant
    when the picker was first offered.
    """
    fetcher, renderer = option.fetcher, option.renderer
    if fetcher is None or renderer is None:
        logger.error("_show_medications_keeping_picker: option %s has no fetcher/renderer", option.label)
        _send_menu(session, phone_number, channel, outbox, prefix=_msg("fetch_error"))
        return

    limits = get_channel_limits(channel)
    choices = [Choice.from_candidate(candidate) for candidate in session.candidates or []]  # pyright: ignore[reportGeneralTypeIssues]
    prompt = _msg("select_prescription_prompt")
    header = _scope_line(session, option.label.lower())

    with _reporting_data_errors(session, actor, phone_number, channel, outbox, label=option.label, scope=option.scope):
        if advance == 0:
            session.open_data_list(_MEDICATIONS_LIST_KEY)
        elif advance < 0:
            session.back_page()
        else:
            session.advance_page(session.next_offset())

        budget = _body_budget(limits, prompt)
        page = fetcher(actor, session)
        page = fit_to_budget(
            page,
            lambda rows: renderer(rows, _UNBOUNDED_CHARS, page.offset + 1, header=header).text,
            budget,
            _list_line_budget(limits),
            int(plugin_settings.DATA_PAGE_MIN_RECORDS),
        )
        session.record_shown(page.consumed())

        if not page.records and page.number > 0:
            session.back_page()
            outbox.append(Outbound(phone_number, _msg("page_last")))
            return

        outbox.extend(
            picker_reply(
                phone_number,
                limits,
                prompt=prompt,
                choices=choices,
                button_label=_msg("select_prescription"),
                section_title=_msg("prescriptions_title"),
                leading_rows=[_all_prescriptions_row()],
                content=renderer(page.records, budget, page.offset + 1, header=header).text,
                page=page,
            )
        )


def _handle_selecting_prescription(
    session: ConversationSession, phone_number: str, text: str, channel: str, outbox: list[Outbound]
) -> None:
    choice = text.strip()

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        outbox.append(Outbound(phone_number, _msg("session_expired")))
        return

    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore[reportAssignmentType]
    menu_key = candidates[0]["menu_key"] if candidates else ""
    option = menu_for(session).get(menu_key)

    if choice == BACK_ID:
        session.return_to_menu()
        _send_menu(session, phone_number, channel, outbox)
        return

    if option is None:
        logger.error("_handle_selecting_prescription: stale menu_key %s in session candidates", menu_key)
        session.return_to_menu()
        _send_menu(session, phone_number, channel, outbox, prefix=_msg("fetch_error"))
        return

    step = _paging_step(choice)
    if step:
        if step < 0 and session.data_page == 0:
            outbox.append(Outbound(phone_number, _msg("page_first")))
            return
        if session.active_prescription_external_id:
            _show_medications_keeping_picker(session, actor, option, phone_number, channel, outbox, advance=step)
        else:
            _enter_prescription_selection(session, actor, menu_key, option, phone_number, channel, outbox, advance=step)
        return

    if choice.lower() in _ALL_TOKENS:
        session.set_prescription_scope(ALL_PRESCRIPTIONS, _msg("all_prescriptions"))
        _show_medications_keeping_picker(session, actor, option, phone_number, channel, outbox)
        return

    selected = session.select(choice)
    if selected is None:
        outbox.append(Outbound(phone_number, _msg("invalid_choice")))
        return

    session.set_prescription_scope(selected["external_id"], selected["title"])
    _show_medications_keeping_picker(session, actor, option, phone_number, channel, outbox)


def _send_encounter_document(
    session: ConversationSession,
    actor: Any,
    option: MenuOption,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
) -> None:
    """The open encounter's discharge summary.

    No pick-list: the encounter is already chosen, so the resolver is called directly --
    what the retired "Encounter details" option uniquely provided.
    """
    if option.document_resolver is None:
        logger.error("_send_encounter_document: option %s has no document resolver", option.label)
        _send_menu(session, phone_number, channel, outbox, prefix=_msg("fetch_error"))
        return

    with _reporting_data_errors(session, actor, phone_number, channel, outbox, label=option.label, scope=option.scope):
        try:
            encounter = resolve_target_encounter(actor, session)
            document_request = option.document_resolver(encounter.patient, str(encounter.external_id))
            if document_request is None:
                _send_menu(session, phone_number, channel, outbox, prefix=_msg("document_unavailable"))
                return
            link = get_or_create_document_link(actor, encounter.patient, document_request, provider=channel)
        except DocumentUnavailableError:
            logger.warning(
                "_send_encounter_document: document unavailable for %s", session.active_encounter_external_id
            )
            _send_menu(session, phone_number, channel, outbox, prefix=_msg("document_unavailable"))
            return

        outbox.append(
            Outbound(
                phone_number,
                build_document_message(
                    f"{option.label}\n\n{_msg('document_text')}",
                    build_document_url(link),
                    footer=_msg("document_footer"),
                ),
            )
        )


def _menu_section_title(session: ConversationSession) -> str:
    return _msg("encounter_menu_title") if _in_encounter(session) else _msg("menu_title")


def _send_menu(
    session: ConversationSession,
    phone_number: str,
    channel: str,
    outbox: list[Outbound],
    name: str | None = None,
    prefix: str | None = None,
    pace: bool = True,
) -> None:
    """Sends whichever menu the session is currently in, main or encounter sub-menu.

    `prefix` explains why the menu is back -- a permission refusal, an empty list, a patient
    just switched to.
    """
    outbox.extend(
        menu_reply(
            phone_number,
            get_channel_limits(channel),
            prompt=_menu_prompt(session, name),
            rows=_menu_rows(menu_for(session), _in_encounter(session)),
            button_label=_msg("view_menu"),
            section_title=_menu_section_title(session),
            content=prefix or "",
            pace=pace,
        )
    )


def _send_candidate_menu(phone_number: str, choices: list[Choice], channel: str, outbox: list[Outbound]) -> None:
    """The accounts one phone number resolves to. Never paged -- one number maps to a handful
    of identities at most, so this stays a single message either way."""
    limits = get_channel_limits(channel)
    prompt = _msg("select_account")
    plain_text = numbered_block(
        prompt,
        [_msg("account_line", name=choice.title, user_type=choice.description) for choice in choices],
        limits.text_body,
    )

    if len(choices) <= limits.max_buttons:
        interactive = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body=prompt,
            action_data=[{"id": choice.row_id, "title": choice.title} for choice in choices],
        )
    else:
        interactive = InteractivePayload(
            type=InteractiveType.LIST,
            body=prompt,
            button_label=_msg("select"),
            action_data=[{"title": _msg("accounts_title"), "rows": [choice.row for choice in choices]}],
        )

    outbox.append(Outbound(phone_number, OutboundMessage(text=plain_text, interactive=interactive)))
