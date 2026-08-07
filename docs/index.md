# care_im_wrapper

A CARE plugin providing a patient/staff chatbot over instant messaging, plus templated
staff notifications.

The plugin has two halves that share a provider layer:

- **Conversation** — an inbound WhatsApp message drives a state machine
  (`conversation/handlers.py`) that authenticates the sender, offers a menu, and reads
  their clinical records back to them, paged to fit the channel's limits.
- **Notifications** — a CARE signal, or a staff member via the REST API, fires a
  `NotificationEvent`, which is rendered from an approved provider template and
  dispatched to each recipient by a Celery task.

```{toctree}
:maxdepth: 2
:caption: Guides

installation
usage
notification-triggers
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
reference/generated/modules
```

```{toctree}
:maxdepth: 2
:caption: Testing

manual-test-checklist
```

## Indices

- {ref}`genindex`
- {ref}`modindex`
- {ref}`search`
