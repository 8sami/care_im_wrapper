from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.auth.states import ConversationState
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
    logger.info("process_meta_status_update: channel=%s", channel)
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
        send_text(phone_number, _msg("cooldown", until=session.cooldown_until))
        return

    dispatch = {
        ConversationState.NEW: _handle_new,
        ConversationState.AWAITING_YOB: _handle_awaiting_yob,
        ConversationState.AMBIGUOUS: _handle_ambiguous,
        ConversationState.AUTHENTICATED: _handle_authenticated,
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
    session.candidates = [  # pyright: ignore[reportAttributeAccessIssue]
        {
            "user_type": i.user_type,
            "user_id": i.user_id,
            "year_of_birth": i.year_of_birth,
            "full_name": i.full_name,
            "phone_number": i.phone_number,
        }
        for i in result.identities
    ]
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
            send_text(phone_number, _msg("locked"))
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
        _send_main_menu(phone_number, match["user_type"])
        return

    # Multiple candidates share the same YOB —> persist the narrowed shortlist
    # and ask the user to pick one by number.
    session.candidates = shortlist  # pyright: ignore[reportAttributeAccessIssue]
    session.state = ConversationState.AMBIGUOUS  # pyright: ignore[reportAttributeAccessIssue]
    session.save(update_fields=["state", "candidates"])
    _send_candidate_menu(phone_number, shortlist)


def _handle_ambiguous(session: ConversationSession, phone_number: str, text: str) -> None:
    choice = text.strip()
    if not choice.isdigit():
        send_text(phone_number, _msg("invalid_choice"))
        return

    index = int(choice) - 1
    if index < 0 or index >= len(session.candidates):  # pyright: ignore[reportArgumentType]
        send_text(phone_number, _msg("invalid_choice"))
        return

    match = session.candidates[index]  # pyright: ignore[reportIndexIssue]
    session.authenticate(
        user_type=match["user_type"],
        user_id=match["user_id"],
        name=match["full_name"],
        phone=match["phone_number"],
    )
    _send_main_menu(phone_number, match["user_type"])


def _send_candidate_menu(phone_number: str, candidates: list[dict[str, Any]]) -> None:
    lines = [f"{i + 1}. {c['full_name']} ({c['user_type'].capitalize()})" for i, c in enumerate(candidates)]
    body = _msg("select_account") + "\n\n" + "\n".join(lines)
    send_text(phone_number, body)


def _handle_authenticated(session: ConversationSession, phone_number: str, text: str) -> None:
    # TODO: Week 3 — route menu selection to data handlers
    send_text(phone_number, _msg("menu_coming_soon"))


def _send_main_menu(phone_number: str, user_format: str) -> None:
    options = [
        "Encounters",
        "Medications",
        "Procedures",
        "Appointments",
        "Lab reports",
        "Patient summary",
    ]
    if user_format == "staff":
        options.append("Patient lookup")
    lines = [f"{i + 1}. {opt}" for i, opt in enumerate(options)]
    body = _msg("auth_success") + "\n\n" + "\n".join(lines)
    send_text(phone_number, body)


def _get_or_create_session(phone_number: str, provider: str) -> ConversationSession:
    session, _ = ConversationSession.objects.get_or_create(  # pyright: ignore[reportAttributeAccessIssue]
        phone_number=phone_number,
        provider=provider,
    )
    return session


def _extract_phone(payload: dict[str, Any]) -> str | None:
    # "from" is the WhatsApp Cloud API field for the sender's phone number.
    # A provider-agnostic abstraction layer should be added when more providers are supported.
    return payload.get("from")


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
    "cooldown": "Your account is locked until {until}. Please try again later.",
    "select_account": "Multiple accounts found. Please select one by replying with its number:",
    "invalid_choice": "Please reply with a valid number from the list.",
    "auth_success": "Identity verified. How can we help you today?",
    "menu_coming_soon": "Menu options coming soon.",
}


def _msg(key: str, **kwargs: Any) -> str:
    template = _MESSAGES[key]
    return template.format(**kwargs) if kwargs else template
