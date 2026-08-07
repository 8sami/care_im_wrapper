# care_im_wrapper — code review

Whole-repo audit of the plugin backend (`care_im_wrapper`) and frontend (`care_im_wrapper_fe`),
judged against CARE core and care_fe conventions.

Every finding below was produced by one pass and then re-checked by an independent agent
instructed to knock claims down rather than confirm them. Six claims did not survive that
pass; they are corrected or withdrawn here rather than quietly dropped. Verification status
is marked on each finding.

## Verification commands

| Check | Result |
|---|---|
| `make test` | 677 tests pass |
| `make lint` | clean (ruff check + format, 156 files) |
| `make docs` | `build succeeded.` — no warnings emitted |
| `make typecheck` | 455 errors / 9631 warnings (tests/ 5546, src/ 3313, scripts/ 1224, build/ 148) |
| `npm run lint` | 1 warning (`Pagination.tsx:27`) |
| `npx tsc --noEmit` | 1 error — `tsconfig.json`, not code |
| `npm run unimported` | **broken — binary not installed** |

---

## 0. Urgent, operational

**0.1 — Plaintext WhatsApp credentials in `plug_config.py`.**
The file holds live-looking `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_APP_SECRET` values. It is
git-tracked, but the secrets are **working-tree only**: `git status --porcelain` shows ` M`
(unstaged), and `git show HEAD:plug_config.py` contains no token — nothing is committed yet.
A `git add -A` / `git commit -a` would sweep them into history.
**Fix:** move to environment variables before any commit touches that file.

---

## 1. Bugs and security defects

### 1.1 — Template and trigger list endpoints have no permission gate. CONFIRMED.
`api/viewsets.py:107-108`, `:120-121`

Core's `EMRListMixin.list` (`care/emr/api/viewsets/base.py:154-173`) has no authorize hook —
it calls `get_queryset()` and serializes. Both viewsets return unfiltered querysets and only
`authorize_retrieve` checks `can_read_notification_template`. There is no `permission_classes`,
no permissions controller and no filter backend compensating. So **any authenticated CARE user
can list every notification template, including `payload` and `variable_mapping`.**

This needs no special configuration to hit, which is why it ranks first.

**Fix:** gate in `get_queryset()`, as `NotificationEventViewSet` already does at line 294.

### 1.2 — Session state commits before any message is sent; throttled retries compound. CONFIRMED.
`conversation/handlers.py:157` (atomic opens) vs `:183` (`_flush` called), `:186-192`

`_flush` sits **outside** the `with transaction.atomic():` block, so every session mutation is
durable before the first send is attempted. `_flush` re-raises `OutboundRateLimitedError` for
index 0, which `tasks.py:124-127` turns into `self.retry()` — replaying the whole handler
against already-mutated state.

Reproduced (rolled back afterwards): three throttled replays of a **single** inbound message
drove the session into a 30-minute cooldown without one reply ever being delivered.

```
run 1 raised OutboundRateLimitedError | committed failed_attempts=1
run 2 raised OutboundRateLimitedError | committed failed_attempts=2
run 3 raised OutboundRateLimitedError | committed failed_attempts=3 cooldown_until=…
```

No existing mechanism protects against this: message dedup is gated on `self.request.retries == 0`
(`tasks.py:114`) so retries bypass it, and the revoke in `handlers/meta.py:32-49` targets a
`pending_task:` key that `tasks.py:120` deletes at the start of every run.

This also contradicts the contract documented at `messaging/registry.py:121-124`:

> "Throttling mid-turn aborts the turn after earlier messages are already delivered, **the
> caller's transaction rolls the session back as though they weren't**, and the retry replays
> every send."

That rollback does not happen. A stale comment asserting a safety property the code lacks is
worse than no comment. (`_flush` also *swallows* rate limits for index > 0 and never retries there.)

**Fix:** move `_flush` inside the atomic block, or make the retry idempotent by keying it on the
inbound message id.

### 1.3 — Raw phone numbers written to logs. CONFIRMED (observed in test output).
`tasks.py:126, 129, 132`

```
logger.info("Outbound rate-limited for %s on %s. Retrying in %ss.", phone_number, ...)
logger.warning("Transient WhatsApp error for %s: %s. Retrying.", phone_number, exc)
logger.error("Permanent WhatsApp error for %s: %s. Dropping message.", phone_number, exc)
```

Not inference — the test suite itself prints `Permanent WhatsApp error for +919876543210: …`.
`handlers/meta.py:28` does it correctly with `mask_phone_number(...)`, so the convention exists
and these three sites skip it. Related: `messaging/registry.py:134` builds
`OutboundRateLimitedError(f"Outbound send to {to} …")` with the raw number, which Celery logs on retry.

**Fix:** mask at all three sites; drop the number from the exception message.

### 1.4 — Manual dispatch clears live claims. CONFIRMED (real window).
`api/viewsets.py:411`

```python
instance.recipients.filter(latest_status__isnull=True).update(dispatch_started_at=None)
```

`tasks.py:191` only claims when `retries == 0`, and SENT is written at `:257-263`. The gap
between them is the HTTP send itself — a worker mid-`send_template_message` has
`dispatch_started_at` set and `latest_status` still null, so its claim is cleared and a second
task is queued for the same recipient → duplicate WhatsApp message.

**Fix:** clear only claims older than `DISPATCH_CLAIM_STALE_SECONDS`, matching
`dispatch_pending_notification_recipients`.

### 1.5 — Child-org staff locked out of notifications. CONFIRMED, configuration-dependent.
`security/authorization.py:24-26, 40-43`; `api/viewsets.py:68-70`

Reproduced in a rolled-back transaction:

```
PLUGIN  can_read_notification_event (child-org nurse): False
CORE    can_read_invoice_in_facility  (child-org nurse): True
CORE    can_list_booking_on_facility  (child-org nurse): True
PLUGIN  can_read_notification_event (root-org nurse):  True
```

The plugin passes `orgs=[root_org_id]` to `check_permission_in_facility_organization`, which
filters `FacilityOrganizationUser` on `organization_id__in=orgs`
(`care/security/authorization/base.py:50-51`) — admitting only users with a **direct membership
row on the root org**. Core's facility-wide idiom passes `facility=` instead
(`care/security/authorization/invoice.py:14-19`, `booking.py:22-25`), filtering on
`organization__facility` with no org-id restriction, so root *or* any child org matches.

`can_read_notification_event` is granted to `STAFF_ROLE`, `DOCTOR_ROLE`, `NURSE_ROLE`
(`security/permissions.py:27-32`), so a nurse attached to a department org holds the permission
but gets `PermissionDenied` from `viewsets.py:71-72` — the entire events list 403s. The FE
compounds it: `NotificationEventsPage.tsx:132-139` gates buttons on `facility.permissions`, which
core computes as **root ∪ child** (`care/emr/resources/permissions.py:51-53`), so a child-org user
sees "New notification" / "Dispatch" and gets 403 on click.

Nothing in core auto-creates a root membership — only the facility *creator* gets one
(`facility.py:252-255`) — so child-org-only membership is a supported configuration. But the
seeded dev DB has zero such users and the plugin's tests only attach at `self.root_org`
(`test_api_notifications.py:89`), which is why this is untested and config-dependent.

**Fix:** pass `facility=` rather than `orgs=[root_id]`. Note `viewsets.py:68-70` hardcodes root-org
resolution independently and needs the same treatment, or the list still 403s.

> Corrected during verification: an earlier draft cited `Encounter.sync_organization_cache`
> (`care/emr/models/encounter.py:43-58`) as the convention and proposed unioning `parent_cache`.
> That is the wrong analogue — encounters union ancestors because they are tagged to specific
> departments via `EncounterOrganization`, whereas a `NotificationEvent` resolves to a facility
> and has no child org. Since `root.parent_cache == []`, that fix would have been a no-op. The
> earlier draft also misattributed the list 403 to `NotificationEvent.save()` and the detail 403
> to queryset inheritance; `viewsets.py:291-292` explicitly returns the unscoped queryset for
> non-list actions, so the detail 403 comes from `authorize_retrieve` at `:299-301`.

### 1.6 — "Back" row silently dropped from the patient picker. CONFIRMED mechanism, low reachability.
`conversation/handlers.py:549`

`_run_patient_search` is the only one of four `picker_reply` call sites (`:550, 630, 790, 1051`)
that trims by characters and lines but never by `limits.max_rows`. Others clamp explicitly —
`:622` uses `limits.max_rows - 1`, `:754` uses `max_rows - reserved_rows - paging_row_cost`.

Reproduced end-to-end:

```
n=9 : picker_reply built 10 rows -> provider sent 10, back present=True
n=10: picker_reply built 11 rows -> provider sent 10, back present=False
```

`picker_reply` appends Back last (`replies.py:301-304`) and `whatsapp.py:201` breaks at
`total_rows >= max_interactive_rows`. Typing `0` still works (`handlers.py:565-568`) but nothing
on screen says so.

**Reachability is low.** It needs ten *blank* phone numbers on one page, because any non-empty
description makes each choice two lines and `fit_to_budget` then trims to 7. Blank numbers are
not creatable via the CARE API — `PatientBaseSpec.phone_number` is required
(`care/emr/resources/patient/spec.py:64`) — only via direct ORM, import or fixtures.

**Fix:** clamp in `_run_patient_search`, or enforce `limits.max_rows` inside `picker_reply` so a
*choice* is dropped rather than the Back row.

---

## 2. Provider logic outside the provider layer

**2.1 — Retry semantics keyed on WhatsApp exception types. CONFIRMED.**
`tasks.py:25-32, 58, 128, 131`. The agnostic task layer imports five `WhatsApp*` exceptions and
classifies on them. A second provider's permanent error falls into the generic `except Exception`
(`:133`, `:224`) and burns all three retries — the opposite of the documented intent.
**Fix:** provider-neutral bases (`PermanentSendError`, `TransientSendError`, `PairRateLimitError`)
in `messaging/exceptions.py`, with `WhatsApp*` subclassing them.

**2.2 — `conversation/renderers.py:33`** reads `plugin_settings.WHATSAPP_TRUNCATE_RESERVE_CHARS`
inside the agnostic renderer layer.

**2.3 — `messaging/limits.py:141-160`** — `default_limits()`, the fallback for a channel that has
not described itself, reads `WHATSAPP_*` settings for **9** of its 13 fields.

**2.4 — `settings.py:89-94`** — `REQUIRED_SETTINGS` is four WhatsApp keys, validated in
`PluginSettings.__init__` (`:37`, `:70-77`). Verified: with `PLUGIN_CONFIGS={}` and the env
cleared, importing raises `ImproperlyConfigured`. A deployment running only a second provider
cannot boot without Meta credentials.

**2.5 — `conversation/messages.py:23-26`** — `InteractivePayload`'s docstring hardcodes Meta's
caps ("max 3 items", "max 10 rows total", "exactly 1 item") in the agnostic dataclass.

---

## 3. Duplicate and redundant code

**3.1 — The same helper twice, imported side by side. CONFIRMED.**
`handlers/dispatch.py:31-48` `track_previous_field(field_name)` and `:51-58`
`track_previous_status` are behaviourally identical (same query, same attribute name).
`handlers/billing.py:21-22` imports **both**, using one at `:93` and the other at `:115`.
Exactly the repeat offender AGENTS.md calls out.
**Fix:** delete `track_previous_status`; call sites become `track_previous_field("status")` with `weak=False`.

**3.2 — Two truncation implementations.**
`conversation/renderers.py:29-34` `_truncate` vs `messaging/limits.py:100-118` `clamp`. Different
markers; only `clamp` is grapheme-safe. `_truncate` stays under budget only because
`WHATSAPP_TRUNCATE_RESERVE_CHARS` (20, `settings.py:99`) exceeds the 16-char suffix — lower that
configurable value and it overflows.

**3.3 — FE/BE variable-mapping validation written twice. They agree today.**
`messaging/whatsapp.py:142-154` vs `care_im_wrapper_fe/src/lib/notificationTemplateValidation.ts:23-33`.

Verified rather than assumed: zod 4.4.3 applies `.trim()` before `.refine()`s, `@hookform/resolvers`
5.4.0 returns parsed (transformed) data, and the page sends `values.variables[index]`
(`:426-437`) — so no dangerous divergence. The backend is authoritative and additionally validates
Jinja syntax and field existence (`reports/validation.py:102, 110-113`), which the FE does not.

Two structural issues remain: the provider key `"whatsapp"` is hardcoded on both sides with no
shared source, and one asymmetry — the FE schema is `z.array(...).length(count)`
(`NotificationTemplateVariablesPage.tsx:117`), requiring *every* placeholder filled, while the
backend saves partial mappings and `whatsapp.py:417` skips unmapped placeholders at send.
**Fix:** serve the provider's rules from the existing `.../schema/` endpoint the FE already calls.

**3.4 — Redundant accessors.** `whatsapp.py:126, 130, 134` duplicate `self.limits.*`, and
`send_interactive` mixes both styles (`:187`, `:201`). `interactive_body_char_limit` (`:130`) has
**zero call sites anywhere** — outright dead.

---

## 4. Unused and dead code

- `reports/context_builders.py:209` `AccountContext` — zero references across four repos; not a
  `target_context=` of any Field, not in any registry.
- `models/conversation_session.py:301-306` `reset_data_page()` — zero callers; superseded by
  `_reset_paging()` (`:149`, seven call sites). Also **stale**: it fails to clear `data_shown`.
- `care_im_wrapper_fe/src/components/ui/popover.tsx` — zero importers. care_fe has **0** unused
  `ui/` primitives out of 59, so vendoring one is not house style.
- `data/base.py:12` `ACTIVE_MEDICATION_STATUSES` — dead *inside the plugin*, but it mirrors a live
  care_fe constant (`src/types/emr/medicationRequest/medicationRequest.ts:78`, used in 8 files).
  **Delete it or use it** — not simply delete.

**Clean:** all 66 `DEFAULTS` settings keys are referenced outside `settings.py`; all 114 i18n keys
are referenced in `src/` and `public/locale/en.json` is correctly sorted.

---

## 5. Deviation from CARE / care_fe conventions

This axis is close to clean. The backend uses `EMRBaseViewSet` + the right mixins, Pydantic
`EMRResource` specs, `AuthorizationController.call(...)` with registered permissions, and
`get_object_or_404` from `care.utils.shortcuts`. Ruff is clean at line-length 120. Both `@action`
naming hazards are correctly handled — `variable_schema` with `url_path="schema"` (`:189`) and
`dispatch_recipients` with `url_path="dispatch"` (`:396`); neither shadows `APIView.schema` or
`View.dispatch`. The FE uses raviger, TanStack Query, the `request.ts` helpers, shadcn primitives,
sonner, and routes all user-facing copy through react-i18next.

Real items: **1.1** and **1.5** above, plus:

- `src/lib/permissions.ts` is a 3-line `includes()` helper, where care_fe centralises this in
  `src/common/Permissions.tsx` (18KB of named constants) plus `src/context/PermissionContext.tsx`.
  Raw strings at each call site fail open on a typo.
- `conversation/menus.py:43, 63-127` hardcodes menu labels and descriptions as English literals
  while sibling menu chrome (`"back"`, `"menu_button"`, `"back_to_main_menu"`) lives in `_MESSAGES`
  (`templates.py:46, 55, 80, 86`). An internal inconsistency worth fixing.

  > Corrected during verification: an earlier draft claimed `templates.py:5-13` declares that menu
  > labels belong in `_MESSAGES`. It does not — that block is a *WhatsApp markdown* convention
  > (`*bold*` / `_italic_` / `plain`), and "menu labels" appears only as an example of what receives
  > no markdown. The finding stands on the inconsistency alone.

**Not a finding:** FE permission *sourcing* is correct — `auth.user.permissions` for GENERIC-context
permissions (populated at `care/emr/resources/user/spec.py:161, 192`) and `facility.permissions` for
FACILITY-context ones, matching the backend's `PermissionContext` split. Dispatch actions do have
confirmation dialogs. `reports/schema.py:1-5` forks core code with an explicit written justification.

---

## 6. Comment quality

**6.1 — ~14 docstrings truncated mid-sentence.** Systematic; reads like a bulk edit lopped off every
summary line. Line numbers below are the docstring, not the `def`:

| Location | Reads |
|---|---|
| `renderers.py:48` | "…may itself be a `titled()` block,." |
| `data/base.py:17` | "…a value like 'in_progress' or." |
| `data/base.py:41` | "…\"Cardiology Location\",." |
| `handlers/dispatch.py:52` | "…on the instance, so the paired." |
| `whatsapp.py:392` | "…placeholders, plus a dynamic." |
| `whatsapp.py:433` | "…so the audit record cannot." |
| `context_builders.py:14` | "…one attribute of its parent, e.g." |
| `context_builders.py:72` | "…of the appointment_confirmed/cancelled/." |
| `context_builders.py:107` | "…but the reminder template names." |
| `context_builders.py:319` | "…Populated by each." |
| `context_builders.py:174`, `:218` | same defect |
| `pagination.py:67` | "…not a page index --." |
| `pagination.py:95` | "…is NoDataError;." |
| `conversation_session.py:97` | "Logs out a session idle past." |
| `conversation_session.py:288` | "…remembering where this one." |

**6.2 — `conversation/messages.py:44`** — `# status-update correlation, optional for now`, dangling
under `InboundMessage` and describing a field that does not exist.

**6.3 — `settings.py:14`** — `PLUGIN_NAME = "care_im_wrapper"  # was causing circular import`.
Records a past symptom, not a decision.

**6.4 — `messaging/registry.py:121-124`** — documents a transaction-rollback guarantee that does not
exist (see 1.2). The most damaging comment in the repo.

**Inverse check passed.** The genuinely surprising code is well explained: `limits.py:68`
justifies approximating UAX #29 rather than taking a `regex` dependency; `documents/views.py:21`
explains per-token rather than per-IP rate limiting; `viewsets.py:288` explains why only `list`
reads the facility param. Keep those.

---

## 7. UX and DX

**7.1 — `models/conversation_session.py:70-81`** declares both
`UniqueConstraint(fields=["phone_number","provider"])` and an identical `Index`. Verified in live
Postgres: a duplicate btree exists alongside the constraint's own. None of core's five
`UniqueConstraint` `Meta` blocks pairs one with a matching index — not house style. Dead weight on
every write.

**7.2 — Test settings reach Meta's live API.** Credentials flow `plug_config.py` →
`config/settings/base.py:146` (`PLUGIN_CONFIGS = manager.get_config()`) → inherited wholesale by
`config/settings/test.py`. Nothing stubs `WhatsAppClient._send`; there is no `conftest.py`, no socket
blocker, no `responses`/`respx`. `_send` (`whatsapp.py:262-265`) raises only when credentials are
*absent*, so real ones proceed to `httpx.post`. The suite stays offline purely by per-test patching
discipline — an ad-hoc script against an allow-listed number would send a real message to a real
person. (Observed during this review: a probe reached Meta and was rejected only by the allow-list.)
**Fix:** point `WHATSAPP_API_URL` at an unroutable host in test settings, or hard-fail `_send` under test.

**7.3 — Unfixable save from stale mapping keys.** `NotificationTemplateVariablesPage.tsx:429-437`
re-submits "extras" (keys with no form field); `:406` maps server errors via `keys.indexOf(key)`;
`reports/validation.py:130` validates **every** submitted key. So an invalid extra produces a generic
toast with no field to correct and no way to drop the key — the save can never succeed from the UI.

**7.4 — `npm run unimported` does not work.** `package.json:13` declares the script but `unimported`
is in neither `dependencies` nor `devDependencies` and is not in `node_modules/.bin`
(`sh: 1: unimported: not found`). There is no `.unimportedrc.json`.

**7.5 — `/new` route not permission-gated.** `routes.tsx:25-29` registers
`/facility/:facilityId/settings/notifications/new`; `NotificationCreateEventPage.tsx:50`
(`useFacilityAccessGuard`) is the only guard and the file contains no `hasPermission` call, while
`NotificationEventsPage.tsx:132` gates the button. The backend does enforce
(`viewsets.py:303-306`), so this is a dead-end, not a hole: the user fills the whole form, then 403s.

**7.6 — `tsconfig.json:8`** — `baseUrl` deprecation makes `npx tsc --noEmit` exit 2 (TS5101) with no
code error, so a clean typecheck cannot be used as a gate.

**7.7 — Stale `build/` directory** is walked by basedpyright (148 diagnostics).

---

## Withdrawn

**The `.claude/worktrees/` typecheck claim.** An earlier draft asserted the worktree inflates the
typecheck numbers. It does not: `.claude/` is gitignored (`.gitignore:211`) and ruff honours
gitignore (`make lint` reports exactly 156 files), and
`make typecheck 2>&1 | grep -c "\.claude/worktrees"` returns **0**. The 455/9631 figure comes
entirely from real sources. The proposed remediation would have been a no-op. The nearest real
version of it is 7.7 above.
