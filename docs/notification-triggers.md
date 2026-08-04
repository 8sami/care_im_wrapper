# Notification triggers

Ten triggers. Each fires from a `post_save` signal, so doing the action through the UI,
the API or the Django shell all work the same way.

| Trigger | Template | Fires when |
|---|---|---|
| `patient_registered` | `patient_updates` | a Patient is created |
| `patient_discharged` | `patient_updates` | an Encounter moves to `discharged` |
| `appointment_confirmed` | `appointment_update` | a TokenBooking is created as `booked` |
| `appointment_cancelled` | `appointment_update` | a booking moves to `cancelled` |
| `appointment_rescheduled` | `appointment_update` | a booking moves to `rescheduled` |
| `appointment_reminder` | `event_reminder` | the sweep finds a `booked` slot starting within 24h |
| `wait_time_update` | `wait_time_update` | a queue Token is issued to a patient |
| `invoice_issued` | `payment_status` | an Invoice moves to `issued` |
| `payment_recorded` | `payment_status` | a PaymentReconciliation reaches `complete` |
| `document_ready_update` | `document_ready_update` | a ServiceRequest completes with a final DiagnosticReport |

## Before you start

- the patient needs a phone number, and it must be on Meta's allowed recipient list
- templates must be synced and active: `manage.py seed_notification_variable_mappings`
- celery and beat must be running

## Notes per trigger

**Transitions, not saves.** Discharge, cancel, reschedule, invoice and payment all fire on
the move into that state. Creating a record already in it does not fire (except an invoice
created as `issued`, which does), and re-saving one that is already there does not re-fire.

**`appointment_reminder`** is the only time-driven one. It sweeps every 15 minutes; to run
it now:

```bash
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.tasks import send_appointment_reminders
send_appointment_reminders()"
```

A booking is reminded once — the sweep skips anything that already has a reminder event, so
it is safe to re-run.

**`wait_time_update`** counts down to the booking's slot when the token has one, and falls
back to queue position for a walk-in. A token with no patient is skipped.

**`payment_recorded`** needs a `target_invoice`. A payment against the account only is
skipped, since the template quotes an invoice number.

**`patient_registered`** fires on every Patient row, with no bulk guard — reloading fixtures
will send one per patient. Disable it first if you do:

```bash
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models.notification import NotificationTrigger
NotificationTrigger.objects.filter(slug='patient_registered').update(is_active=False)"
```

## Checking what happened

```bash
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models.notification import NotificationEvent, NotificationRecipient, NotificationStatus
for e in NotificationEvent.objects.select_related('trigger').order_by('-created_date')[:10]:
    r = NotificationRecipient.objects.filter(event=e).first()
    print(e.created_date, e.trigger.slug, getattr(r, 'latest_status', None))
    s = NotificationStatus.objects.filter(recipient=r, state='failed').first() if r else None
    if s:
        print('   ', str((s.payload or {}).get('error'))[:160])"
```

`latest_status` runs `sent` → `delivered` → `read`, or `failed`. If nothing appears at all,
the trigger or its template is inactive and the event was never created.
