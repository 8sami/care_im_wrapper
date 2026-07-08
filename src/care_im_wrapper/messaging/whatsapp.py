from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from care_im_wrapper.conversation.messages import OutboundMessage
from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
    WhatsAppTemplateNotConfiguredError,
)
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care_im_wrapper.models.notification import NotificationTemplate

logger = logging.getLogger(__name__)


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


class WhatsAppClient:
    supports_interactive: bool = True
    supports_templates: bool = True
    max_message_chars: int = int(plugin_settings.WHATSAPP_MESSAGE_CHAR_LIMIT)
    min_send_interval_seconds: int = int(plugin_settings.WHATSAPP_MIN_SEND_INTERVAL_SECONDS)

    def send_text(self, to: str, body: str) -> str | None:
        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": body},
            }
        )

    def send_interactive(self, to: str, msg: OutboundMessage) -> str | None:
        """
        Renders OutboundMessage.interactive as the exact Meta Cloud API JSON shape and sends it.
        Silently falls back to send_text() if msg.interactive is None.
        All field truncation happens here — callers must not pre-truncate.
        """
        from care_im_wrapper.conversation.messages import InteractiveType

        if msg.interactive is None:
            return self.send_text(to, msg.as_plain_text())

        iv = msg.interactive
        interactive_obj: dict[str, Any]

        if iv.type == InteractiveType.REPLY_BUTTONS:
            buttons = [
                {
                    "type": "reply",
                    "reply": {
                        "id": str(b["id"])[:256],
                        "title": str(b["title"])[: int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)],
                    },
                }
                for b in iv.action_data[:3]  # hard cap: max 3 buttons
            ]
            interactive_obj = {
                "type": "button",
                "body": {"text": iv.body},
                "action": {"buttons": buttons},
            }

        elif iv.type == InteractiveType.LIST:
            sections = []
            total_rows = 0
            for section in iv.action_data:
                rows = []
                for row in section.get("rows", []):
                    if total_rows >= int(plugin_settings.DATA_FETCH_LIMIT):  # hard cap: max 10 rows across all sections
                        break
                    entry: dict[str, Any] = {
                        "id": str(row["id"])[:256],
                        "title": str(row["title"])[: int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)],
                    }
                    if row.get("description"):
                        entry["description"] = str(row["description"])[
                            : int(plugin_settings.WHATSAPP_DESCRIPTION_TRUNCATE)
                        ]
                    rows.append(entry)
                    total_rows += 1
                if rows:
                    sections.append(
                        {
                            "title": str(section.get("title", ""))[: int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)],
                            "rows": rows,
                        }
                    )
            interactive_obj = {
                "type": "list",
                "body": {"text": iv.body},
                "action": {
                    "button": str(iv.button_label)[: int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)],
                    "sections": sections,
                },
            }

        elif iv.type == InteractiveType.CTA_URL:
            params = iv.action_data[0] if iv.action_data else {}
            interactive_obj = {
                "type": "cta_url",
                "body": {"text": iv.body},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": str(params.get("display_text", "Open"))[
                            : int(plugin_settings.WHATSAPP_TITLE_TRUNCATE)
                        ],
                        "url": str(params.get("url", "")),
                    },
                },
            }

        else:
            return self.send_text(to, msg.as_plain_text())

        if iv.header:
            interactive_obj["header"] = {"type": "text", "text": iv.header}
        if iv.footer:
            interactive_obj["footer"] = {"text": iv.footer}

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
            response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error_code: int | None = None
            try:
                if exc.response.content:
                    error_code = exc.response.json().get("error", {}).get("code")
            except (ValueError, KeyError):
                pass

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

    def _build_body_parameters(self, template: NotificationTemplate, merged_variables: dict) -> list[dict[str, Any]]:
        """
        Builds Meta's body `parameters` list. A template uses exactly one of two
        mutually-exclusive schemes (`template.parameter_format`), never a mix:
          POSITIONAL — `variable_mapping` keys are Meta's numbered slots ("1", "2", ...),
            substituted by list order; parameter objects carry no name.
          NAMED — `variable_mapping` keys are Meta's own `{{name}}` placeholder names,
            substituted by matching `parameter_name`; list order doesn't matter to Meta,
            but is still deterministic here (sorted) for stable request payloads/logging.
        """
        from care_im_wrapper.models.notification import TemplateParameterFormat

        if not template.variable_mapping:
            logger.error("WhatsApp send_template: template %s has no variable_mapping configured", template.slug)
            raise WhatsAppTemplateNotConfiguredError(
                f"NotificationTemplate '{template.slug}' has no variable_mapping configured"
            )

        variable_mapping: dict[str, str] = template.variable_mapping  # pyright: ignore[reportAssignmentType]
        is_named = template.parameter_format == TemplateParameterFormat.NAMED

        parameters: list[dict[str, Any]] = []
        for meta_key in sorted(variable_mapping) if is_named else sorted(variable_mapping, key=int):
            variable_name = variable_mapping[meta_key]
            if variable_name not in merged_variables:
                logger.warning(
                    "WhatsApp send_template: variable '%s' (%s %s) missing from merged_variables for template %s",
                    variable_name,
                    "parameter" if is_named else "position",
                    meta_key,
                    template.slug,
                )
            value = str(merged_variables.get(variable_name, ""))
            if is_named:
                parameters.append({"type": "text", "parameter_name": meta_key, "text": value})
            else:
                parameters.append({"type": "text", "text": value})
        return parameters

    def send_template(self, to: str, template: NotificationTemplate, merged_variables: dict) -> str | None:
        body_parameters = self._build_body_parameters(template, merged_variables)

        language_code = template.language_code
        if not language_code:
            logger.warning(
                "WhatsApp send_template: template %s has no language_code, falling back to en_US", template.slug
            )
            language_code = "en_US"

        template_obj: dict[str, Any] = {
            "name": template.slug,
            "language": {"code": language_code},
        }
        if body_parameters:
            template_obj["components"] = [
                {
                    "type": "body",
                    "parameters": body_parameters,
                }
            ]

        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": template_obj,
            }
        )

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
                response = httpx.get(url, params=params, headers=headers, timeout=10.0)
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
