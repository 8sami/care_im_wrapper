from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from care_im_wrapper.conversation.handlers import run_state_machine

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_inbound_message(
    self,
    phone_number: str,
    text: str,
    channel: str,
    raw_id: str | None = None,
) -> None:
    logger.info("process_inbound_message: channel=%s", channel)
    try:
        run_state_machine(phone_number, text, channel)
    except Exception as exc:
        logger.error("process_inbound_message failed: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_status_update(self, payload: dict[str, Any], channel: str) -> None:
    # TODO: Week 6 -> notification status tracking
    # logger.info("process_status_update: channel=%s", channel)
    try:
        pass
    except Exception as exc:
        logger.error("process_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc
