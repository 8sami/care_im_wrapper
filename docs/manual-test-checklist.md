# Manual test checklist

Exhaustive end-to-end pass over `care_im_wrapper`: inbound chat, authentication, data
menus, documents, rate limiting, notifications, and the delivery pipeline.

Values in brackets are the shipped defaults from `settings.py` — check yours if the
deployment overrides them via `PLUGIN_CONFIGS`.

Useful throughout:

```bash
# state of a conversation
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models import ConversationSession
s = ConversationSession.objects.get(phone_number='+9199…')
print(s.state, s.user_type, s.failed_attempts, s.cooldown_until, s.last_active_at)"

# recent notifications and their delivery status
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models.notification import NotificationEvent, NotificationRecipient
for e in NotificationEvent.objects.select_related('trigger').order_by('-created_date')[:10]:
    r = NotificationRecipient.objects.filter(event=e).first()
    print(e.created_date, e.trigger.slug, getattr(r,'latest_status',None))"

# why a send failed
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models.notification import NotificationStatus
s = NotificationStatus.objects.filter(state='failed').latest('created_date')
print(s.recipient.phone_number, (s.payload or {}).get('error'))"

docker compose logs -f celery backend
```

---

## 0. Preconditions

- [ ] `care-backend`, `care-celery`, `care-db`, `care-redis` all healthy
- [ ] Celery **beat** is running (periodic tasks: reminder sweep, dispatch sweep, template sync)
- [ ] Meta credentials set: `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_BUSINESS_ACCOUNT_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`
- [ ] Your test phone number is on Meta's **allowed recipient list** (otherwise every send fails `131030`)
- [ ] `DOCUMENT_LINK_BASE_URL` set and publicly reachable, or document links will be unopenable
- [ ] Templates synced and `approval_status=active`: `manage.py seed_notification_variable_mappings`
- [ ] All 10 triggers present and `is_active` (migrations through 0018 applied)
- [ ] Test patient exists with a `phone_number` **and** a known year of birth
- [ ] Test staff user exists with the same phone number (to exercise the ambiguous path)

---

## 1. Webhook & transport

- [ ] **GET challenge** — Meta's verify call with correct `hub.verify_token` returns `hub.challenge` verbatim
- [ ] GET with wrong `hub.verify_token` is rejected
- [ ] GET when `WHATSAPP_WEBHOOK_VERIFY_TOKEN` is unset is rejected (and logs an error)
- [ ] **POST with valid `X-Hub-Signature-256`** is accepted and processed
- [ ] POST with a tampered body (valid-looking signature, altered payload) is rejected
- [ ] POST with the `X-Hub-Signature-256` header missing is rejected
- [ ] POST with a malformed/non-dict `changes` entry does not 500 the endpoint
- [ ] POST with an unknown `field` (not `messages`) is ignored quietly
- [ ] A status-update payload (`sent`/`delivered`/`read`/`failed`) updates the matching `NotificationRecipient.latest_status`
- [ ] A status update for an unknown `tracking_id` is logged and dropped, not retried forever

---

## 2. Authentication

### 2.1 Unknown number
- [ ] Message from a number matching no patient and no staff → "not found" reply
- [ ] Session stays in `NEW` (no year-of-birth prompt)

### 2.2 Happy path — single match
- [ ] Message from a known patient number → year-of-birth prompt, state `AWAITING_YOB`
- [ ] Correct 4-digit year → authenticated, main menu appears, state `AUTHENTICATED`
- [ ] `user_type` is `patient`, and `snapshot_name` / `snapshot_phone` are populated
- [ ] Menu shows options 1–6 and a Logout row (no option 7)

### 2.3 Year-of-birth validation
- [ ] 3 digits (`199`) → invalid-format reply, attempt counter **not** incremented
- [ ] 5 digits (`19900`) → invalid-format reply
- [ ] Non-numeric (`hello`) → invalid-format reply
- [ ] Empty / whitespace-only message → invalid-format reply
- [ ] Correct length but wrong year (`1901`) → wrong-year reply showing remaining attempts, counter **incremented**

### 2.4 Ambiguous — one number, several people
- [ ] Number matching >1 person → after correct YOB, a candidate pick-list appears, state `AMBIGUOUS`
- [ ] Picking a row authenticates as that identity
- [ ] Candidate rows are 1-based (`candidate_1` is first)
- [ ] Out-of-range selection → invalid-choice reply, still `AMBIGUOUS`
- [ ] Free-text instead of a selection → invalid-choice reply
- [ ] Same number registered as **both** patient and staff → both appear; picking staff yields the staff menu (option 7 present)

### 2.5 Lockout
- [ ] Wrong year `[MAX_FAILED_ATTEMPTS = 5]` times → state `COOLDOWN`, `cooldown_until` set `[COOLDOWN_MINUTES = 30]` ahead
- [ ] Any message during cooldown → cooldown reply with remaining minutes, no data access
- [ ] Remaining-minutes figure decreases across attempts
- [ ] After cooldown expires, next message resets to `NEW` and restarts the flow
- [ ] `failed_attempts` resets to 0 on successful authentication

---

## 3. Session lifecycle

- [ ] **Logout** — send `0` from the menu → logout confirmation, state `NEW`, `user_id` / `snapshot_*` / `candidates` cleared
- [ ] After logout, next message restarts at the year-of-birth prompt
- [ ] **Idle timeout** — authenticated session untouched for `[SESSION_IDLE_TIMEOUT_SECONDS = 30 min]`, then a new message → logged out and treated as `NEW`
- [ ] A session active within the window is **not** logged out
- [ ] `last_active_at` advances on **every** inbound turn, including read-only ones that change no state
- [ ] A session in `COOLDOWN` is exempt from idle logout (cooldown runs on its own timer)
- [ ] **Deleted/deactivated user** — authenticated session whose backing user is removed → session-expired reply and logout
- [ ] Two different numbers have independent sessions
- [ ] Same number on a different provider is a separate session (unique on `phone_number` + `provider`)

---

## 4. Patient menu (options 1–6)

For **each** option below: valid data renders, empty data gives the no-data message and
returns to the menu, and the menu is re-offered afterwards.

- [ ] **1 Encounter details** — renders facility, date, status, class; offers document selection
- [ ] **2 Medications** — prescriptions, each with its prescriber, facility, note and the
      medications on it; per medication one block per `dosage_instruction` (dosage,
      frequency + additional instructions, duration, instructions)
- [ ] A tapered medication shows numbered Steps, each dose beside its own duration
- [ ] Medications with no prescription are grouped under their authored date
- [ ] A prescription is never split or repeated across two pages
- [ ] A PRN medication renders `SOS` (care_fe's label), with its reason if recorded
- [ ] A `1-1-1` timing renders `1-1-1 (Thrice a day)`; a non-preset like `2-2-2` stays verbatim
- [ ] A coded frequency (`BID`) renders `1-0-1 (Twice a day)`
- [ ] Medication with unstructured/legacy `dosage_instruction` still renders without error
- [ ] **3 Procedures** — name, date, status
- [ ] **4 Appointments** — practitioner booking reads `<Practitioner> at <Facility>`
- [ ] Location-type booking reads `<Name> Location at <Facility>`
- [ ] Healthcare-service booking reads `<Name> HealthcareService at <Facility>`
- [ ] **5 Lab reports** — name, date, status; offers document selection
- [ ] **6 Patient summary** — name, DOB, blood group, gender, phone; missing fields say "not recorded"
- [ ] Records marked `entered_in_error` are excluded everywhere
- [ ] At most `[DATA_FETCH_LIMIT = 10]` records per list
- [ ] `n` / `p` page forward and back on any list; `n` on the last page says so
- [ ] Interactive providers also show Next/Previous rows, dropped when over the row cap
- [ ] Numbering continues across pages rather than restarting at 1
- [ ] No record is repeated or skipped when paging forward then back
- [ ] Invalid menu choice (`9`, `abc`, emoji) → invalid-choice reply, still `AUTHENTICATED`
- [ ] Repeating the same option twice within `[DATA_CACHE_TIMEOUT_SECONDS = 90]` serves from cache (no second DB fetch)
- [ ] A very long list is truncated to `[WHATSAPP_MESSAGE_CHAR_LIMIT = 4096]` with a truncation marker
- [ ] When data + greeting exceed the limit, it splits into a plain-text message **then** the menu, in that order

---

## 5. Staff menu (option 7 — patient lookup)

- [ ] Staff sees option **7 Patient lookup**; patients do not
- [ ] Selecting 7 → search prompt, state `AWAITING_PATIENT_SEARCH`
- [ ] Query shorter than `[PATIENT_SEARCH_MIN_QUERY_LENGTH = 3]` → error message, stays in search state so the next message retries as a query
- [ ] Query with no matches → no-patients-found reply
- [ ] Query with matches → pick-list, state `SELECTING_PATIENT`
- [ ] Result rows are 0-based (`patient_0` is first)
- [ ] More than `[WHATSAPP_LIST_ROW_LIMIT = 10]` matches → list is capped, not rejected by Meta
- [ ] Selecting a patient sets `active_patient_external_id` and returns to the menu with a confirmation
- [ ] Subsequent menu options now return **that** patient's data, not the staff member's
- [ ] Out-of-range / free-text selection → invalid-choice reply
- [ ] Staff without lookup permission → permission-denied reply, returned to `AUTHENTICATED`
- [ ] Staff querying a patient outside their facility scope → permission denied or no results (never another facility's data)

---

## 6. Documents

### 6.1 Selection flow
- [ ] Option 1 (encounters) with selectable records → document pick-list, state `SELECTING_DOCUMENT`
- [ ] Option 5 (lab reports) with a finalised report → document pick-list
- [ ] Selecting a row delivers the document and **stays** in `SELECTING_DOCUMENT` (so another can be picked)
- [ ] Sending `0` returns to the main menu, `candidates` cleared
- [ ] Out-of-range selection → invalid-choice reply
- [ ] Records with no retrievable document → document-unavailable message, not a crash
- [ ] When the interactive body would exceed `[WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT = 1024]`, it falls back to the short prompt and still shows rows

### 6.2 Link generation
- [ ] An encounter report is generated on first request
- [ ] Re-requesting within `[ENCOUNTER_REPORT_REUSE_SECONDS = 15 min]` reuses the existing report rather than regenerating
- [ ] Requesting after that window regenerates
- [ ] An already-uploaded diagnostic file is served directly (not regenerated)
- [ ] An existing unexpired `DocumentLink` for the same object is reused rather than minting a new token

### 6.3 Public redirect endpoint
- [ ] Opening the link redirects to a working presigned file URL
- [ ] Presigned URL stops working after `[DOCUMENT_PRESIGN_TTL_SECONDS = 5 min]`
- [ ] Link stops working after `[DOCUMENT_LINK_TTL_SECONDS = 7 days]`
- [ ] Unknown token → 404
- [ ] Malformed token → 404
- [ ] Expired token → 404 **identical** to the unknown-token 404 (no enumeration signal)
- [ ] Hitting the endpoint more than `[DOCUMENT_LINK_RATE_LIMIT_MAX = 30]` times in `[60s]` is throttled
- [ ] Link is not guessable and carries no patient identifiers in the URL

### 6.4 Authorization
- [ ] A patient cannot obtain a document for another patient
- [ ] Staff without report-generation permission → permission denied
- [ ] A link minted for patient A does not resolve to patient B's file

---

## 7. Rate limiting, debounce, deduplication

- [ ] **Inbound** — more than `[RATE_LIMIT_MAX_MESSAGES = 10]` messages in `[RATE_LIMIT_WINDOW_SECONDS = 60]` from one number is throttled
- [ ] Throttling is per phone number: a second number is unaffected
- [ ] After the window passes, the number works again
- [ ] **Debounce** — several messages within `[DEBOUNCE_SECONDS = 2]` collapse into one processed turn (the last one wins)
- [ ] A message arriving during the debounce window resets the timer
- [ ] **Dedup** — Meta redelivering the same message id within `[MESSAGE_DEDUP_TIMEOUT_SECONDS = 300]` is dropped, not reprocessed
- [ ] A celery **retry** of the same message id is *not* mistaken for a duplicate (retries must still run)
- [ ] **Outbound pacing** — two replies in one turn are not throttled against each other (2nd+ send uses `pace=False`)
- [ ] Replying to the bot within `[WHATSAPP_MIN_SEND_INTERVAL_SECONDS = 6]` of its last message defers rather than dropping the reply
- [ ] ⚠️ **Known issue — verify the blast radius**: when pacing trips on the *first* queued send of a turn, the task retries with state already committed, so the turn is re-run. Reply within 6 s from `NEW` and from `AWAITING_YOB` with a wrong year, and check whether you get a wrong reply / a double-counted failed attempt. See §11.

---

## 8. Notifications — triggers

For each: perform the action, confirm a `NotificationEvent` + `NotificationRecipient` are
created, and that `latest_status` reaches `sent` → `delivered` → `read`.

- [ ] **`patient_registered`** — register a new patient with a phone number
- [ ] Patient created **without** a phone number → skipped cleanly, no failed recipient row
- [ ] **`patient_discharged`** — move an existing encounter to `discharged`
- [ ] Creating an encounter already `discharged` does **not** fire
- [ ] Re-saving an already-discharged encounter (note edit) does **not** re-fire
- [ ] Moving to a non-discharged status does **not** fire
- [ ] **`appointment_confirmed`** — book an appointment
- [ ] **`appointment_cancelled`** — cancel it
- [ ] **`appointment_rescheduled`** — reschedule it
- [ ] Re-saving a booking without a status change does **not** re-fire
- [ ] **`appointment_reminder`** — book for ~within 24 h, then run the sweep
- [ ] Booking beyond `[APPOINTMENT_REMINDER_LEAD_SECONDS = 24 h]` is not reminded
- [ ] Booking already started is not reminded
- [ ] Cancelled booking is not reminded
- [ ] Running the sweep twice reminds only **once** per booking
- [ ] A rescheduled booking (new row) does get its own reminder
- [ ] **`wait_time_update`** — issue a queue token to a patient
- [ ] Token **with** a booking counts down to the slot start (not "under a minute")
- [ ] Token days out reads in days, e.g. "3 days 3 hours"
- [ ] Token whose slot already started falls back to queue position
- [ ] Walk-in token (no booking) uses queue position × `[WAIT_TIME_MINUTES_PER_TOKEN = 5]`
- [ ] Token without a patient is skipped
- [ ] Updating an existing token does **not** re-fire
- [ ] **`invoice_issued`** — move an invoice to `issued`
- [ ] Draft invoice stays silent
- [ ] Re-saving an issued invoice does **not** re-fire
- [ ] Invoice with a blank `number` still sends (falls back to external id)
- [ ] **`payment_recorded`** — complete a payment against an invoice
- [ ] Incomplete/partial payment stays silent
- [ ] Payment with **no** target invoice is skipped — see §11, this is a known gap
- [ ] **`document_ready_update`** — complete a service request with a finalised report
- [ ] Service request completed with no finalised report does **not** fire

### Resource-type coverage (appointments)
- [ ] Practitioner booking → `doctor_name` is the practitioner
- [ ] Location booking → `doctor_name` reads `<Name> Location`
- [ ] Healthcare-service booking → `doctor_name` reads `<Name> HealthcareService`
- [ ] Same three cases for `appointment_reminder`

---

## 9. Notification delivery pipeline

- [ ] Every template parameter renders non-empty (a blank one fails the whole send)
- [ ] A deliberately blank variable produces `WhatsAppTemplateNotConfiguredError` naming the template **and** parameter — not Meta's opaque `131008`
- [ ] That failure is recorded immediately and **not** retried
- [ ] A transient 5xx / network error **is** retried up to `[TASK_MAX_RETRIES = 3]`, then recorded failed
- [ ] A permanent 400 is recorded failed without retrying
- [ ] Sending to a number outside Meta's allowlist fails with `131030` and is not retried forever
- [ ] Two workers cannot double-send the same recipient (dispatch claim)
- [ ] A claim older than `[DISPATCH_CLAIM_STALE_SECONDS = 900]` is reclaimable
- [ ] The sweep `[NOTIFICATION_DISPATCH_INTERVAL_SECONDS = 120]` picks up recipients real-time dispatch missed
- [ ] Template sync `[TEMPLATE_SYNC_INTERVAL_SECONDS = 6 h]` refreshes Meta-sourced fields and does **not** clobber `variable_mapping`
- [ ] Provider returning no message id records a `MissingTrackingId` failure
- [ ] `message_payload.sent_parameters` matches what the patient actually received

---

## 10. Admin / API

- [ ] `notification-triggers` — list/retrieve/update; unauthenticated is rejected
- [ ] `notification-templates` — list; `variable_mapping` editable via the builder
- [ ] Variable picker offers the right fields per trigger `context_slug`
- [ ] Saving a trigger with an unknown `context_slug` is rejected with a clear validation error
- [ ] `notification-events` / `notification-recipients` — list and filter
- [ ] A user without notification permissions cannot read or write any of them
- [ ] Facility scoping: a user only sees events for facilities they belong to

---

## 11. Known gaps — expected to fail

Not bugs to file; these are open items as of this checklist.

- [ ] **Account-level payments send nothing.** A `PaymentReconciliation` with no
      `target_invoice` is skipped, because `payment_status` resolves patient/account/invoice
      off an Invoice. Amjith asked for the invoice number "if exist", which needs a second
      template without the invoice line.
- [ ] **`patient_updates` needs the approved template updated** to drop the
      `Hospital / Clinic: {{hospital_or_clinic}}` line. Until then registration and
      discharge notifications will fail on a missing parameter.
- [ ] **Discharge no longer names a facility** — removing that parameter cost discharge its
      facility name too. Split discharge into its own template if that matters.
- [ ] **Pacing retry replays a committed turn** (see §7). `_flush` re-raises
      `OutboundRateLimitedError` on the first queued send, but session state is already
      committed, so the retry re-runs the turn against the advanced state.
