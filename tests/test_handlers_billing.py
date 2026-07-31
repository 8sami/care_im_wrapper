from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from care.utils.tests.base import CareAPITestBase
from django.test import SimpleTestCase

from care_im_wrapper.handlers.billing import describe_invoice_number, format_amount

PATIENT_PHONE = "+919876500031"


class FormatAmountTests(CareAPITestBase):
    def test_thousands_separated_to_two_places(self):
        self.assertEqual(format_amount(Decimal("14000")), "14,000.00")

    def test_keeps_paise(self):
        self.assertEqual(format_amount(Decimal("1234.5")), "1,234.50")

    def test_none_renders_empty(self):
        self.assertEqual(format_amount(None), "")


class DescribeInvoiceNumberTests(SimpleTestCase):
    """Invoice.number is nullable and stays empty when the facility's identifier."""

    def test_prefers_the_invoice_number(self):
        invoice = SimpleNamespace(number="INV-2026-0042", external_id=uuid4())
        self.assertEqual(describe_invoice_number(invoice), "INV-2026-0042")

    def test_falls_back_to_external_id_when_number_is_empty(self):
        external_id = uuid4()
        invoice = SimpleNamespace(number="", external_id=external_id)
        self.assertEqual(describe_invoice_number(invoice), str(external_id))

    def test_falls_back_to_external_id_when_number_is_none(self):
        external_id = uuid4()
        invoice = SimpleNamespace(number=None, external_id=external_id)
        self.assertEqual(describe_invoice_number(invoice), str(external_id))

    def test_never_returns_blank(self):
        for number in ("", None):
            with self.subTest(number=number):
                invoice = SimpleNamespace(number=number, external_id=uuid4())
                self.assertTrue(describe_invoice_number(invoice).strip())


class BillingSignalTestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient(phone_number=PATIENT_PHONE)
        self.facility = self.create_facility(user=self.user)
        self.account = self._create_account()

    def _create_account(self):
        from care.emr.models.account import Account

        return Account.objects.create(
            facility=self.facility,
            patient=self.patient,
            name="JANE DOE",
            status="active",
            billing_status="open",
        )

    def _create_invoice(self, status="draft", **kwargs):
        from care.emr.models.invoice import Invoice

        data = {
            "facility": self.facility,
            "patient": self.patient,
            "account": self.account,
            "status": status,
            "number": "#1322",
            "total_gross": Decimal("14000"),
        }
        data.update(kwargs)
        return Invoice.objects.create(**data)

    def _create_payment(self, outcome="queued", target_invoice=None, **kwargs):
        from care.emr.models.payment_reconciliation import PaymentReconciliation

        data = {
            "facility": self.facility,
            "account": self.account,
            "target_invoice": target_invoice,
            "reconciliation_type": "payment",
            "status": "active",
            "kind": "deposit",
            "issuer_type": "patient",
            "outcome": outcome,
            "method": "cash",
            "tendered_amount": Decimal("5000"),
            "returned_amount": Decimal("0"),
            "amount": Decimal("5000"),
        }
        data.update(kwargs)
        return PaymentReconciliation.objects.create(**data)


class InvoiceIssuedSignalTests(BillingSignalTestBase):
    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_transition_to_issued_fires(self, mock_fire):
        invoice = self._create_invoice(status="draft")
        mock_fire.reset_mock()

        invoice.status = "issued"
        invoice.save()

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "invoice_issued")
        self.assertEqual(kwargs["related_object"], invoice)
        self.assertEqual(kwargs["recipient"].phone_number, PATIENT_PHONE)
        self.assertEqual(kwargs["variable_values"]["status"], "issued")
        self.assertEqual(kwargs["variable_values"]["header_status"], "Issued")
        self.assertEqual(kwargs["variable_values"]["amount"], "14,000.00")
        self.assertTrue(kwargs["variable_values"]["invoice_number"].strip())

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_created_already_issued_fires(self, mock_fire):
        self._create_invoice(status="issued")

        mock_fire.assert_called_once()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_draft_invoice_stays_silent(self, mock_fire):
        self._create_invoice(status="draft")

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_resaving_an_issued_invoice_does_not_re_fire(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        invoice.note = "edited after issue"
        invoice.save()

        mock_fire.assert_not_called()


class PaymentRecordedSignalTests(BillingSignalTestBase):
    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_transition_to_complete_fires_against_the_target_invoice(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        payment = self._create_payment(outcome="queued", target_invoice=invoice)
        mock_fire.reset_mock()

        payment.outcome = "complete"
        payment.save()

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "payment_recorded")
        # The invoice, not the payment: the template quotes an invoice number.
        self.assertEqual(kwargs["related_object"], invoice)
        self.assertEqual(kwargs["variable_values"]["status"], "confirmed")
        self.assertEqual(kwargs["variable_values"]["amount"], "5,000.00")

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_incomplete_payment_stays_silent(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        self._create_payment(outcome="partial", target_invoice=invoice)

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_complete_payment_without_a_target_invoice_is_skipped(self, mock_fire):
        self._create_payment(outcome="complete", target_invoice=None)

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_resaving_a_complete_payment_does_not_re_fire(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        payment = self._create_payment(outcome="complete", target_invoice=invoice)
        mock_fire.reset_mock()

        payment.note = "edited after completion"
        payment.save()

        mock_fire.assert_not_called()
