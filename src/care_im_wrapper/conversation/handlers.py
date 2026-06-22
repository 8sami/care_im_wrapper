from __future__ import annotations

import logging
from typing import Any

from care_im_wrapper.auth.actor import resolve_actor
from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.auth.states import ConversationState
from care_im_wrapper.conversation.menus import _PATIENT_MENU, _STAFF_MENU
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data import (
    patient_lookup,
)
from care_im_wrapper.data.exceptions import (
    DataFetchError,
    MissingContextError,
    NoDataError,
    PermissionDeniedError,
)
from care_im_wrapper.messaging.registry import send as messaging_send
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def run_state_machine(phone_number: str, text: str, channel: str) -> None:
    session = _get_or_create_session(phone_number, channel)

    if session.is_in_cooldown():
        messaging_send(channel, phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes()))
        return

    dispatch = {
        ConversationState.NEW: _handle_new,
        ConversationState.AWAITING_YOB: _handle_awaiting_yob,
        ConversationState.AMBIGUOUS: _handle_ambiguous,
        ConversationState.AUTHENTICATED: _handle_authenticated,
        ConversationState.AWAITING_PATIENT_SEARCH: _handle_awaiting_patient_search,
        ConversationState.SELECTING_PATIENT: _handle_selecting_patient,
    }
    handler = dispatch.get(session.state)  # pyright: ignore [reportArgumentType]
    if handler:
        handler(session, phone_number, text, channel)
    else:
        logger.error("run_state_machine: unhandled state %s", session.state)


def _handle_new(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found:
        messaging_send(channel, phone_number, _msg("not_found"))
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
    session.state = ConversationState.AWAITING_YOB  # pyright: ignore[reportAttributeAccessIssue]
    session.save(update_fields=["state", "candidates"])
    messaging_send(channel, phone_number, _msg("yob_prompt"))


def _handle_awaiting_yob(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    stripped = text.strip()
    if not stripped.isdigit() or len(stripped) != 4:
        messaging_send(channel, phone_number, _msg("yob_invalid"))
        return

    year = int(stripped)
    shortlist = [c for c in session.candidates if c["year_of_birth"] == year]  # pyright: ignore [reportGeneralTypeIssues]

    if not shortlist:
        session.increment_failed_attempt()
        if session.state == ConversationState.COOLDOWN:
            messaging_send(channel, phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes()))
        else:
            remaining = int(plugin_settings.MAX_FAILED_ATTEMPTS) - session.failed_attempts  # pyright: ignore[reportOperatorIssue]
            messaging_send(channel, phone_number, _msg("yob_wrong", remaining=remaining))
        return

    if len(shortlist) == 1:
        # Exactly one match -> authenticate immediately
        match = shortlist[0]
        session.authenticate(
            user_type=match["user_type"],
            user_id=match["user_id"],
            name=match["full_name"],
            phone=match["phone_number"],
        )
        _send_main_menu(phone_number, match["user_type"], name=match["full_name"], channel=channel)
        return

    session.candidates = shortlist
    session.state = ConversationState.AMBIGUOUS  # pyright: ignore [reportAttributeAccessIssue]
    session.save(update_fields=["state", "candidates"])
    _send_candidate_menu(phone_number, shortlist, channel)


def _handle_ambiguous(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    choice = text.strip()
    if not choice.isdigit():
        messaging_send(channel, phone_number, _msg("invalid_choice"))
        return

    index = int(choice) - 1
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore [reportAssignmentType]
    if index < 0 or index >= len(candidates):
        messaging_send(channel, phone_number, _msg("invalid_choice"))
        return

    match = candidates[index]
    session.authenticate(
        user_type=match["user_type"],
        user_id=match["user_id"],
        name=match["full_name"],
        phone=match["phone_number"],
    )
    _send_main_menu(phone_number, str(match["user_type"]), name=match["full_name"], channel=channel)


def _handle_authenticated(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    choice = text.strip()

    if choice == "0":
        session.logout()
        messaging_send(channel, phone_number, _msg("logout_confirm"))
        return

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        messaging_send(channel, phone_number, _msg("session_expired"))
        return

    user_type_str = str(session.user_type)
    menu = _STAFF_MENU if user_type_str == "staff" else _PATIENT_MENU
    entry = menu.get(choice)

    if not entry:
        _send_main_menu(phone_number, user_type_str, channel=channel)
        return

    label, fetcher = entry

    if fetcher is None:
        session.state = ConversationState.AWAITING_PATIENT_SEARCH  # pyright: ignore [reportAttributeAccessIssue]
        session.save(update_fields=["state"])
        messaging_send(channel, phone_number, _msg("patient_search_prompt"))
        return

    try:
        result = fetcher(actor, session)
        messaging_send(channel, phone_number, str(result))
    except PermissionDeniedError:
        logger.warning(
            "PermissionDenied: %s id=%s action=%s",
            actor.user_type,
            actor.instance.id,
            label,
        )
        messaging_send(channel, phone_number, _msg("permission_denied"))
    except MissingContextError as exc:
        messaging_send(channel, phone_number, str(exc))
    except NoDataError:
        messaging_send(channel, phone_number, _msg("no_data", label=label.lower()))
    except DataFetchError as exc:
        logger.error("DataFetchError %s: %s", label, exc)
        messaging_send(channel, phone_number, _msg("fetch_error"))

    _send_main_menu(phone_number, user_type_str, channel=channel)


def _handle_awaiting_patient_search(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        messaging_send(channel, phone_number, _msg("session_expired"))
        return

    try:
        results = patient_lookup.search_patients(actor, text)
    except PermissionDeniedError:
        messaging_send(channel, phone_number, _msg("permission_denied"))
        session.state = ConversationState.AUTHENTICATED  # pyright: ignore [reportAttributeAccessIssue]
        session.save(update_fields=["state"])
        return
    except NoDataError:
        messaging_send(channel, phone_number, _msg("no_patients_found"))
        return

    session.candidates = results
    session.state = ConversationState.SELECTING_PATIENT  # pyright: ignore [reportAttributeAccessIssue]
    session.save(update_fields=["state", "candidates"])

    options = [f"{r['name']} — {r['phone_number']}" for r in results]
    messaging_send(
        channel,
        phone_number,
        _msg("patient_search_results") + "\n\n" + "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options)),
    )


def _handle_selecting_patient(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    choice = text.strip()
    if not choice.isdigit():
        messaging_send(channel, phone_number, _msg("invalid_choice"))
        return

    index = int(choice) - 1
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore [reportAssignmentType]
    if index < 0 or index >= len(candidates):
        messaging_send(channel, phone_number, _msg("invalid_choice"))
        return

    selected = candidates[index]
    session.active_patient_external_id = selected["external_id"]
    session.state = ConversationState.AUTHENTICATED  # pyright: ignore [reportAttributeAccessIssue]
    session.candidates = []
    session.save(update_fields=["state", "active_patient_external_id", "candidates"])

    messaging_send(
        channel,
        phone_number,
        _msg("patient_selected", name=selected["name"]),
    )
    _send_main_menu(phone_number, str(session.user_type), channel=channel)


def _get_or_create_session(phone_number: str, provider: str) -> ConversationSession:
    session, _ = ConversationSession.objects.get_or_create(  # pyright: ignore[reportAttributeAccessIssue]
        phone_number=phone_number,
        provider=provider,
    )
    return session


def _send_main_menu(phone_number: str, user_type: str, channel: str, name: str | None = None) -> None:
    menu = _STAFF_MENU if user_type == "staff" else _PATIENT_MENU
    lines = [f"{k}. {v[0]}" for k, v in menu.items()]
    lines.append("0. Logout")
    greeting = _msg("greeting", name=name) if name else _msg("choose_option")
    body = greeting + "\n\n" + "\n".join(lines)
    messaging_send(channel, phone_number, body)


def _send_candidate_menu(phone_number: str, candidates: list[dict[str, Any]], channel: str) -> None:
    lines = [f"{i + 1}. {c['full_name']} ({c['user_type'].capitalize()})" for i, c in enumerate(candidates)]
    body = _msg("select_account") + "\n\n" + "\n".join(lines)
    messaging_send(channel, phone_number, body)


def format_as_str(val: Any) -> str:
    return str(val)
