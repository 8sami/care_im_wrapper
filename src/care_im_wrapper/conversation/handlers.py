from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from care_im_wrapper.auth.actor import resolve_actor
from care_im_wrapper.auth.resolver import resolve_phone_number
from care_im_wrapper.conversation.menus import _PATIENT_MENU, _STAFF_MENU
from care_im_wrapper.conversation.messages import InteractivePayload, InteractiveType, OutboundMessage
from care_im_wrapper.conversation.renderers import render_patient_search_results
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data import patient_lookup
from care_im_wrapper.data.exceptions import (
    DataFetchError,
    InvalidQueryError,
    MissingContextError,
    NoDataError,
    PermissionDeniedError,
)
from care_im_wrapper.messaging.registry import get_interactive_body_char_limit, get_max_chars, send_message
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def _menu_rows(menu: dict[str, Any]) -> list[dict[str, str]]:
    """Builds interactive list rows from a menu dict, plus the trailing Logout row."""
    rows = [{"id": key, "title": entry[0]} for key, entry in menu.items()]
    rows.append({"id": "0", "title": _msg("logout")})
    return rows


def _menu_text(rows: list[dict[str, str]]) -> str:
    """Renders menu rows as a numbered plain-text fallback for non-interactive display."""
    return "\n".join(f"{r['id']}. {r['title']}" for r in rows)


def run_state_machine(phone_number: str, text: str, channel: str) -> None:
    with transaction.atomic():  # pyright: ignore[reportGeneralTypeIssues]
        session, created = ConversationSession.objects.select_for_update().get_or_create(  # pyright: ignore[reportAttributeAccessIssue]
            phone_number=phone_number,
            provider=channel,
        )

        if session.is_in_cooldown():
            send_message(channel, phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes()))
            return

        dispatch = {
            ConversationSession.State.NEW: _handle_new,
            ConversationSession.State.AWAITING_YOB: _handle_awaiting_yob,
            ConversationSession.State.AMBIGUOUS: _handle_ambiguous,
            ConversationSession.State.AUTHENTICATED: _handle_authenticated,
            ConversationSession.State.AWAITING_PATIENT_SEARCH: _handle_awaiting_patient_search,
            ConversationSession.State.SELECTING_PATIENT: _handle_selecting_patient,
        }
        handler = dispatch.get(session.state)  # pyright: ignore[reportArgumentType]
        if handler:
            handler(session, phone_number, text, channel)
        else:
            logger.error("run_state_machine: unhandled state %s", session.state)


def _handle_new(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    result = resolve_phone_number(phone_number)
    if not result.found:
        send_message(channel, phone_number, _msg("not_found"))
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
    send_message(channel, phone_number, _msg("yob_prompt"))


def _handle_awaiting_yob(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    stripped = text.strip()
    if not stripped.isdigit() or len(stripped) != 4:
        send_message(channel, phone_number, _msg("yob_invalid"))
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
            send_message(channel, phone_number, _msg("cooldown", minutes=session.get_cooldown_remaining_minutes()))
        else:
            remaining = int(plugin_settings.MAX_FAILED_ATTEMPTS) - int(session.failed_attempts)  # pyright: ignore[reportOperatorIssue, reportArgumentType]
            send_message(channel, phone_number, _msg("yob_wrong", remaining=remaining))
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
    session.state = ConversationSession.State.AMBIGUOUS
    session.save(update_fields=["state", "candidates"])
    _send_candidate_menu(phone_number, shortlist, channel)


def _handle_ambiguous(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    choice = text.strip()

    if choice.startswith("candidate_"):
        try:
            index = int(choice.removeprefix("candidate_")) - 1
        except ValueError:
            send_message(channel, phone_number, _msg("invalid_choice"))
            return
    elif choice.isdigit():
        # plain-text fallback path (non-interactive provider or typed digit)
        index = int(choice) - 1
    else:
        send_message(channel, phone_number, _msg("invalid_choice"))
        return

    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore[reportAssignmentType]
    if index < 0 or index >= len(candidates):
        send_message(channel, phone_number, _msg("invalid_choice"))
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
        send_message(channel, phone_number, _msg("logout_confirm"))
        return

    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        send_message(channel, phone_number, _msg("session_expired"))
        return

    menu = _STAFF_MENU if session.user_type == ConversationSession.UserType.STAFF.value else _PATIENT_MENU
    entry = menu.get(choice)

    if not entry:
        send_message(channel, phone_number, _msg("invalid_choice"))
        return

    label, fetcher, renderer = entry

    if fetcher is None:
        session.state = ConversationSession.State.AWAITING_PATIENT_SEARCH
        session.save(update_fields=["state"])
        send_message(channel, phone_number, _msg("patient_search_prompt"))
        return

    try:
        data = fetcher(actor, session)
        renderer_msg = renderer(data, get_max_chars(channel))

        menu_rows = _menu_rows(menu)

        greeting = _msg("choose_option")
        menu_items_text = _menu_text(menu_rows)
        full_text = f"{renderer_msg.text}\n\n{greeting}\n\n{menu_items_text}"

        interactive_payload = InteractivePayload(
            type=InteractiveType.LIST,
            body=greeting,
            button_label=_msg("view_menu"),
            action_data=[{"title": _msg("menu_title"), "rows": menu_rows}],
        )

        limit = get_interactive_body_char_limit(channel)

        if len(renderer_msg.text) + len(greeting) > limit:
            # Fallback: Send data as plain text, then menu separately.
            send_message(channel, phone_number, OutboundMessage(text=renderer_msg.text))
            send_message(channel, phone_number, OutboundMessage(text=greeting, interactive=interactive_payload))
        else:
            # Single message: data + greeting in interactive body (avoiding redundant menu list)
            interactive_payload = InteractivePayload(
                type=interactive_payload.type,
                body=f"{renderer_msg.text}\n\n{greeting}",
                action_data=interactive_payload.action_data,
                button_label=interactive_payload.button_label,
                header=interactive_payload.header,
                footer=interactive_payload.footer,
            )
            send_message(channel, phone_number, OutboundMessage(text=full_text, interactive=interactive_payload))

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
            channel=channel,
            prefix=_msg("permission_denied"),
        )
    except MissingContextError as exc:
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel=channel,
            prefix=str(exc),
        )
    except NoDataError:
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel=channel,
            prefix=_msg("no_data", label=label.lower()),
        )
    except DataFetchError as exc:
        logger.error("DataFetchError %s: %s", label, exc)
        _send_main_menu(
            phone_number,
            str(session.user_type),
            channel=channel,
            prefix=_msg("fetch_error"),
        )


def _handle_awaiting_patient_search(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    actor = resolve_actor(session)
    if actor is None:
        session.logout()
        send_message(channel, phone_number, _msg("session_expired"))
        return

    try:
        results = patient_lookup.search_patients(actor, text)
    except PermissionDeniedError:
        send_message(channel, phone_number, _msg("permission_denied"))
        session.state = ConversationSession.State.AUTHENTICATED
        session.save(update_fields=["state"])
        return
    except InvalidQueryError as exc:
        # Stay in AWAITING_PATIENT_SEARCH so the next message is retried as a search query.
        send_message(channel, phone_number, str(exc))
        return
    except NoDataError:
        send_message(channel, phone_number, _msg("no_patients_found"))
        return

    session.candidates = results
    session.state = ConversationSession.State.SELECTING_PATIENT
    session.save(update_fields=["state", "candidates"])

    prompt = _msg("patient_search_results")
    plain_options = [f"{r['name']} — {r['phone_number']}" for r in results]
    msg = render_patient_search_results(prompt, plain_options, get_max_chars(channel))

    if len(results) <= 3:
        buttons = [{"id": f"patient_{i}", "title": r["name"]} for i, r in enumerate(results)]
        interactive = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body=prompt,
            action_data=buttons,
        )
    else:
        rows = [
            {"id": f"patient_{i}", "title": r["name"], "description": r["phone_number"]} for i, r in enumerate(results)
        ]
        interactive = InteractivePayload(
            type=InteractiveType.LIST,
            body=prompt,
            button_label=_msg("select_patient"),
            action_data=[{"title": _msg("patients_title"), "rows": rows}],
        )

    send_message(channel, phone_number, OutboundMessage(text=msg.text, interactive=interactive))


def _handle_selecting_patient(session: ConversationSession, phone_number: str, text: str, channel: str) -> None:
    choice = text.strip()

    if choice.startswith("patient_"):
        try:
            index = int(choice.removeprefix("patient_"))
        except ValueError:
            send_message(channel, phone_number, _msg("invalid_choice"))
            return
    elif choice.isdigit():
        # plain-text fallback path — numbered_list() uses 1-based display
        index = int(choice) - 1
    else:
        send_message(channel, phone_number, _msg("invalid_choice"))
        return

    candidates: list[dict[str, Any]] = session.candidates  # pyright: ignore[reportAssignmentType]
    if index < 0 or index >= len(candidates):
        send_message(channel, phone_number, _msg("invalid_choice"))
        return

    selected = candidates[index]
    session.active_patient_external_id = selected["external_id"]
    session.state = ConversationSession.State.AUTHENTICATED
    session.candidates = []
    session.save(update_fields=["state", "active_patient_external_id", "candidates"])
    _send_main_menu(
        phone_number,
        str(session.user_type),
        channel=channel,
        prefix=_msg("patient_selected", name=selected["name"]),
    )


def _send_main_menu(
    phone_number: str, user_type: str, channel: str, name: str | None = None, prefix: str | None = None
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
    send_message(channel, phone_number, msg)


def _send_candidate_menu(phone_number: str, candidates: list[dict[str, Any]], channel: str) -> None:
    prompt = _msg("select_account")
    plain_lines = [f"{i + 1}. {c['full_name']} ({c['user_type'].capitalize()})" for i, c in enumerate(candidates)]
    plain_text = prompt + "\n\n" + "\n".join(plain_lines)

    if len(candidates) <= 3:
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

    send_message(channel, phone_number, OutboundMessage(text=plain_text, interactive=interactive))
