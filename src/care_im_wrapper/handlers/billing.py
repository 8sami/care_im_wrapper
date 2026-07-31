"""Billing notifications: an invoice being issued, and a payment being recorded against one."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from care.emr.models.invoice import Invoice  # pyright: ignore[reportMissingImports]
from care.emr.models.payment_reconciliation import PaymentReconciliation  # pyright: ignore[reportMissingImports]
from care.emr.resources.invoice.spec import InvoiceStatusOptions  # pyright: ignore[reportMissingImports]
from care.emr.resources.payment_reconciliation.spec import (  # pyright: ignore[reportMissingImports]
    PaymentReconciliationOutcomeOptions,
)
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from care_im_wrapper.handlers.dispatch import (
    NotificationRecipientSpec,
    fire_notification_event,
    track_previous_field,
    track_previous_status,
)
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY, InvoiceContext
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

# Set on the invoice_issued / payment_recorded triggers' context_slug.
INVOICE_CONTEXT_SLUG = "invoice"


def _resolve_invoice_facility(invoice: Invoice) -> Any | None:
    return invoice.facility


_FACILITY_RESOLVERS[Invoice] = _resolve_invoice_facility
NOTIFICATION_CONTEXT_REGISTRY.register(INVOICE_CONTEXT_SLUG, InvoiceContext)


def format_amount(amount: Decimal | float | None) -> str:
    """Two decimal places, thousands-separated. Unit-less: CARE stores no currency."""
    if amount is None:
        return ""
    return f"{Decimal(amount):,.2f}"


def describe_invoice_number(invoice: Invoice) -> str:
    """The number to quote in the message, never blank."""
    return str(invoice.number or invoice.external_id)


def _fire_billing_event(invoice: Invoice, *, trigger_slug: str, status: str, amount: Decimal | float | None) -> None:
    patient = invoice.patient
    invoice_number = describe_invoice_number(invoice)
    fire_notification_event(
        trigger_slug=trigger_slug,
        title=f"Payment {status} — invoice {invoice_number}",
        related_object=invoice,
        recipient=NotificationRecipientSpec(content_object=patient, phone_number=patient.phone_number),
        variable_values={
            "status": status,
            "header_status": status.capitalize(),
            "amount": format_amount(amount),
            "invoice_number": invoice_number,
        },
    )


pre_save.connect(track_previous_status, sender=Invoice)


@receiver(post_save, sender=Invoice)
def on_invoice_post_save(sender: type[Invoice], instance: Invoice, created: bool, **kwargs: Any) -> None:
    """Fires when an invoice reaches `issued`. Drafts stay silent."""
    if instance.status != InvoiceStatusOptions.issued.value:
        return

    if not created and getattr(instance, "_previous_status", None) == instance.status:
        return

    _fire_billing_event(
        instance,
        trigger_slug=plugin_settings.BILLING_TRIGGER_SLUGS["invoice_issued"],
        status="issued",
        amount=instance.total_gross,
    )


pre_save.connect(track_previous_field("outcome"), sender=PaymentReconciliation, weak=False)


@receiver(post_save, sender=PaymentReconciliation)
def on_payment_post_save(
    sender: type[PaymentReconciliation], instance: PaymentReconciliation, created: bool, **kwargs: Any
) -> None:
    """Fires when a payment completes. `outcome`, not `status`, says the money moved."""
    if instance.outcome != PaymentReconciliationOutcomeOptions.complete.value:
        return

    if not created and getattr(instance, "_previous_outcome", None) == instance.outcome:
        return

    invoice = instance.target_invoice
    if invoice is None:
        logger.info(
            "on_payment_post_save: payment %s has no target invoice, skipping notification",
            instance.external_id,
        )
        return

    _fire_billing_event(
        invoice,
        trigger_slug=plugin_settings.BILLING_TRIGGER_SLUGS["payment_recorded"],
        status="confirmed",
        amount=instance.amount,
    )
