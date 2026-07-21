from django.dispatch import Signal

# Provider-neutral: fired by any webhook provider (Meta today), not just WhatsApp.
# kwargs: sender=class, payload=dict, channel=str
inbound_message_received = Signal()

# kwargs: sender=class, payload=dict, channel=str
inbound_status_updated = Signal()
