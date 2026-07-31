# Manual test checklist

End-to-end pass over the plugin: chat, auth, data menus, documents, rate limiting and
notifications. Values in brackets are the defaults from `settings.py`.

## 0. Setup

- [ ] backend, celery, db and redis containers healthy
- [ ] celery beat running (reminder sweep, dispatch sweep, template sync)
- [ ] Meta credentials set: phone number id, access token, business account id, app secret, webhook verify token
- [ ] test number on Meta's allowed recipient list, or every send fails `131030`
- [ ] `DOCUMENT_LINK_BASE_URL` set and publicly reachable
- [ ] templates synced and active: `manage.py seed_notification_variable_mappings`
- [ ] all 10 triggers present and active
- [ ] test patient has a phone number and a year of birth
- [ ] a staff user shares that phone number, to test the ambiguous path

## 1. Webhook

- [ ] GET with the right `hub.verify_token` returns `hub.challenge`
- [ ] GET with a wrong token is rejected
- [ ] GET with `WHATSAPP_WEBHOOK_VERIFY_TOKEN` unset is rejected
- [ ] POST with a valid `X-Hub-Signature-256` is processed
- [ ] POST with a tampered body is rejected
- [ ] POST with no signature header is rejected
- [ ] POST with a malformed `changes` entry does not 500
- [ ] POST with an unknown `field` is ignored
- [ ] a status payload updates `NotificationRecipient.latest_status`
- [ ] a status for an unknown `tracking_id` is dropped, not retried forever

## 2. Auth

Unknown number
- [ ] unmatched number gets the not-found reply, session stays `NEW`

Happy path
- [ ] known number gets the year-of-birth prompt, state `AWAITING_YOB`
- [ ] correct year authenticates, menu appears, state `AUTHENTICATED`
- [ ] `user_type`, `snapshot_name` and `snapshot_phone` are set
- [ ] menu shows options 1–6 plus Logout, no option 7

Year of birth
- [ ] `199` → invalid format, attempt counter unchanged
- [ ] `19900` → invalid format
- [ ] `hello` → invalid format
- [ ] empty message → invalid format
- [ ] wrong 4-digit year → wrong-year reply, counter incremented

Several people on one number
- [ ] >1 match after the correct year → candidate pick-list, state `AMBIGUOUS`
- [ ] picking a row authenticates as that identity
- [ ] out-of-range or free-text selection → invalid choice, still `AMBIGUOUS`
- [ ] a number that is both patient and staff shows both; picking staff gives option 7

Lockout
- [ ] `[MAX_FAILED_ATTEMPTS = 5]` wrong years → `COOLDOWN` for `[COOLDOWN_MINUTES = 30]`
- [ ] messages during cooldown get the cooldown reply, no data access
- [ ] the remaining-minutes figure counts down
- [ ] after expiry the next message resets to `NEW`
- [ ] `failed_attempts` resets on success

## 3. Session

- [ ] `0` logs out; `user_id`, `snapshot_*` and `candidates` cleared
- [ ] the next message restarts at the year-of-birth prompt
- [ ] idle past `[SESSION_IDLE_TIMEOUT_SECONDS = 30 min]` logs out on the next message
- [ ] a session used within the window is not logged out
- [ ] `COOLDOWN` is exempt from the idle logout
- [ ] a session whose user was deleted gets the session-expired reply
- [ ] two numbers have independent sessions
- [ ] the same number on another provider is a separate session

## 4. Patient menu

Each option: data renders, no data gives the no-data message, menu is re-offered.

- [ ] **1 Encounters** — facility, date, status, class; offers documents
- [ ] **2 Medications** — prescriptions with prescriber, facility and note, medications inside
- [ ] one block per `dosage_instruction`: dosage, frequency, duration, instructions
- [ ] a tapered medication shows numbered steps, each dose next to its own duration
- [ ] medications with no prescription group under their authored date
- [ ] a prescription is never split or repeated across pages
- [ ] PRN renders `SOS`, with its reason if recorded
- [ ] `1-1-1` renders `1-1-1 (Thrice a day)`; `2-2-2` stays verbatim
- [ ] `BID` renders `1-0-1 (Twice a day)`
- [ ] legacy string `dosage_instruction` still renders
- [ ] **3 Procedures** — name, date, status
- [ ] **4 Appointments** — practitioner reads `<Practitioner> at <Facility>`
- [ ] location reads `<Name> Location at <Facility>`
- [ ] healthcare service reads `<Name> HealthcareService at <Facility>`
- [ ] **5 Lab reports** — name, date, status; offers documents
- [ ] **6 Summary** — name, DOB, blood group, gender, phone; blanks say not recorded
- [ ] `entered_in_error` records are excluded everywhere
- [ ] at most `[DATA_FETCH_LIMIT = 10]` records per page
- [ ] `n` and `p` page forward and back; `n` on the last page says so
- [ ] interactive providers show Next/Previous rows, dropped when over the row cap
- [ ] numbering continues across pages instead of restarting
- [ ] nothing repeats or is skipped paging forward then back
- [ ] invalid choice (`9`, `abc`, emoji) → invalid choice, still `AUTHENTICATED`
- [ ] a long list truncates at `[WHATSAPP_MESSAGE_CHAR_LIMIT = 4096]` with a marker

## 5. Staff lookup

- [ ] staff see option 7, patients do not
- [ ] option 7 → search prompt, state `AWAITING_PATIENT_SEARCH`
- [ ] query under `[PATIENT_SEARCH_MIN_QUERY_LENGTH = 3]` errors but stays in the search state
- [ ] no matches → no-patients-found
- [ ] matches → pick-list, state `SELECTING_PATIENT`
- [ ] more than `[WHATSAPP_LIST_ROW_LIMIT = 10]` matches caps the list rather than failing
- [ ] selecting sets `active_patient_external_id` and confirms
- [ ] later menu options return that patient's data, not the staff member's
- [ ] out-of-range or free-text selection → invalid choice
- [ ] staff without lookup permission → permission denied, back to `AUTHENTICATED`
- [ ] a patient outside the staff member's facility scope is never returned

## 6. Documents

Selection
- [ ] encounters with selectable records → pick-list, state `SELECTING_DOCUMENT`
- [ ] lab reports with a finalised report → pick-list
- [ ] selecting delivers the document and stays in `SELECTING_DOCUMENT`
- [ ] `0` returns to the menu and clears `candidates`
- [ ] out-of-range selection → invalid choice
- [ ] a record with no document → document-unavailable, not a crash
- [ ] over `[WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT = 1024]` it falls back to the short prompt and keeps the rows

Links
- [ ] an encounter report generates on first request
- [ ] within `[ENCOUNTER_REPORT_REUSE_SECONDS = 15 min]` it is reused
- [ ] after that window it regenerates
- [ ] an already-uploaded diagnostic file is served directly
- [ ] an unexpired `DocumentLink` for the same object is reused

Redirect endpoint
- [ ] the link redirects to a working presigned URL
- [ ] the presigned URL expires after `[DOCUMENT_PRESIGN_TTL_SECONDS = 5 min]`
- [ ] the link expires after `[DOCUMENT_LINK_TTL_SECONDS = 7 days]`
- [ ] unknown, malformed and expired tokens all 404 identically
- [ ] over `[DOCUMENT_LINK_RATE_LIMIT_MAX = 30]` hits in 60s is throttled
- [ ] the URL carries no patient identifiers

Authorization
- [ ] a patient cannot fetch another patient's document
- [ ] staff without report permission → permission denied
- [ ] a link for patient A never resolves to patient B's file

## 7. Rate limiting

- [ ] over `[RATE_LIMIT_MAX_MESSAGES = 10]` in `[RATE_LIMIT_WINDOW_SECONDS = 60]` is throttled
- [ ] throttling is per number; another number is unaffected
- [ ] the number works again after the window
- [ ] messages within `[DEBOUNCE_SECONDS = 2]` collapse into one turn, last one wins
- [ ] a message during the debounce window resets the timer
- [ ] Meta redelivering a message id within `[MESSAGE_DEDUP_TIMEOUT_SECONDS = 300]` is dropped
- [ ] two replies in one turn are not paced against each other
- [ ] replying within `[WHATSAPP_MIN_SEND_INTERVAL_SECONDS = 6]` defers rather than drops

## 8. Notification triggers

For each: the action creates a `NotificationEvent` and `NotificationRecipient`, and
`latest_status` reaches sent → delivered → read.

- [ ] `patient_registered` — register a patient with a phone number
- [ ] a patient with no phone number is skipped, no failed recipient
- [ ] `patient_discharged` — move an encounter to `discharged`
- [ ] an encounter created already discharged does not fire
- [ ] re-saving a discharged encounter does not re-fire
- [ ] a non-discharged status change does not fire
- [ ] `appointment_confirmed` — book
- [ ] `appointment_cancelled` — cancel
- [ ] `appointment_rescheduled` — reschedule
- [ ] re-saving a booking with no status change does not re-fire
- [ ] `appointment_reminder` — book within 24h, run the sweep
- [ ] a booking beyond `[APPOINTMENT_REMINDER_LEAD_SECONDS = 24 h]` is not reminded
- [ ] a started or cancelled booking is not reminded
- [ ] running the sweep twice reminds once
- [ ] a rescheduled booking gets its own reminder
- [ ] `wait_time_update` — issue a queue token
- [ ] a token with a booking counts down to the slot, not "under a minute"
- [ ] days out reads in days, e.g. "3 days 3 hours"
- [ ] a token whose slot already started falls back to queue position
- [ ] a walk-in uses queue position × `[WAIT_TIME_MINUTES_PER_TOKEN = 5]`
- [ ] a token with no patient is skipped
- [ ] updating a token does not re-fire
- [ ] `invoice_issued` — move an invoice to `issued`
- [ ] a draft invoice stays silent
- [ ] re-saving an issued invoice does not re-fire
- [ ] an invoice with a blank number still sends, using the external id
- [ ] `payment_recorded` — complete a payment against an invoice
- [ ] a partial payment stays silent
- [ ] `document_ready_update` — complete a service request with a final report
- [ ] a service request with no final report does not fire

Resource types
- [ ] practitioner booking → `doctor_name` is the practitioner
- [ ] location booking → `<Name> Location`
- [ ] healthcare service booking → `<Name> HealthcareService`
- [ ] same three for `appointment_reminder`

## 9. Delivery

- [ ] every template parameter renders non-empty
- [ ] a blank variable raises `WhatsAppTemplateNotConfiguredError` naming the template and parameter, not Meta's `131008`
- [ ] a number off the allowlist fails `131030` and is not retried forever
- [ ] template sync refreshes Meta fields without clobbering `variable_mapping`

## 10. API

- [ ] `notification-triggers` list/retrieve/update; unauthenticated rejected
- [ ] `notification-templates` list; `variable_mapping` editable
- [ ] the variable picker offers the right fields per `context_slug`
- [ ] an unknown `context_slug` is rejected with a clear error
- [ ] `notification-events` and `notification-recipients` list and filter
- [ ] a user without notification permissions cannot read or write them
- [ ] a user only sees events for their own facilities
