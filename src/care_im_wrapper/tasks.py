from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.core.cache import cache

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.core.sanitize import mask_phone_number

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_inbound_message(
    self,
    phone_number: str,
    text: str,
    channel: str,
    raw_id: str | None = None,
) -> None:
    # Handle same raw_id
    if raw_id:
        dup_key = f"msg_seen:{raw_id}"
        if not cache.add(dup_key, True, timeout=300):
            logger.info("Duplicate message detected (ID: %s). Dropping.", raw_id)
            return

    # Handle same user/session
    lock_key = f"session_lock:{phone_number}:{channel}"
    if not cache.add(lock_key, True, timeout=30):
        logger.info(
            "Concurrency detected for %s:%s. Dropping message to prevent task storm.",
            mask_phone_number(phone_number),
            channel,
        )
        return

    try:
        run_state_machine(phone_number, text, channel)
    except Exception as exc:
        logger.error("process_inbound_message failed: %s", exc)
        raise self.retry(exc=exc) from exc
    finally:
        cache.delete(lock_key)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_status_update(self, payload: dict[str, Any], channel: str) -> None:
    # TODO: Week 6 -> notification status tracking
    try:
        pass
    except Exception as exc:
        logger.error("process_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc
