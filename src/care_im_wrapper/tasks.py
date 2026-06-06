import logging

from celery import current_app, shared_task

from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.auth.states import ConversationState
from care_im_wrapper.auth.validator import validate_year_of_birth
from care_im_wrapper.messaging.whatsapp import send_interactive_menu, send_text
from care_im_wrapper.models import ConversationSession

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_meta_message(self, payload: dict, channel: str) -> None:
    logger.info("process_meta_message: channel=%s", channel)
    try:
        _run_state_machine(payload, channel)
    except Exception as exc:
        logger.error("process_meta_message failed: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_meta_status_update(self, payload: dict, channel: str) -> None:
    # TODO: Week 6 — notification status tracking
    logger.info("process_meta_status_update: channel=%s", channel)
    try:
        pass
    except Exception as exc:
        logger.error("process_meta_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # TODO: Week 6 — notification scheduling tasks
    pass


def _run_state_machine(payload: dict, channel: str) -> None:
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
    handler = dispatch.get(session.state)
    if handler:
        handler(session, phone_number, text)
    else:
        logger.error("process_meta_message: unhandled state %s", session.state)


def _handle_new(session, phone_number: str, text: str) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found:
        send_text(phone_number, _msg("not_found"))
        return
    if result.ambiguous:
        session.state = ConversationState.AMBIGUOUS
        session.save(update_fields=["state"])
        send_interactive_menu(phone_number, _msg("ambiguous"), ["Patient", "Staff"])
        return
    session.state = ConversationState.AWAITING_YOB
    session.save(update_fields=["state"])
    send_text(phone_number, _msg("yob_prompt"))


def _handle_awaiting_yob(session, phone_number: str, text: str) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found or not result.identities:
        session.state = ConversationState.NEW
        session.save(update_fields=["state"])
        return
    identity = next(
        (i for i in result.identities if i.user_type == session.user_type or session.user_type == "unknown"),
        result.identities[0],
    )
    if validate_year_of_birth(text, identity):
        session.authenticate(
            user_type=identity.user_type,
            user_id=identity.user_id,
            name=identity.full_name,
            phone=identity.phone_number,
        )
        _send_main_menu(phone_number, identity.user_type)
    else:
        session.increment_failed_attempt()
        if session.state == ConversationState.COOLDOWN:
            send_text(phone_number, _msg("locked"))
        else:
            remaining = 5 - session.failed_attempts
            send_text(phone_number, _msg("yob_wrong", remaining=remaining))


def _handle_ambiguous(session, phone_number: str, text: str) -> None:
    choice = text.strip()
    role_map = {"1": "patient", "2": "staff"}
    role = role_map.get(choice)
    if not role:
        send_text(phone_number, _msg("invalid_choice"))
        return
    session.user_type = role
    session.state = ConversationState.AWAITING_YOB
    session.save(update_fields=["state", "user_type"])
    send_text(phone_number, _msg("yob_prompt"))


def _handle_authenticated(session, phone_number: str, text: str) -> None:
    # TODO: Week 3 — route menu selection to data handlers
    send_text(phone_number, _msg("menu_coming_soon"))


def _send_main_menu(phone_number: str, user_format: str) -> None:
    options = [
        "My encounters",
        "My medications",
        "My procedures",
        "My appointments",
        "My lab reports",
        "My patient summary",
    ]
    if user_format == "staff":
        options.append("Patient lookup")
    send_interactive_menu(phone_number, _msg("auth_success"), options)


def _get_or_create_session(phone_number: str, provider: str):

    session, _ = ConversationSession.objects.get_or_create(
        phone_number=phone_number,
        provider=provider,
    )
    return session


def _extract_phone(payload: dict) -> str | None:
    return payload.get("from")


def _extract_text(payload: dict) -> str | None:
    try:
        return payload.get("text", {}).get("body", "").strip()
    except AttributeError:
        return None


_MESSAGES = {
    "not_found": "Sorry, we couldn't find an account linked to your number.",
    "yob_prompt": "Please reply with your year of birth (e.g. 1990).",
    "yob_wrong": "That doesn't match. You have {remaining} attempt(s) remaining.",
    "locked": "Too many incorrect attempts. Please try again in 30 minutes.",
    "cooldown": "Your account is $\\{until\\}. Please try again later.",
    "ambiguous": "We found multiple accounts for your number. Are you a:\n1. Patient\n2. Staff member",
    "invalid_choice": "Please reply with 1 or 2.",
    "auth_success": "Identity verified. How can we help you today?",
    "menu_coming_soon": "Menu options coming soon.",
}


def _msg(key: str, **kwargs) -> str:
    template = _MESSAGES.get(key, "Something went wrong.")
    return template.format(**kwargs) if kwargs else template
