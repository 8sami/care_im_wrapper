"""Billing notifications: an invoice being issued or cancelled, and a payment being recorded."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from care.emr.models.invoice import Invoice  # pyright: ignore[reportMissingImports]
from care.emr.models.payment_reconciliation import PaymentReconciliation  # pyright: ignore[reportMissingImports]
from care.emr.resources.invoice.spec import (  # pyright: ignore[reportMissingImports]
    INVOICE_CANCELLED_STATUS,
    InvoiceStatusOptions,
)
from care.emr.resources.payment_reconciliation.spec import (  # pyright: ignore[reportMissingImports]
    PaymentReconciliationOutcomeOptions,
)
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from care_im_wrapper.handlers.dispatch import (
    NotificationRecipientSpec,
    fire_notification_event,
    track_previous_field,
)
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY, InvoiceContext
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

# Set on the invoice_issued / invoice_cancelled / payment_recorded triggers' context_slug.
INVOICE_CONTEXT_SLUG = "invoice"


# Shown when a payment is against the account rather than a specific invoice.
NO_INVOICE = "Not applicable"

# Statuses a cancellation notifies from. `draft` is absent: nothing was ever sent to the
# patient. CARE rejects draft -> balanced, so a balanced invoice was necessarily issued first.
NOTIFIED_INVOICE_STATUS = (
    InvoiceStatusOptions.issued.value,
    InvoiceStatusOptions.balanced.value,
)


def _resolve_invoice_facility(invoice: Invoice) -> Any | None:
    return invoice.facility


def _resolve_payment_facility(payment: PaymentReconciliation) -> Any | None:
    return payment.facility


_FACILITY_RESOLVERS[Invoice] = _resolve_invoice_facility
_FACILITY_RESOLVERS[PaymentReconciliation] = _resolve_payment_facility
NOTIFICATION_CONTEXT_REGISTRY.register(INVOICE_CONTEXT_SLUG, InvoiceContext)


def format_amount(amount: Decimal | float | None) -> str:
    """Two decimal places, thousands-separated. Unit-less: CARE stores no currency."""
    if amount is None:
        return ""
    return f"{Decimal(amount):,.2f}"


def describe_invoice_number(invoice: Invoice) -> str:
    """The number to quote in the message, never blank."""
    return str(invoice.number or invoice.external_id)


def _fire_billing_event(
    related_object: Any,
    *,
    trigger_slug: str,
    status: str,
    amount: Decimal | float | None,
    account: Any,
    invoice: Invoice | None,
    title_noun: str = "Payment",
) -> None:
    """Patient and account are handler-supplied so a payment with no invoice can still
    send: PaymentReconciliation has an account but no patient, so the template cannot
    read them off the object."""
    patient = account.patient
    invoice_number = describe_invoice_number(invoice) if invoice is not None else NO_INVOICE
    fire_notification_event(
        trigger_slug=trigger_slug,
        title=f"{title_noun} {status} — {invoice_number}",
        related_object=related_object,
        recipient=NotificationRecipientSpec(content_object=patient, phone_number=patient.phone_number),
        variable_values={
            "status": status,
            "header_status": status.capitalize(),
            "amount": format_amount(amount),
            "invoice_number": invoice_number,
            "patient_name": patient.name,
            "patient_account_name": account.name,
        },
    )


pre_save.connect(track_previous_field("status"), sender=Invoice, weak=False)


@receiver(post_save, sender=Invoice)
def on_invoice_post_save(sender: type[Invoice], instance: Invoice, created: bool, **kwargs: Any) -> None:
    """Fires when an invoice reaches `issued`, and when one already sent to the patient is
    cancelled. Drafts stay silent: CARE's cancel endpoint accepts a draft, so cancellation
    checks the status being left, not just the one being entered."""
    previous_status = None if created else getattr(instance, "_previous_status", None)
    if previous_status == instance.status:
        return

    if instance.status == InvoiceStatusOptions.issued.value:
        _fire_billing_event(
            instance,
            trigger_slug=plugin_settings.BILLING_TRIGGER_SLUGS["invoice_issued"],
            status="issued",
            amount=instance.total_gross,
            account=instance.account,
            invoice=instance,
        )
        return

    # `entered_in_error` is an internal distinction: both read as "cancelled" to the patient.
    if instance.status in INVOICE_CANCELLED_STATUS and previous_status in NOTIFIED_INVOICE_STATUS:
        _fire_billing_event(
            instance,
            trigger_slug=plugin_settings.BILLING_TRIGGER_SLUGS["invoice_cancelled"],
            status="cancelled",
            amount=instance.total_gross,
            account=instance.account,
            invoice=instance,
            title_noun="Invoice",
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

    # A payment against the account rather than an invoice still notifies; it fires with
    # itself as the related object and quotes no invoice number.
    invoice = instance.target_invoice
    _fire_billing_event(
        invoice if invoice is not None else instance,
        trigger_slug=plugin_settings.BILLING_TRIGGER_SLUGS["payment_recorded"],
        status="confirmed",
        amount=instance.amount,
        account=instance.account,
        invoice=invoice,
    )
