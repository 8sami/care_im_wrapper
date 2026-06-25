from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.core.cache import cache

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    time_limit=int(plugin_settings.DEBOUNCE_SECONDS + plugin_settings.TASK_EXECUTION_BUFFER_SECONDS),
)
def process_inbound_message(
    self,
    phone_number: str,
    text: str,
    channel: str,
    raw_id: str | None = None,
    dedup_key: str | None = None,
) -> None:
    # Handle same raw_id
    if raw_id:
        dup_key = f"msg_seen:{raw_id}"
        if not cache.add(dup_key, True, timeout=300):
            logger.info("Duplicate message detected (ID: %s). Dropping.", raw_id)
            return

    try:
        run_state_machine(phone_number, text, channel)
    except Exception as exc:
        logger.error("process_inbound_message failed: %s", exc)
        raise self.retry(exc=exc) from exc
    finally:
        if dedup_key:
            cache.delete(dedup_key)  # release so next message can enqueue


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_status_update(self, payload: dict[str, Any], channel: str) -> None:
    # TODO: Week 6 -> notification status tracking
    try:
        pass
    except Exception as exc:
        logger.error("process_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc
