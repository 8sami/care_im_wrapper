from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import httpx
from jinja2 import UndefinedError

from care_im_wrapper.conversation.messages import InboundMessage, OutboundMessage, SentTemplate, StatusUpdate
from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
    WhatsAppTemplateNotConfiguredError,
)
from care_im_wrapper.messaging.limits import ChannelLimits, clamp, whatsapp_limits
from care_im_wrapper.messaging.variables import resolve_variable
from care_im_wrapper.models.notification import NotificationStatusState
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care_im_wrapper.models.notification import NotificationTemplate

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
# Full-string {{ ... }} wrap, the shape resolve_variable() evaluates.
_DOUBLE_BRACE_EXPRESSION_RE = re.compile(r"^\{\{([\s\S]*)\}\}$")

# Meta's hard cap on an interactive button/row id length; a longer id is rejected outright.
_INTERACTIVE_ID_MAX_CHARS = 256


def _resolve_choice(
    choices_cls: type, meta_value: str, *, overrides: dict[str, Any] | None = None, default: Any
) -> Any:
    """Maps a Meta-vocabulary string onto a `TextChoices` member: `overrides` first, then a case-insensitive match."""
    if overrides and meta_value in overrides:
        return overrides[meta_value]
    try:
        return choices_cls(meta_value.lower())
    except ValueError:
        return default


def normalize_meta_message(payload: dict[str, Any], channel: str) -> InboundMessage | None:
    """Normalizes a WhatsApp Cloud API (Meta) message payload."""
    raw_phone = payload.get("from")
    if not raw_phone:
        return None
    phone_number = raw_phone if raw_phone.startswith("+") else f"+{raw_phone}"

    msg_type = payload.get("type")

    if msg_type == "text":
        try:
            text = payload.get("text", {}).get("body", "").strip()
        except AttributeError:
            return None

    elif msg_type == "interactive":
        interactive = payload.get("interactive", {})
        interactive_type = interactive.get("type")
        if interactive_type == "button_reply":
            text = interactive.get("button_reply", {}).get("id", "").strip()
        elif interactive_type == "list_reply":
            text = interactive.get("list_reply", {}).get("id", "").strip()
        else:
            logger.debug("normalize_meta_message: unhandled interactive subtype %r, dropping", interactive_type)
            return None

    else:
        logger.debug("normalize_meta_message: unhandled message type %r, dropping", msg_type)
        return None

    if not text:
        return None

    return InboundMessage(
        phone_number=phone_number,
        text=text,
        channel=channel,
        raw_id=payload.get("id"),
    )


_META_STATUS_MAP: dict[str, NotificationStatusState] = {
    "sent": NotificationStatusState.SENT,
    "delivered": NotificationStatusState.DELIVERED,
    "read": NotificationStatusState.READ,
    "failed": NotificationStatusState.FAILED,
}


def normalize_meta_status(payload: dict[str, Any], channel: str) -> StatusUpdate | None:
    """Normalizes a WhatsApp Cloud API (Meta) status webhook entry."""
    tracking_id = payload.get("id")
    if not tracking_id:
        logger.warning("normalize_meta_status: missing id, dropping")
        return None

    status = payload.get("status") or ""
    state = _META_STATUS_MAP.get(status)
    if state is None:
        logger.warning("normalize_meta_status: unrecognized status %r, dropping", status)
        return None

    return StatusUpdate(tracking_id=tracking_id, state=state, raw_payload=payload)


class WhatsAppClient:
    supports_interactive: bool = True
    supports_templates: bool = True

    @property
    def max_message_chars(self) -> int:
        return int(plugin_settings.WHATSAPP_MESSAGE_CHAR_LIMIT)

    @property
    def min_send_interval_seconds(self) -> int:
        return int(plugin_settings.WHATSAPP_MIN_SEND_INTERVAL_SECONDS)

    @property
    def limits(self) -> ChannelLimits:
        """Every field cap in one object, read fresh so overrides apply."""
        return whatsapp_limits()

    def validate_variable_mapping_value(self, expr: str) -> list[str]:
        """Validates one variable_mapping expression against Meta's rules."""
        errors: list[str] = []
        if not expr.strip():
            return ["Expression is required."]
        if re.search(r"[\n\r\t]", expr):
            errors.append("Value must not contain newlines or tabs.")
        if re.search(r" {5,}", expr):
            errors.append("Value must not contain 5 or more consecutive spaces.")
        match = _DOUBLE_BRACE_EXPRESSION_RE.match(expr.strip())
        if not match or not match.group(1).strip():
            errors.append("Value must be a Jinja2 expression wrapped in {{ ... }}.")
        return errors

    def declared_placeholders(self, template: NotificationTemplate) -> list[str]:
        """Every placeholder the approved template body requires a value for.

        The same HEADER/BODY scan _build_components does, plus the fixed url_suffix key a
        dynamic URL button is addressed through. Meta rejects a template message whose
        parameter count does not match the approved body, so a mapping that covers only
        some of these cannot produce a valid send.
        """
        payload_components = (template.payload or {}).get("components", [])  # pyright: ignore[reportAttributeAccessIssue]
        keys: list[str] = []
        for comp in payload_components:
            comp_type = comp.get("type", "").upper()
            if comp_type == "BUTTONS":
                for button in comp.get("buttons", []):
                    if button.get("type", "").upper() == "URL" and _PLACEHOLDER_RE.findall(button.get("url", "")):
                        keys.append("url_suffix")
                continue
            if comp_type not in ("HEADER", "BODY") or comp.get("format", "TEXT").upper() != "TEXT":
                continue
            keys.extend(_PLACEHOLDER_RE.findall(comp.get("text", "")))
        return list(dict.fromkeys(keys))

    def send_text(self, to: str, body: str) -> str | None:
        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": clamp(body, self.limits.text_body)},
            }
        )

    def send_interactive(self, to: str, msg: OutboundMessage) -> str | None:
        """Renders OutboundMessage.interactive as the exact Meta Cloud API JSON shape and sends it."""
        from care_im_wrapper.conversation.messages import InteractiveType

        if msg.interactive is None:
            return self.send_text(to, msg.as_plain_text())

        iv = msg.interactive
        limits = self.limits
        interactive_obj: dict[str, Any]

        if iv.type == InteractiveType.REPLY_BUTTONS:
            buttons = [
                {
                    "type": "reply",
                    "reply": {
                        "id": str(b["id"])[:_INTERACTIVE_ID_MAX_CHARS],
                        "title": clamp(b["title"], limits.button_title),
                    },
                }
                for b in iv.action_data[: limits.max_buttons]
            ]
            interactive_obj = {
                "type": "button",
                "body": {"text": clamp(iv.body, limits.interactive_body)},
                "action": {"buttons": buttons},
            }

        elif iv.type == InteractiveType.LIST:
            sections = []
            total_rows = 0
            for section in iv.action_data:
                rows = []
                for row in section.get("rows", []):
                    if total_rows >= limits.max_rows:
                        break
                    entry: dict[str, Any] = {
                        "id": str(row["id"])[:_INTERACTIVE_ID_MAX_CHARS],
                        "title": clamp(row["title"], limits.row_title),
                    }
                    if row.get("description"):
                        entry["description"] = clamp(row["description"], limits.row_description)
                    rows.append(entry)
                    total_rows += 1
                if rows:
                    sections.append(
                        {
                            "title": clamp(section.get("title", ""), limits.section_title),
                            "rows": rows,
                        }
                    )
            interactive_obj = {
                "type": "list",
                "body": {"text": clamp(iv.body, limits.interactive_body)},
                "action": {
                    "button": clamp(iv.button_label, limits.list_button_label),
                    "sections": sections,
                },
            }

        elif iv.type == InteractiveType.CTA_URL:
            params = iv.action_data[0] if iv.action_data else {}
            interactive_obj = {
                "type": "cta_url",
                "body": {"text": clamp(iv.body, limits.interactive_body)},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": clamp(params.get("display_text", "Open"), limits.button_title),
                        "url": str(params.get("url", "")),
                    },
                },
            }

        else:
            return self.send_text(to, msg.as_plain_text())

        if iv.footer:
            interactive_obj["footer"] = {"text": clamp(iv.footer, limits.interactive_footer)}

        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": interactive_obj,
            }
        )

    def _send(self, payload: dict[str, Any]) -> str | None:
        token = plugin_settings.WHATSAPP_ACCESS_TOKEN
        phone_id = plugin_settings.WHATSAPP_PHONE_NUMBER_ID
        api_url = plugin_settings.WHATSAPP_API_URL

        if not token or not phone_id:
            raise RuntimeError(
                "WhatsApp credentials (WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID) are not configured"
            )

        url = f"{api_url}/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=float(plugin_settings.WHATSAPP_HTTP_TIMEOUT_SECONDS)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error_code: int | None = None
            try:
                if exc.response.content:
                    error_code = exc.response.json().get("error", {}).get("code")
            except (ValueError, KeyError):
                pass

            logger.error("WhatsApp API error (HTTP %s): %s", status_code, exc.response.text)

            if status_code == 429 or error_code == 131056:
                raise WhatsAppPairRateLimitError(f"WhatsApp rate limit hit: {exc.response.text}") from exc
            if 400 <= status_code < 500:
                raise WhatsAppBadRequestError(f"WhatsApp permanent error ({status_code}): {exc.response.text}") from exc
            if status_code >= 500:
                raise WhatsAppServerError(f"WhatsApp server error ({status_code}): {exc.response.text}") from exc
            return None
        except httpx.RequestError as exc:
            raise WhatsAppNetworkError(f"WhatsApp network/timeout error: {exc}") from exc

        try:
            message_id = response.json().get("messages", [{}])[0].get("id")
        except (ValueError, IndexError, AttributeError):
            message_id = None

        if message_id is None:
            logger.warning("WhatsApp _send: response did not contain a message id: %s", response.text)

        return message_id

    def _build_parameters(
        self,
        meta_keys: list[str],
        template: NotificationTemplate,
        related_object: Any,
        context: dict[str, Any],
        *,
        is_named: bool,
    ) -> list[dict[str, Any]]:
        variable_mapping: dict[str, str] = template.variable_mapping  # pyright: ignore[reportAssignmentType]
        parameters: list[dict[str, Any]] = []
        for meta_key in meta_keys:
            try:
                value = resolve_variable(variable_mapping[meta_key], related_object, context)
            except UndefinedError as exc:
                logger.error(
                    "WhatsApp send_template: template %s parameter %r references an undefined value in mapping %r: %s",
                    template.slug,
                    meta_key,
                    variable_mapping[meta_key],
                    exc,
                )
                raise WhatsAppTemplateNotConfiguredError(
                    f"NotificationTemplate '{template.slug}' parameter '{meta_key}' references an undefined "
                    f"value in mapping '{variable_mapping[meta_key]}': {exc}"
                ) from exc
            text = clamp(value, self.limits.template_parameter)
            if not text.strip():
                logger.error(
                    "WhatsApp send_template: template %s parameter %r resolved to an empty value from mapping %r",
                    template.slug,
                    meta_key,
                    variable_mapping[meta_key],
                )
                raise WhatsAppTemplateNotConfiguredError(
                    f"NotificationTemplate '{template.slug}' parameter '{meta_key}' resolved to an empty "
                    f"value from mapping '{variable_mapping[meta_key]}'; WhatsApp rejects blank parameters."
                )
            if is_named:
                parameters.append({"type": "text", "parameter_name": meta_key, "text": text})
            else:
                parameters.append({"type": "text", "text": text})
        return parameters

    def _build_button_components(
        self,
        buttons_component: dict[str, Any],
        template: NotificationTemplate,
        related_object: Any,
        context: dict[str, Any],
        variable_mapping: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Meta URL-button params are always positional, whatever the body's parameter_format."""
        button_components: list[dict[str, Any]] = []
        for index, button in enumerate(buttons_component.get("buttons", [])):
            if button.get("type", "").upper() != "URL":
                continue
            if not _PLACEHOLDER_RE.findall(button.get("url", "")):
                continue  # static URL button, nothing dynamic to fill in

            if "url_suffix" not in variable_mapping:
                logger.error(
                    "WhatsApp send_template: template %s has a dynamic URL button but no "
                    "'url_suffix' in variable_mapping",
                    template.slug,
                )
                raise WhatsAppTemplateNotConfiguredError(
                    f"NotificationTemplate '{template.slug}' has a dynamic URL button with no 'url_suffix' mapping"
                )

            value = resolve_variable(variable_mapping["url_suffix"], related_object, context)
            button_components.append(
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": str(index),
                    "parameters": [{"type": "text", "text": value}],
                }
            )
        return button_components

    def _build_components(
        self, template: NotificationTemplate, related_object: Any, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Builds Meta's `components` list: HEADER and BODY text placeholders, plus a dynamic
        URL button when the template has one."""
        from care_im_wrapper.models.notification import TemplateParameterFormat

        if not template.variable_mapping:
            logger.error("WhatsApp send_template: template %s has no variable_mapping configured", template.slug)
            raise WhatsAppTemplateNotConfiguredError(
                f"NotificationTemplate '{template.slug}' has no variable_mapping configured"
            )

        variable_mapping: dict[str, str] = template.variable_mapping  # pyright: ignore[reportAssignmentType]
        is_named = template.parameter_format == TemplateParameterFormat.NAMED
        payload_components = (template.payload or {}).get("components", [])  # pyright: ignore[reportAttributeAccessIssue]

        components: list[dict[str, Any]] = []
        for comp in payload_components:
            comp_type = comp.get("type", "").upper()

            if comp_type == "BUTTONS":
                components.extend(
                    self._build_button_components(comp, template, related_object, context, variable_mapping)
                )
                continue

            if comp_type not in ("HEADER", "BODY") or comp.get("format", "TEXT").upper() != "TEXT":
                continue
            placeholder_keys = [k for k in _PLACEHOLDER_RE.findall(comp.get("text", "")) if k in variable_mapping]
            if not placeholder_keys:
                continue
            meta_keys = sorted(placeholder_keys) if is_named else sorted(placeholder_keys, key=int)
            components.append(
                {
                    "type": comp_type.lower(),
                    "parameters": self._build_parameters(
                        meta_keys, template, related_object, context, is_named=is_named
                    ),
                }
            )
        return components

    @staticmethod
    def _flatten_components(components: list[dict[str, Any]]) -> dict[str, str]:
        """Resolved values read back off the components we send, so the audit record cannot
        drift from what actually went on the wire."""
        flat: dict[str, str] = {}
        for comp in components:
            if comp.get("type") == "button":
                params = comp.get("parameters", [])
                if params:
                    flat["url_suffix"] = params[0].get("text", "")
                continue
            for position, param in enumerate(comp.get("parameters", []), start=1):
                flat[param.get("parameter_name") or str(position)] = param.get("text", "")
        return flat

    def send_template(
        self, to: str, template: NotificationTemplate, related_object: Any, context: dict[str, Any]
    ) -> SentTemplate:
        components = self._build_components(template, related_object, context)

        language_code = template.language_code
        if not language_code:
            language_code = str(plugin_settings.WHATSAPP_DEFAULT_LANGUAGE_CODE)
            logger.warning(
                "WhatsApp send_template: template %s has no language_code, falling back to %s",
                template.slug,
                language_code,
            )

        template_obj: dict[str, Any] = {
            "name": template.slug,
            "language": {"code": language_code},
        }
        if components:
            template_obj["components"] = components

        message_id = self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": template_obj,
            }
        )
        return SentTemplate(tracking_id=message_id, parameters=self._flatten_components(components))

    def list_templates(self) -> list[dict[str, Any]]:
        token = plugin_settings.WHATSAPP_ACCESS_TOKEN
        api_url = plugin_settings.WHATSAPP_API_URL
        business_account_id = plugin_settings.WHATSAPP_BUSINESS_ACCOUNT_ID

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url: str | None = f"{api_url}/{business_account_id}/message_templates"
        params: dict[str, Any] | None = None
        templates: list[dict[str, Any]] = []

        while url is not None:
            try:
                response = httpx.get(
                    url, params=params, headers=headers, timeout=float(plugin_settings.WHATSAPP_HTTP_TIMEOUT_SECONDS)
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code >= 500:
                    raise WhatsAppServerError(f"WhatsApp server error ({status_code}): {exc.response.text}") from exc
                raise WhatsAppServerError(f"WhatsApp error ({status_code}): {exc.response.text}") from exc
            except httpx.RequestError as exc:
                raise WhatsAppNetworkError(f"WhatsApp network/timeout error: {exc}") from exc

            body = response.json()
            templates.extend(body.get("data", []))
            url = body.get("paging", {}).get("next")
            params = None

        return templates

    def upsert_synced_templates(self, raw_templates: list[dict[str, Any]]) -> None:
        from care_im_wrapper.models import ConversationSession
        from care_im_wrapper.models.notification import (
            NotificationCategory,
            NotificationTemplate,
            TemplateApprovalStatus,
            TemplateParameterFormat,
        )

        status_overrides = {"APPROVED": TemplateApprovalStatus.ACTIVE}

        for template in raw_templates:
            NotificationTemplate.objects.update_or_create(
                slug=template["name"],
                provider=ConversationSession.Provider.WHATSAPP,
                defaults={
                    "name": template["name"],
                    "category": _resolve_choice(
                        NotificationCategory, template.get("category", ""), default=NotificationCategory.UTILITY
                    ),
                    "approval_status": _resolve_choice(
                        TemplateApprovalStatus,
                        template.get("status", ""),
                        overrides=status_overrides,
                        default=TemplateApprovalStatus.DISABLED,
                    ),
                    "language_code": template.get("language"),
                    "parameter_format": _resolve_choice(
                        TemplateParameterFormat,
                        template.get("parameter_format", "positional"),
                        default=TemplateParameterFormat.POSITIONAL,
                    ),
                    "payload": template,
                },
            )

    def sync_templates(self) -> None:
        self.upsert_synced_templates(self.list_templates())
