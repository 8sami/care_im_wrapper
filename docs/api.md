# REST API

The plugin registers four routers under `/api/care_im_wrapper/`, plus two plain paths that
are not part of the REST surface — a provider webhook and a document redirect. The routers
are ordinary `EMRBaseViewSet`s, so they appear in CARE's own OpenAPI schema alongside core's
endpoints — there is no separate schema to serve or host.

| Where | URL |
| --- | --- |
| Swagger UI | `/swagger/` |
| ReDoc | `/redoc/` |
| OpenAPI schema (YAML) | `/api/schema/` |

To regenerate the schema as a file:

```bash
docker compose exec backend python manage.py spectacular --file schema.yaml
```

## Endpoints

| Route | Methods | Purpose |
| --- | --- | --- |
| `notification-triggers/` | list, retrieve | The events that can fire a notification. Read-only; seeded by migrations. |
| `notification-templates/` | list, retrieve | Provider templates synced from Meta. |
| `notification-templates/{id}/toggle_active/` | POST | Enable/disable a template. |
| `notification-templates/{id}/schema/` | GET | Field schema for the variable picker. |
| `notification-templates/{id}/set_variable_mapping/` | POST | Save a placeholder → expression mapping. |
| `notification-templates/{id}/preview_variable_mapping/` | POST | Dry-run a mapping against a preview stub. |
| `notification-templates/sync/` | POST | Queue a pull of the provider's template catalogue. |
| `notification-events/` | list, retrieve, create | Fired notifications. |
| `notification-events/{id}/dispatch/` | POST | Queue pending recipients for delivery. |
| `notification-recipients/` | list, retrieve | Per-recipient delivery log and status history. |

`notification-triggers/` is read-only: the viewset mixes in only `EMRListMixin` and
`EMRRetrieveMixin`, so a trigger cannot be created or edited over the API.

## Non-REST endpoints

Two paths under the same prefix are not viewsets, are not in the OpenAPI schema, and are not
authenticated by CARE's session or JWT layers. Each carries its own proof instead.

| Route | Methods | Authenticated by | Purpose |
| --- | --- | --- | --- |
| `webhook/meta/` | GET | `hub.verify_token` matching `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Meta's subscription handshake; echoes `hub.challenge`. |
| `webhook/meta/` | POST | `X-Hub-Signature-256`, HMAC-SHA256 over the raw body with `WHATSAPP_APP_SECRET` | Inbound messages and delivery-status callbacks. |
| `documents/<token>/` | GET | possession of the opaque token | Redirects to a freshly minted presigned URL for the document. |

The webhook URL registered with Meta is this path on your deployment:

```
https://<your-care-host>/api/care_im_wrapper/webhook/meta/
```

A POST with a missing or wrong signature is rejected before the payload is read. The
document token is the durable capability — it carries no patient identifier, expires after
`DOCUMENT_LINK_TTL_SECONDS`, and is rate limited to `DOCUMENT_LINK_RATE_LIMIT_MAX` hits per
window. Unknown, malformed and expired tokens all return an identical 404, so the endpoint
cannot be used to probe which documents exist.

## Permissions

Authorization goes through CARE's `AuthorizationController`; the plugin registers its own
handler in {py:mod}`care_im_wrapper.security.authorization`.

| Permission | Context | Grants |
| --- | --- | --- |
| `can_read_notification_template` | Generic | Read templates and their field schema. |
| `can_manage_notification_template` | Generic | Toggle, sync, and set variable mappings. |
| `can_read_notification_event` | Facility | Read events and recipients. |
| `can_create_notification_event` | Facility | Create a manual-trigger event. |
| `can_dispatch_notification_event` | Facility | Trigger delivery of pending recipients. |

## Facility scoping

`notification-events/` and `notification-recipients/` are scoped by a `facility` query
parameter carrying a facility's external id. It is **required for every caller except a
superuser**, who may omit it to read across all facilities — the authorization layer cannot
enumerate "every org this user can see" without one.

Scoping applies to *lists* only. A detail route addresses one event, whose own
`facility_organization_cache` is the better scope, and is authorized per-object instead.

:::{note}
An `@action` may not be named `schema`, `dispatch`, or anything else that collides with a
DRF view attribute. `schema` shadows the `AutoSchema` descriptor drf-spectacular reads and
takes down `/api/schema/` for the whole of CARE; `dispatch` shadows `View.dispatch` and
breaks routing. Both are avoided by naming the method apart from its `url_path`.
:::

## Notification variables

A template's `variable_mapping` maps each provider placeholder to a Jinja expression
evaluated against the trigger's context object:

```json
{
  "1": "{{ object.patient.name }}",
  "2": "{{ object.token_slot.start_datetime|date }}",
  "3": "{{ doctor_name }}"
}
```

`object` is the trigger's `related_object`; bare names come from the context's
`extra_context_fields`. Both are enumerated by the `schema/` endpoint and checked by
{py:func}`care_im_wrapper.reports.validation.validate_variable_mapping` before saving, so an
unknown field is refused at authoring time rather than rendering blank on a patient's phone.
