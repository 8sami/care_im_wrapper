# Encounter-scoped navigation: matching care_fe's data hierarchy

## Why this document exists

care_fe organises clinical data in two levels: a **patient** level (demographics,
encounters, appointments) and an **encounter** level (medicines, service requests,
diagnostic reports, …). Medications nest one level deeper still, under a **prescription**.
The backend models mostly agree: `MedicationRequest`, `MedicationRequestPrescription` and
`DiagnosticReport` carry a non-null `encounter` FK. **`ServiceRequest.encounter` is
nullable** (`care/emr/models/service_request.py:22`), so a service request recorded without
one is reachable from no encounter — the same blind spot care_fe's `service_requests` tab
has, since it queries by encounter id.

`care_im_wrapper` ignores that hierarchy entirely. Every fetcher in `data/` filters on
`patient=` alone, so a chat user sees every procedure, report and medication from every
visit to every facility flattened into a single list. Prescriptions exist only as invisible
grouping headers inside `group_medications` — there is no way to select one, and no
equivalent of care_fe's "All prescriptions / View all medications" control.

This plan restructures the conversation menu into the same two-level hierarchy, adds an
encounter picker and a prescription picker, and scopes the affected fetchers.

**Status: applied.** Migration `0021_conversationsession_encounter_scope`.

---

## Where each menu option lives in care_fe

Sources: `src/pages/Encounters/EncounterShow.tsx:120` (encounter tabs),
`src/pages/Patient/home/PatientHomeTabs.tsx:39` (patient tabs),
`src/pages/Patient/PatientHome.tsx` (`PatientInfoCard`).

| care_im_wrapper option | care_fe home | Level |
| --- | --- | --- |
| Medications | Encounter → `medicines` tab → prescription sidebar | encounter → prescription |
| Procedures | Encounter → `service_requests` tab | encounter |
| Lab reports | Encounter → `diagnostic_reports` tab | encounter |
| Encounter details | PatientHome → `encounters` tab | patient |
| Appointments | PatientHome → `appointments` tab | patient |
| Patient summary | `PatientInfoCard` — persistent header, **not a tab** | chrome |
| Patient lookup | facility patient search | — |

care_fe also exposes `updates`, `plots`, `observations`, `responses`, `files`, `notes`,
`consents` and `devices` at encounter level. The wrapper implements a deliberate subset;
the sub-menu below leaves room for them.

`TokenBooking` *does* carry an encounter FK — `associated_encounter`, nullable
(`care/emr/models/scheduling/booking.py:37`). Appointments still stay patient-scoped,
because care_fe lists them on PatientHome rather than on an encounter tab. The placement
follows care_fe's information architecture, not the absence of a column.

care_fe's PatientHome also has a third tab, `tokens`, shown only on small breakpoints. Not
mirrored here.

---

## Current shape (as-is)

```
Main menu (flat, 6 options + 1 staff-only)
  1. Encounter details  → list of all encounters → pick one → discharge summary PDF
  2. Medications        → every MedicationRequest for the patient, grouped by prescription
  3. Procedures         → every ServiceRequest for the patient
  4. Appointments       → every TokenBooking for the patient
  5. Lab reports        → every DiagnosticReport for the patient
  6. Patient summary    → demographics
  7. Patient lookup     → staff only
  0. Logout
```

Menu entries are 4-tuples `(label, fetcher, renderer, document_resolver)` in
`conversation/menus.py`. `_handle_authenticated` looks one up, calls the fetcher, renders
a page, and re-sends the menu with `Next`/`Previous` rows folded in by `_rows_with_paging`
(`conversation/handlers.py:91`).

---

## Target shape (to-be)

```
MAIN MENU                              ENCOUNTER SUB-MENU
                                       Encounter: City Hospital — 12 Jul 2026

1. Encounters      ──picker──►         1. Medications
2. Appointments                        2. Procedures
3. Patient summary                     3. Lab reports
4. Patient lookup   (staff only)       4. Discharge summary
0. Logout                              5. Change encounter
                                       0. Back to main menu
```

Main menu = PatientHome's tabs plus the info card. Sub-menu = EncounterShow's tabs. The
mapping is one-to-one.

5 rows for staff (4 for a patient) and 6 rows against WhatsApp's 10-row cap
(`DEFAULT_MAX_INTERACTIVE_ROWS`, `settings.py`), leaving headroom for the tabs listed
above. Pinned by `test_menus.py::MenuRowBudgetTests`.

### Medications branch

```
1. Medications
   │
   ├─ 0 or 1 prescription in this encounter → straight to the medication list
   │     (filtered to "all", never to the single prescription: a medication with
   │      `prescription=NULL` would otherwise be silently hidden)
   │
   └─ 2+ prescriptions → prescription picker
            a. All prescriptions            (description: "View all medications")
            1. 12 Jul 2026, 10:30 am        (description: "Prescribed by: Dr. Anita Rao")
            2. 12 Jul 2026, 09:15 am        (description: "Prescribed by: Dr. S. Menon")
            0. Back
                 ↓
            medication list → back to encounter sub-menu
```

Row titles use `formatDateTime(created_date, "DD/MM/YYYY hh:mm A")` with `prescribed_by`
as the description — the same two fields as care_fe's cards in
`PrescriptionListSelector.tsx`.

---

## Decisions and rationale

### Nested menus, not flat scope-resolution

The rejected alternative kept one flat menu and resolved the encounter lazily on first use
of an encounter-scoped option, keeping it sticky thereafter.

Rejected because the context becomes **invisible state**. A user picks Medications, is
asked for an encounter, and nothing signals that the choice now silently governs Procedures
and Lab reports too. On a text-only medical channel a staff member could read a stale
encounter's data without noticing. Nesting makes the context something you deliberately
enter and leave.

Nesting also *removes* machinery: no `pending_menu_choice`, no partially-resolved menu
option threaded through two picker handlers.

Cost: one extra tap to reach medications the first time. Every subsequent option within the
same encounter is a single tap, identical to flat.

### `Encounter details` is absorbed by the encounter picker

The picker renders the same list with the same `render_encounters`, and sits on the path to
every encounter-scoped option, so the encounter list is never unreachable. What option 1
uniquely provided was the **discharge summary PDF** (`resolve_encounter_document`), which
becomes `4. Discharge summary` in the sub-menu.

This also retires the encounter branch of `_enter_document_selection`: the encounter is
already chosen, so the resolver is called directly. Lab reports keep their document
pick-list — one encounter can hold many reports.

### `Patient summary` is kept

It is the only option with no tab equivalent — it maps to `PatientInfoCard`, which is
always-visible chrome in care_fe. A WhatsApp menu header can afford roughly one line before
it eats the interactive body budget, and the card carries name, DOB, gender, blood group
and phone. Blood group and phone appear nowhere else in the wrapper, so the full card stays
as a menu option and the header carries a condensed identity line.

### Prescription is a transient filter, not a third nav level

care_fe agrees: `PrescriptionListSelector` is a sidebar *within* the medicines tab, and
`MedicationRequestTable/index.tsx` resets `selectedPrescriptionId` on encounter change
because it is local component state, not navigation.

So the encounter is sticky context; the prescription is a per-viewing choice. It must still
survive `n`/`p` within one open list, so it persists on the session with the same lifecycle
`data_menu_choice` already has: set when picked, reset when Medications is re-entered,
cleared on encounter change / patient change / logout.

### Deliberate divergence: picker-first instead of all-first

care_fe's default is *All prescriptions* — `selectedPrescriptionId` starts `undefined` and
the sidebar narrows from there. That is its desktop behaviour; on mobile (`lg:hidden`) the
sidebar collapses to a drawer behind a button, which is the behaviour this plan mirrors.

Picker-first is the better fit for chat: an encounter's full medication list can run several
pages, and choosing upfront beats paging through everything to find one prescription. Worth
raising with mentors, as a side-by-side desktop comparison will show the difference.

### `a` for "All prescriptions", not a number

care_fe lists it first and it should be the most prominent row here too, but the numbered
prescriptions are numbered from the page offset (`page.offset + 1`). A leading `1.` for All
would desync the plain-text fallback from the interactive rows.

### Sequential sub-menu keys

`5. Change encounter` follows `4`, with no gap reserved for the unimplemented tabs. A gap
reads to a user as missing options. If a tab is added later it takes 5 and Change encounter
moves to 6 — the menu is re-sent in full every turn, so there is no muscle memory to
protect.

`0` for back follows existing convention: `templates.py` already defines
`"back": "Back to menu"` and the document picker appends it as `id: "0"`. The main menu
keeps `0` as Logout; in the sub-menu `0` reads "Back to main menu" and leaves the encounter
rather than logging out.

---

## Pagination redesign

Paging rows and menu rows used to compete inside one 10-row list, and `_rows_with_paging`
dropped the paging rows wholesale when the combination overflowed.

**Navigation is never content.** Paging is reply buttons; list rows carry only things worth
choosing:

- **Unpaginated reply** → data plus the menu list, unchanged from before.
- **Paginated data list** → data plus `[Previous] [Next] [Menu]` buttons. Over the interactive
  body limit the data goes out as plain text first and the buttons follow on their own, so a
  long page never loses its controls.
- **Paginated picker** → the selectable rows, then a second message carrying
  `[Previous] [Next]`. A picker's rows have to stay selectable, and one interactive message
  cannot hold rows and buttons at once. No `Menu` button here: the rows already end in *Back*.

Three buttons is exactly `max_buttons` (`ChannelLimits`). A provider with fewer falls back to
paging rows inside the list, plus the typed `n`/`p` hint in the body -- the only case where
navigation and content share a list.

Cost: one extra message per paged picker page. Typing `n` / `p` works throughout.

## Data model changes

`ConversationSession` gains:

| Field | Purpose |
| --- | --- |
| `menu_context` | `main` \| `encounter` — which menu `AUTHENTICATED` is showing |
| `active_encounter_external_id` | sticky encounter scope |
| `active_encounter_label` | pre-rendered header line, avoids a refetch per turn |
| `active_prescription_external_id` | `""` unresolved, `__all__` sentinel, or a UUID |
| `active_prescription_label` | as above |
| `active_encounter_has_alternatives` | whether "Change encounter" has anywhere to go |
| `active_patient_label` | the name the scope line reports, for a staff member viewing someone |

New states: `SELECTING_ENCOUNTER`, `SELECTING_PRESCRIPTION`.

The model owns every transition, so no handler can forget half of one -- `update_fields` is
derived from what was actually assigned rather than typed out beside it:
`open_encounter`, `clear_encounter_scope`, `set_prescription_scope`, `switch_patient`,
`offer`, `select`, `close_selection`, `return_to_menu`, `start_patient_search`.

`offer` stores each candidate with the row id and the printed number that select it; `select`
resolves a reply against those. The two differ -- row ids are positional, numbers continue
across pages -- and recomputing either one from paging state is what made a typed number mean
the wrong record once a picker sat on top of an open list.

One migration (`0021_...`), additive only. No data migration: a session mid-list at deploy
time restarts at the main menu on its next turn, matching the precedent set by
`0020_conversationsession_data_offsets`.

---

## Module-by-module changes

### `conversation/menus.py`

Replace the 4-tuple with a frozen `MenuOption` dataclass carrying `label`, `fetcher`,
`renderer`, `document_resolver` and `scope`. Add a `Scope` enum
(`PATIENT` / `ENCOUNTER` / `PRESCRIPTION`). Define `_MAIN_MENU`, `_STAFF_MAIN_MENU` and
`_ENCOUNTER_MENU`; a `menu_for(session)` helper resolves user type × `menu_context`.

Unpacking sites to update: `_handle_authenticated`, `_handle_selecting_document`.

### `data/common.py`

Add `resolve_target_encounter(actor, session)` — resolves
`active_encounter_external_id` **filtered by the resolved patient**, so a stale or guessed
external_id cannot reach another patient's encounter. Same defence
`resolve_target_patient` already applies. Raises `MissingContextError` when unset or not
found. Add the `ALL_PRESCRIPTIONS` sentinel.

### `data/procedures.py`, `data/lab_reports.py`

Add `encounter=` to the queryset filter. Because `ServiceRequest.encounter` is nullable,
procedures with no encounter become unreachable — matching care_fe, and called out in the
fetcher's docstring rather than papered over.

### `data/medications.py`

`fetch_prescriptions` gains `encounter=`, plus
`.filter(prescription__external_id=...)` when a specific prescription is selected. The
existing `.exclude(prescription__status=ENTERED_IN_ERROR_STATUS)` stays.

New `fetch_prescription_choices` over `MedicationRequestPrescription` filtered by
patient + encounter, excluding `entered_in_error`, `select_related("prescribed_by")`,
ordered `-created_date, -id`.

`group_medications` is untouched. Filtering to one prescription simply yields one group, so
the existing render already prints that prescription's header — meaning **the data reply
needs no scope line for the prescription**. Only the encounter line is added.

Medications with `prescription=NULL` (the FK is `null=True, on_delete=SET_NULL`) keep their
existing date-group fallback and appear only under *All prescriptions*.

### `data/base.py`

`_build_cache_key` currently keys on function, actor type, actor id, patient and offset.
Without encounter and prescription, picking prescription A then B serves A's cached page at
the same offset. Add both; bump `_CACHE_SCHEMA_VERSION` 3 → 4 so existing entries do not
leak across the change.

### `data/records.py`, `conversation/renderers.py`, `conversation/templates.py`

Add `PrescriptionChoiceRecord`. New message keys for the two pickers, the sub-menu, the scope
line, `all_prescriptions` / `view_all_medications` (names taken from care_fe's i18n keys), and
the `Menu` button.

`renderers.py` loses the picker renderers: what a picker offers is described once, in
`handlers`, and written out by `replies.choices_as_text`.

### `conversation/replies.py` (new)

Composing a reply -- how it splits across messages, where paging goes, what a provider that
cannot draw rows sees instead -- was decided independently at six send sites, which is how
they drifted apart. It now happens once, against `ChannelLimits`, in two functions:
`menu_reply` (rows are chrome) and `picker_reply` (rows are the content).

`Choice` is the other half. A selectable record has to exist in four forms at once -- an
interactive row, the number printed beside it, the candidate stored on the session, and the
plain-text fallback -- and every one of them is derived from a single `describe(record)` per
picker. That is what makes it structurally impossible for the rows and the fallback to
describe different things.

### `conversation/handlers.py`

- `_handle_authenticated` dispatches on `menu_context`.
- New `_handle_selecting_encounter`, `_handle_selecting_prescription` -- both support
  `n` / `p` and `0`.
- Every state transition moved onto `ConversationSession`; no handler assigns `session.state`
  or `session.candidates` directly any more.
- `_scope_line` prepends the context to every reply.

---

## Test plan

Update: `test_menus.py`, `test_procedures.py`, `test_lab_reports.py`, `test_medications.py`,
`test_medications_integration.py`, `test_handlers_authenticated_dispatch.py`,
`test_handlers_authenticated_success.py`, `test_handlers_paging.py`,
`test_handlers_document_pull.py`, `test_handlers_diagnostic_report.py`, `test_data_base.py`
(cache key), `test_renderers.py`.

Add:

- encounter picker: zero / one (auto-select) / many encounters; paging; `0` back
- prescription picker: zero / one (skipped) / many; `a` for all; paging
- scoping: a fetcher must not return another encounter's rows
- cross-patient guard: a forged encounter external_id belonging to another patient
- prescription filter survives `n` / `p`, resets on re-entry into Medications
- cache key separates two encounters and two prescriptions at the same offset
- reply-button pagination: buttons on paged lists, menu list on unpaginated ones

### Blocker cleared first

Seven classes across six files carried the `@OverrideCache`-without-parens class decorator,
which rebinds the class to a non-class instance so the runner discovers **zero** tests from
it, silently. All six are fixed:

- `test_encounters.py`, `test_procedures.py`, `test_patient_summary.py` create real EMR
  models, so the override is dropped entirely (`LocMemCache` has no `delete_pattern`, which
  `care.emr.resources.base.delete_model_cache` calls on every EMR model save) and each
  clears the cache in `setUp` instead.
- `test_data_base.py`, `test_registry.py`, `test_rate_limit.py` are pure-mock
  `SimpleTestCase`s, so they use `override_test_cache()`.

Unsilencing them surfaced three genuinely failing assertions: the override isolates per
*class*, not per method, and those tests shared a cache key across methods. Each affected
class now clears the cache in `setUp`.

Test count went from 476 discovered to 556.

---

## What shipped differently from this plan

- Appointments stay patient-scoped for care_fe-parity reasons, not because `TokenBooking`
  lacks an encounter FK (it has one).
- A single encounter is opened without asking; a single prescription skips the picker and
  shows *all* medications rather than narrowing to that one.
- `humanize_datetime` was added to `data/base.py` for the picker's card title, which needs
  the time as well as the date.
- The encounter lookup tolerates a malformed `external_id` (the column is a UUID) as
  "not found" rather than letting it raise.
- The scope line reads `Viewing *{subject}* for encounter X for patient Y`, with either
  clause dropped when it does not apply. It replaced the one-off "Viewing records for X"
  confirmation that used to follow a patient switch, which said the same thing once.
- On a reply that carries records, the scope line *heads* them, in place of the fetcher's own
  "Your recent lab reports:". The two said the same thing, and the scope says more. A
  renderer's own header stands only when nothing is scoped -- a patient reading their own
  records from the main menu -- where otherwise the list would have no title at all.
- Selection stopped being arithmetic. Rows and typed numbers are both recorded on the
  candidate when it is offered (`row_id` and `token`), so resolving a reply is a lookup.
  The old code recomputed the typed number from the current paging offset, which belonged to
  a different list once a picker sat on top of one.
- `render_encounters`, `render_prescription_choices` and `render_patient_search_results` are
  gone. They rendered, a second way, what `Choice` already describes; `renderers.py` now
  covers only records the reader reads, not records the reader chooses between.
- `ChannelLimits` gained `preview_lines`, and `registry.get_channel_limits` replaced four
  per-cap getters. The conversation layer no longer reads a `WHATSAPP_*` setting or names a
  provider anywhere.

## Open questions for mentors

1. **Picker-first vs all-first for prescriptions** — this plan mirrors care_fe's mobile
   drawer, not its desktop default. See the divergence note above.
2. **Discharge summary replacing the encounter list** — confirm patients do not need a
   browse-only "my visits" destination separate from the picker.
3. **Identity line for patient actors** — planned for staff only, on the grounds that a
   patient knows who they are and the body budget is tight. Cheap to enable for both.
