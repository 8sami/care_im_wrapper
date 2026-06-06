import logging

from django.dispatch import receiver

from care_im_wrapper.signals import meta_message_received, meta_status_updated
from care_im_wrapper.tasks import process_meta_message, process_meta_status_update

logger = logging.getLogger(__name__)


@receiver(meta_message_received)
def on_meta_message(sender, payload: dict, channel: str, **kwargs) -> None:
    process_meta_message.delay(payload=payload, channel=channel)


@receiver(meta_status_updated)
def on_meta_status(sender, payload: dict, channel: str, **kwargs) -> None:
    process_meta_status_update.delay(payload=payload, channel=channel)
