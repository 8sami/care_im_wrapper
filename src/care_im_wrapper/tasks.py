from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.core.cache import cache

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
)
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=plugin_settings.TASK_MAX_RETRIES,
    default_retry_delay=plugin_settings.TASK_RETRY_DELAY_SECONDS,
    time_limit=int(plugin_settings.DEBOUNCE_SECONDS + plugin_settings.TASK_EXECUTION_BUFFER_SECONDS),
)
def process_inbound_message(
    self,
    phone_number: str,
    text: str,
    channel: str,
    raw_id: str | None = None,
) -> None:
    if raw_id:
        dup_key = f"msg_seen:{raw_id}"
        if not cache.add(dup_key, True, timeout=plugin_settings.MESSAGE_DEDUP_TIMEOUT_SECONDS):
            logger.info("Duplicate message detected (ID: %s). Dropping.", raw_id)
            return

    cache.delete(f"pending_task:{phone_number}")

    try:
        run_state_machine(phone_number, text, channel)
    except (WhatsAppPairRateLimitError, WhatsAppNetworkError, WhatsAppServerError) as exc:
        logger.warning("Transient WhatsApp error for %s: %s. Retrying.", phone_number, exc)
        raise self.retry(exc=exc) from exc
    except WhatsAppBadRequestError as exc:
        logger.error("Permanent WhatsApp error for %s: %s. Dropping message.", phone_number, exc)
    except Exception as exc:
        logger.error("process_inbound_message failed: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(
    bind=True,
    max_retries=plugin_settings.TASK_MAX_RETRIES,
    default_retry_delay=plugin_settings.TASK_RETRY_DELAY_SECONDS,
)
def process_status_update(self, payload: dict[str, Any], channel: str) -> None:
    # TODO: Week 6 -> notification status tracking
    try:
        pass
    except Exception as exc:
        logger.error("process_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc
