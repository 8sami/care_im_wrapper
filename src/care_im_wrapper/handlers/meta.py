from __future__ import annotations

import logging
from typing import Any

from django.dispatch import receiver

from care_im_wrapper.signals import meta_message_received, meta_status_updated
from care_im_wrapper.tasks import process_meta_message, process_meta_status_update

logger = logging.getLogger(__name__)


@receiver(meta_message_received)
def on_meta_message(*, payload: dict[str, Any], channel: str, **kwargs: Any) -> None:
    process_meta_message.delay(payload=payload, channel=channel)  # pyright: ignore[reportCallIssue]


@receiver(meta_status_updated)
def on_meta_status(*, payload: dict[str, Any], channel: str, **kwargs: Any) -> None:
    process_meta_status_update.delay(payload=payload, channel=channel)  # pyright: ignore[reportCallIssue]
