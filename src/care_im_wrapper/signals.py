from django.dispatch import Signal

# kwargs: sender=class, payload=dict, channel=str
meta_message_received = Signal()

# kwargs: sender=class, payload=dict, channel=str
meta_status_updated = Signal()

# TODO: Week 5 — auth_succeeded, auth_failed for audit logging
