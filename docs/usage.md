# Using the chat interface

Recipes for driving and inspecting the conversation half of the plugin — the state machine
that answers inbound WhatsApp messages. For the notification half, see
[](notification-triggers.md).

Everything below assumes the plugin is installed and its webhook is registered
([](installation.md)). A patient or staff member interacts by messaging the business number;
the commands are the numbers and letters printed beside each option.

## Sign in from a phone number

The sender is identified by the number they message from, then challenged for a year of
birth.

| Step | They send | They get back |
| --- | --- | --- |
| 1 | any message | the year-of-birth prompt |
| 2 | `1990` | the main menu |

The number must already be on a patient or user record in CARE. An unrecognised number gets
a not-found reply and the session stays at `NEW` — the plugin never reveals whether a number
is registered by behaving differently.

If one number matches more than one person — a parent and child sharing a phone, or someone
who is both a patient and staff — a pick-list is offered after the correct year, and the
selection decides which identity the session runs as.

Five wrong years lock the session for 30 minutes
(`MAX_FAILED_ATTEMPTS`, `COOLDOWN_MINUTES`). A session idle for 30 minutes
(`SESSION_IDLE_TIMEOUT_SECONDS`) is logged out on its next message.

## Navigate the menus

The menu mirrors care_fe's own hierarchy: patient-level options at the top, and the clinical
records of one visit nested under an encounter.

```
MAIN MENU                          ENCOUNTER SUB-MENU
1. Encounters   ──picker──►        1. Medications
2. Appointments                    2. Procedures
3. Patient summary                 3. Lab reports
4. Patient lookup  (staff only)    4. Discharge summary
0. Logout                          5. Change encounter
                                   0. Back to main menu
```

Choosing **1. Encounters** offers a picker of the patient's visits; picking one opens the
sub-menu, and every reply from then on is scoped to that encounter and says so in its first
line. A patient with exactly one encounter skips the picker, and `5. Change encounter` is
hidden because there is nowhere to change to.

`0` means *back* inside the sub-menu and *logout* on the main menu.

## Page through a long list

| Command | Effect |
| --- | --- |
| `n` | next page |
| `p` | previous page |

Lists are cut to 10 records a page (`DATA_FETCH_LIMIT`). Numbering continues across pages
rather than restarting, so a number always selects the row it is printed beside. Paged data
lists carry Previous/Next/Menu as reply buttons; paged pickers keep their rows selectable
and put the paging controls on a second message.

## Filter medications by prescription

**1. Medications** inside an encounter offers a prescription picker when the encounter has
two or more:

```
a. All prescriptions        View all medications
1. 12/07/2026 10:30 AM      Prescribed by: Dr. Anita Rao
2. 12/07/2026 09:15 AM      Prescribed by: Dr. S. Menon
0. Back
```

`a` shows every medication in the encounter, including any recorded without a prescription —
which is also why 0 or 1 prescription goes straight to the full list instead of filtering.

The filter survives `n`/`p`, resets when Medications is re-entered, and is dropped on an
encounter change.

## Look up another patient (staff)

Staff see **4. Patient lookup**, patients do not. It is hidden inside an encounter and
returns after `0`.

1. Choose `4`, and send a name or phone number — at least 3 characters
   (`PATIENT_SEARCH_MIN_QUERY_LENGTH`).
2. Pick from the results.
3. Every later reply names that patient, and menu options return their records.

Results never include a patient outside the staff member's facility scope, and the lookup is
refused outright without the relevant permission.

## Deliver a document

**3. Lab reports** offers a pick-list of reports that have a finalised document, and
**4. Discharge summary** delivers the encounter's summary with no pick-list.

Both send a link rather than a file. The link resolves through the plugin's redirect
endpoint to a short-lived presigned URL, so it carries no patient identifier and expires —
after 7 days for the link itself (`DOCUMENT_LINK_TTL_SECONDS`) and 5 minutes for each
presigned URL it mints (`DOCUMENT_PRESIGN_TTL_SECONDS`). `DOCUMENT_LINK_BASE_URL` must be
publicly reachable or the phone cannot open it.

## Inspect a session

```bash
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models import ConversationSession
s = ConversationSession.objects.get(phone_number='+919876543210')
print(s.state, s.user_type, s.failed_attempts, s.cooldown_until, s.last_active_at)"
```

| State | Meaning |
| --- | --- |
| `NEW` | unauthenticated, no year of birth asked yet |
| `AWAITING_YOB` | prompted for year of birth |
| `AMBIGUOUS` | several people matched; awaiting a pick |
| `AUTHENTICATED` | signed in, showing a menu |
| `SELECTING_ENCOUNTER` / `SELECTING_PRESCRIPTION` | a picker is open |
| `AWAITING_PATIENT_SEARCH` / `SELECTING_PATIENT` | staff lookup in progress |
| `SELECTING_DOCUMENT` | a document pick-list is open |
| `COOLDOWN` | locked after too many failed attempts |

Sessions are per number **per provider**, so the same number on another channel is a
separate session.

## Clear a stuck session

Sending `0` from the main menu logs out and clears the identity, which resolves most stuck
states. To reset one from the shell:

```bash
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models import ConversationSession
ConversationSession.objects.filter(phone_number='+919876543210').delete()"
```

The next inbound message starts a fresh session at `NEW`.

To clear a cooldown without deleting the session:

```bash
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models import ConversationSession
ConversationSession.objects.filter(phone_number='+919876543210').update(
    failed_attempts=0, cooldown_until=None)"
```

## When nothing comes back

Work outward from the webhook:

```bash
docker compose logs -f celery backend
```

| Symptom | Usual cause |
| --- | --- |
| no log line at all for the inbound message | Meta is not delivering: check the callback URL and that the app is subscribed to `messages` |
| the POST is rejected | `WHATSAPP_APP_SECRET` does not match the app signing the request |
| the turn runs but no reply arrives | the recipient is not on Meta's allowed list — a `131030` |
| replies stop after a burst | inbound throttling, 10 messages per 60 s per number (`RATE_LIMIT_MAX_MESSAGES`) |

Messages sent within 2 seconds of each other (`DEBOUNCE_SECONDS`) collapse into one turn and
the last one wins, so a fast double-tap is answered once by design.
