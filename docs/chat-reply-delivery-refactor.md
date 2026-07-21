# Chat reply delivery: current approach vs. commit-then-send (Plan B)

## Why this document exists

The inbound chat path sends WhatsApp messages **inside** the database transaction that
advances the conversation state. That single choice is the root of two problems:

1. **A DB row lock is held across network I/O.** `run_state_machine` takes a
   `select_for_update` lock on the `ConversationSession` and then makes synchronous
   WhatsApp HTTP calls while that lock is held and the transaction is open.
2. **A mid-turn failure can duplicate messages.** If a turn sends message 1 and then a
   later send raises, the task retries and replays the whole turn — re-sending message 1 —
   while the transaction rollback silently reverts the session state that message 1 implied.

A narrow symptom (the outbound-rate-limit trigger) is already patched via `send_message(...,
pace=False)` for continuation sends. This document is the **root** fix: separate state
persistence from message delivery so sends never sit inside the transaction and a retry can
never replay an already-sent turn.

It is written as a plan, not applied — it is a larger, test-heavy refactor that deserves its
own PR.

---

## Current approach (as-is)

### Shape

`webhooks → handlers/meta.py → tasks.process_inbound_message → conversation.handlers.run_state_machine`

```python
# conversation/handlers.py
def run_state_machine(phone_number, text, channel):
    with transaction.atomic():
        session = ConversationSession.objects.select_for_update().get_or_create(...)
        ...
        handler(session, phone_number, text, channel)   # handler calls send_message() inline
```

Each handler (`_handle_authenticated`, `_handle_selecting_document`, …) calls
`send_message(channel, phone_number, msg)` directly, interleaved with `session.save(...)`.
There are ~26 `send_message` call sites across `conversation/handlers.py`.

```python
# tasks.py
def process_inbound_message(self, phone_number, text, channel, raw_id=None):
    if raw_id and self.request.retries == 0:
        # inbound dedup: guards against Meta re-delivering the same webhook,
        # deliberately bypassed on retries so a retry re-runs the turn
        ...
    try:
        run_state_machine(phone_number, text, channel)
    except OutboundRateLimitedError:      # retry
    except (PairRateLimit|Network|Server): # retry
    except WhatsAppBadRequestError:        # drop (permanent)
    except Exception:                      # retry
```

### Failure sequence

```
send msg 1  ── OK (already on the wire, cannot be un-sent)
send msg 2  ── raises WhatsAppNetworkError
              │
              ├─ exception leaves `with transaction.atomic()` → session state ROLLED BACK
              └─ process_inbound_message catches → self.retry()
                    └─ run_state_machine re-runs from the pre-turn state → RE-SENDS msg 1
```

With `TASK_MAX_RETRIES`, the patient can receive the same message 2–4 times, and the
session state no longer reflects what was actually delivered.

### Properties

| Property | Value |
| --- | --- |
| Reply latency | Immediate (synchronous in the task) |
| DB lock duration | Held across every WhatsApp HTTP call in the turn |
| Delivery on happy path | Exactly the intended messages |
| Delivery on mid-turn error | Duplicates possible; state/delivery can diverge |
| State durability | Coupled to send success — a late send failure discards earlier state |

---

## Plan B — commit state, then send

### Principle

Do all state work inside the (briefly held) transaction, have handlers **return** the
messages they want sent instead of sending them, commit, and **then** flush the messages
after the lock is released. This is a per-turn in-memory outbox — not the persistent,
worker-backed outbox the notification path uses (that is correct for fire-and-forget
business messages but would add a hop of latency to interactive chat, see "Alternatives").

### Target shape

```python
# conversation/handlers.py

@dataclass(frozen=True)
class Outbound:
    """A message a handler wants delivered, resolved after the transaction commits."""
    phone_number: str
    message: OutboundMessage | str
    pace: bool = True

def run_state_machine(phone_number, text, channel) -> None:
    outbox: list[Outbound] = []
    with transaction.atomic():
        session = ConversationSession.objects.select_for_update().get_or_create(...)
        handler(session, phone_number, text, channel, outbox)   # handler appends to outbox
    # lock released, state committed and durable:
    _flush(channel, outbox)

def _flush(channel, outbox):
    for item in outbox:
        send_message(channel, item.phone_number, item.message, pace=item.pace)
```

Handlers change from *sending* to *collecting*:

```python
# before
send_message(channel, phone_number, _msg("invalid_choice"))

# after
outbox.append(Outbound(phone_number, _msg("invalid_choice")))
```

### Retry policy

Because state is committed before any send, a retry has nothing safe to replay — the turn
already advanced. So `process_inbound_message` becomes **at-most-once** for the reply:

- Keep retrying only the *pre-send* pacing case (nothing has been delivered yet, so a retry
  is safe) — i.e. `OutboundRateLimitedError` raised before the first flush send.
- Do **not** retry once `_flush` has begun. A failed flush is logged; the patient re-drives
  (re-taps) if a reply was lost.

A clean way to encode this: `_flush` swallows and logs per-message send failures rather than
raising, so a mid-flush error never propagates back into a task retry. The pre-commit phase
can still raise (and be retried) because it has sent nothing.

### Failure sequence under Plan B

```
── transaction: read session, compute reply, write new state ── COMMIT (durable)
flush msg 1 ── OK
flush msg 2 ── network error → logged, not raised
              (no retry, no replay; state already reflects the turn)
```

Worst case: the patient gets msg 1 but not msg 2, and re-taps. No duplicates, no
state/delivery divergence.

### Properties

| Property | Value |
| --- | --- |
| Reply latency | Immediate (flush still runs in the same task, just after commit) |
| DB lock duration | Held only for state read/write, released before any network call |
| Delivery on happy path | Exactly the intended messages |
| Delivery on mid-turn error | At-most-once; a dropped follow-up is user-recoverable |
| State durability | Independent of send outcome |

---

## What changes, concretely

### Code

- `conversation/handlers.py`
  - Add the `Outbound` dataclass and thread an `outbox: list[Outbound]` argument through
    `run_state_machine` → every `_handle_*` and every private helper that currently sends
    (`_send_main_menu`, `_send_candidate_menu`, `_send_report_menu` / `_enter_document_selection`,
    `_handle_selecting_document`, …).
  - Convert all ~26 `send_message(...)` call sites to `outbox.append(Outbound(...))`,
    preserving the existing `pace=False` markers on continuation messages.
  - Add `_flush(channel, outbox)` and call it after the `with transaction.atomic()` block.
- `tasks.py`
  - `process_inbound_message`: keep the pre-commit pacing retry; stop retrying once delivery
    has started (achieved by `_flush` not raising on per-message failure).
- No settings, model, or migration changes. No provider changes.

### Tests

The mechanical cost is here. ~10 test files assert on `send_message` being called:

```
test_handlers_ambiguous.py            test_handlers_new_and_yob.py
test_handlers_authenticated_dispatch  test_handlers_patient_search.py
test_handlers_authenticated_success   test_handlers_selecting_patient.py
test_handlers_document_pull.py        test_run_state_machine.py
test_outbound_pacing.py               (test_registry.py is unaffected — unit tests send_message itself)
```

These move from *"assert `send_message` was called with X"* to one of:

- assert the handler appended the expected `Outbound` items to the outbox (unit level), or
- assert `_flush` / `send_message` was called with them after commit (integration level).

`test_outbound_pacing.py` in particular re-frames: instead of proving the throttle no longer
aborts a turn mid-send, it proves the turn commits and then flushes both messages, and that a
flush failure does not trigger a task retry.

### Effort

Moderate and test-heavy — the single largest change among the reviewed items. The production
diff is mechanical (collect instead of send); the test diff touches ~10 files. Best done as
its own PR with its own review, not folded into an unrelated change.

---

## Alternatives considered

- **Plan A — at-most-once, retry only before the first send (symptom patch).**
  ~10 lines in `tasks.py` / `run_state_machine`: stop retrying once a send has happened.
  Removes the patient-visible duplicates immediately but leaves sends inside the transaction
  and the lock-across-network smell. A reasonable ship-now step with Plan B as the follow-up.

- **Plan C — persistent transactional outbox with a delivery worker.**
  What `dispatch_notification_recipient` already does for business notifications
  (`NotificationRecipient` row + `dispatch_started_at` claim + `latest_status` guard +
  `tracking_id`). Correct and at-least-once, but **asynchronous** — it adds a worker hop to
  every chat reply, which defeats the interactive path's whole reason for being synchronous.
  Appropriate for notifications, wrong for live chat.

## The delivery guarantee is inherent, not a choice

As long as chat replies are synchronous (no persistent outbox), the guarantee is
**at-most-once**: WhatsApp outbound messages have no idempotency key the platform dedupes on,
so a lost reply cannot be safely auto-retried without risking a duplicate. Plan B does not
change that guarantee — it makes the internals correct (durable state, no lock across I/O, no
replay) while accepting the same at-most-once semantics, which are appropriate because the
patient drives the conversation and can re-trigger any lost reply.
