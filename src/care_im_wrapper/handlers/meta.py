import logging
from typing import Any

from celery.result import AsyncResult
from django.core.cache import cache
from django.dispatch import receiver

from care_im_wrapper.core.rate_limit import is_rate_limited
from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.messaging.normalize import normalize_inbound
from care_im_wrapper.settings import plugin_settings
from care_im_wrapper.signals import inbound_message_received, inbound_status_updated
from care_im_wrapper.tasks import process_inbound_message, process_status_update

logger = logging.getLogger(__name__)


@receiver(inbound_message_received)
def on_meta_message(*, payload: dict[str, Any], channel: str, **kwargs: Any) -> None:
    message = normalize_inbound(payload, channel)
    if message is None:
        logger.warning("on_meta_message: could not normalize payload, dropping")
        return

    if is_rate_limited(message.phone_number):
        logger.info(
            "Rate limit exceeded at handler for %s. Skipping task creation.",
            mask_phone_number(message.phone_number),
        )
        return

    pending_task_key = f"pending_task:{message.phone_number}"
    existing_task_id = cache.get(pending_task_key)
    if existing_task_id:
        AsyncResult(str(existing_task_id)).revoke(terminate=False)

    result = process_inbound_message.apply_async(  # pyright: ignore[reportCallIssue]
        args=[
            message.phone_number,
            message.text,
            message.channel,
            message.raw_id,
        ],
        countdown=plugin_settings.DEBOUNCE_SECONDS,
    )

    cache.set(pending_task_key, result.id, timeout=plugin_settings.DEBOUNCE_SECONDS + 2)


@receiver(inbound_status_updated)
def on_meta_status(*, payload: dict[str, Any], channel: str, **kwargs: Any) -> None:
    process_status_update.delay(payload=payload, channel=channel)  # pyright: ignore[reportCallIssue]
