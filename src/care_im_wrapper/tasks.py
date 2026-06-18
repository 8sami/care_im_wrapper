from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from care_im_wrapper.auth.actor import resolve_actor
from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.auth.states import ConversationState
from care_im_wrapper.data import (
    appointments,
    encounters,
    lab_reports,
    medications,
    patient_lookup,
    patient_summary,
    procedures,
)
from care_im_wrapper.data.exceptions import (
    DataFetchError,
    MissingContextError,
    NoDataError,
    PermissionDeniedError,
)
from care_im_wrapper.messaging.whatsapp import send_text
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_meta_message(self, payload: dict[str, Any], channel: str) -> None:
    logger.info("process_meta_message: channel=%s", channel)
    try:
        _run_state_machine(payload, channel)
    except Exception as exc:
        logger.error("process_meta_message failed: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_meta_status_update(self, payload: dict[str, Any], channel: str) -> None:
    # TODO: Week 6 -> notification status tracking
    # logger.info("process_meta_status_update: channel=%s", channel)
    try:
        pass
    except Exception as exc:
        logger.error("process_meta_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc


def _run_state_machine(payload: dict[str, Any], channel: str) -> None:
    phone_number = _extract_phone(payload)
    text = _extract_text(payload)

    if not phone_number or text is None:
        logger.warning("process_meta_message: missing phone or text in payload")
        return

    session = _get_or_create_session(phone_number, channel)

    if session.is_in_cooldown():
        send_text(phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes()))
        return

    dispatch = {
        ConversationState.NEW: _handle_new,
        ConversationState.AWAITING_YOB: _handle_awaiting_yob,
        ConversationState.AMBIGUOUS: _handle_ambiguous,
        ConversationState.AUTHENTICATED: _handle_authenticated,
        ConversationState.AWAITING_PATIENT_SEARCH: _handle_awaiting_patient_search,
        ConversationState.SELECTING_PATIENT: _handle_selecting_patient,
    }
    handler = dispatch.get(session.state)  # pyright: ignore[reportArgumentType]
    if handler:
        handler(session, phone_number, text)
    else:
        logger.error("process_meta_message: unhandled state %s", session.state)


def _handle_new(session: ConversationSession, phone_number: str, text: str) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found:
        send_text(phone_number, _msg("not_found"))
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
    send_text(phone_number, _msg("yob_prompt"))


def _handle_awaiting_yob(session: ConversationSession, phone_number: str, text: str) -> None:
    stripped = text.strip()
    if not stripped.isdigit() or len(stripped) != 4:
        send_text(phone_number, _msg("yob_invalid"))
        return

    year = int(stripped)
    shortlist = [c for c in session.candidates if c["year_of_birth"] == year]  # pyright: ignore [reportGeneralTypeIssues]

    if not shortlist:
        session.increment_failed_attempt()
        if session.state == ConversationState.COOLDOWN:
            send_text(phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes()))
        else:
            remaining = plugin_settings.MAX_FAILED_ATTEMPTS - session.failed_attempts
            send_text(phone_number, _msg("yob_wrong", remaining=remaining))
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
        _send_main_menu(phone_number, match["user_type"], name=match["full_name"])
        return

    # Multiple candidates share the same YOB —> persist the narrowed shortlist
    session.candidates = shortlist
    session.state = ConversationState.AMBIGUOUS  # pyright: ignore [reportAttributeAccessIssue]
    session.save(update_fields=["state", "candidates"])
    _send_candidate_menu(phone_number, shortlist)


def _handle_ambiguous(session: ConversationSession, phone_number: str, text: str) -> None:
    choice = text.strip()
    if not choice.isdigit():
        send_text(phone_number, _msg("invalid_choice"))
        return

    index = int(choice) - 1
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore [reportAssignmentType]
    if index < 0 or index >= len(candidates):
        send_text(phone_number, _msg("invalid_choice"))
        return

    match = candidates[index]
    session.authenticate(
        user_type=match["user_type"],
        user_id=match["user_id"],
        name=match["full_name"],
        phone=match["phone_number"],
    )
    _send_main_menu(phone_number, str(match["user_type"]), name=match["full_name"])


def _send_main_menu(phone_number: str, user_type: str, name: str | None = None) -> None:
    menu = _STAFF_MENU if user_type == "staff" else _PATIENT_MENU
    lines = [f"{k}. {v[0]}" for k, v in menu.items()]
    lines.append("0. Logout")
    greeting = _msg("greeting", name=name) if name else _msg("choose_option")
    body = greeting + "\n\n" + "\n".join(lines)
    send_text(phone_number, body)


def _send_candidate_menu(phone_number: str, candidates: list[dict[str, Any]]) -> None:
    lines = [f"{i + 1}. {c['full_name']} ({c['user_type'].capitalize()})" for i, c in enumerate(candidates)]
    body = _msg("select_account") + "\n\n" + "\n".join(lines)
    send_text(phone_number, body)


_PATIENT_MENU = {
    "1": ("Encounter details", encounters.fetch_encounters),
    "2": ("Current medications", medications.fetch_medications),
    "3": ("Procedures", procedures.fetch_procedures),
    "4": ("Appointments", appointments.fetch_appointments),
    "5": ("Lab reports", lab_reports.fetch_lab_reports),
    "6": ("Patient summary", patient_summary.fetch_summary),
}

_STAFF_MENU = {
    **_PATIENT_MENU,
    "7": ("Patient lookup", None),
}


def _handle_authenticated(session: ConversationSession, phone_number: str, text: str) -> None:
    choice = text.strip()

    if choice == "0":
        session.logout()
        send_text(phone_number, _msg("logout_confirm"))
        return

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        send_text(phone_number, _msg("session_expired"))
        return

    user_type_str = str(session.user_type)
    menu = _STAFF_MENU if user_type_str == "staff" else _PATIENT_MENU
    entry = menu.get(choice)

    if not entry:
        _send_main_menu(phone_number, user_type_str)
        return

    label, fetcher = entry

    if fetcher is None:
        # Patient lookup -> transition to search state
        session.state = ConversationState.AWAITING_PATIENT_SEARCH  # pyright: ignore [reportAttributeAccessIssue]
        session.save(update_fields=["state"])
        send_text(phone_number, _msg("patient_search_prompt"))
        return

    try:
        result = fetcher(actor, session)
        send_text(phone_number, result)
    except PermissionDeniedError:
        logger.warning(
            "PermissionDenied: %s id=%s action=%s",
            actor.user_type,
            actor.instance.id,
            label,
        )
        send_text(phone_number, _msg("permission_denied"))
    except MissingContextError as exc:
        send_text(phone_number, str(exc))
    except NoDataError:
        send_text(phone_number, _msg("no_data", label=label.lower()))
    except DataFetchError as exc:
        logger.error("DataFetchError %s: %s", label, exc)
        send_text(phone_number, _msg("fetch_error"))

    _send_main_menu(phone_number, user_type_str)


def _handle_awaiting_patient_search(session: ConversationSession, phone_number: str, text: str) -> None:
    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        send_text(phone_number, _msg("session_expired"))
        return

    try:
        results = patient_lookup.search_patients(actor, text)
    except PermissionDeniedError:
        send_text(phone_number, _msg("permission_denied"))
        session.state = ConversationState.AUTHENTICATED  # pyright: ignore [reportAttributeAccessIssue]
        session.save(update_fields=["state"])
        return
    except NoDataError:
        send_text(phone_number, _msg("no_patients_found"))
        return

    session.candidates = results
    session.state = ConversationState.SELECTING_PATIENT  # pyright: ignore [reportAttributeAccessIssue]
    session.save(update_fields=["state", "candidates"])

    options = [f"{r['name']} — {r['phone_number']}" for r in results]
    send_text(
        phone_number,
        _msg("patient_search_results") + "\n\n" + "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options)),
    )


def _handle_selecting_patient(session: ConversationSession, phone_number: str, text: str) -> None:
    choice = text.strip()
    if not choice.isdigit():
        send_text(phone_number, _msg("invalid_choice"))
        return

    index = int(choice) - 1
    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore [reportAssignmentType]
    if index < 0 or index >= len(candidates):
        send_text(phone_number, _msg("invalid_choice"))
        return

    selected = candidates[index]
    session.active_patient_external_id = selected["external_id"]
    session.state = ConversationState.AUTHENTICATED  # pyright: ignore [reportAttributeAccessIssue]
    session.candidates = []
    session.save(update_fields=["state", "active_patient_external_id", "candidates"])

    send_text(
        phone_number,
        _msg("patient_selected", name=selected["name"]),
    )
    _send_main_menu(phone_number, str(session.user_type))


def _get_or_create_session(phone_number: str, provider: str) -> ConversationSession:
    session, _ = ConversationSession.objects.get_or_create(  # pyright: ignore[reportAttributeAccessIssue]
        phone_number=phone_number,
        provider=provider,
    )
    return session


def _extract_phone(payload: dict[str, Any]) -> str | None:
    raw = payload.get("from")
    if not raw:
        return None
    return raw if raw.startswith("+") else f"+{raw}"


def _extract_text(payload: dict[str, Any]) -> str | None:
    # WhatsApp Cloud API text message structure: {"text": {"body": "..."}}
    # A provider-agnostic abstraction layer should be added when more providers are supported.
    try:
        return payload.get("text", {}).get("body", "").strip()
    except AttributeError:
        return None


_MESSAGES: dict[str, str] = {
    "not_found": "Sorry, we couldn't find an account linked to your number.",
    "yob_prompt": "Please reply with your year of birth (e.g. 1990).",
    "yob_invalid": "Please enter a valid 4-digit year (e.g. 1990).",
    "yob_wrong": "That doesn't match. You have {remaining} attempt(s) remaining.",
    "locked": "Too many incorrect attempts. Please try again in 30 minutes.",
    "cooldown": "Your account is locked. Please try again in {minutes} minutes.",
    "select_account": "Multiple accounts found. Please select one by replying with its number:",
    "invalid_choice": "Please reply with a valid number from the list.",
    "choose_option": "Please choose an option:",
    "logout_confirm": "You have been logged out. Send any message to start again.",
    "session_expired": "Your session has expired. Please send any message to re-authenticate.",
    "permission_denied": "You don't have permission to view this information.",
    "no_data": "No {label} found on record.",
    "fetch_error": "Could not retrieve that information. Please try again.",
    "patient_search_prompt": "Enter the patient's phone number or name to search.",
    "patient_search_results": "Search results. Reply with the number to select:",
    "no_patients_found": "No patients found matching that search.",
    "patient_selected": "Viewing records for {name}. What would you like to see?",
    "greeting": "Hello, {name}! How can I help you today?",
}


def _msg(key: str, **kwargs: Any) -> str:
    template = _MESSAGES[key]
    return template.format(**kwargs) if kwargs else template
