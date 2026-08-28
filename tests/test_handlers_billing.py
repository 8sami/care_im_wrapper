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
        self.patient = self.create_patient(phone_number=PATIENT_PHONE, name="Jane Doe")
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


class InvoiceCancelledSignalTests(BillingSignalTestBase):
    """Cancelling an issued invoice notifies the patient. Both of CARE's cancellation
    statuses read as "cancelled" to them, and a draft invoice never notifies at all."""

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_issued_invoice_cancelled_fires(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        invoice.status = "cancelled"
        invoice.save()

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "invoice_cancelled")
        self.assertEqual(kwargs["related_object"], invoice)
        self.assertEqual(kwargs["recipient"].phone_number, PATIENT_PHONE)
        self.assertEqual(kwargs["variable_values"]["status"], "cancelled")
        self.assertEqual(kwargs["variable_values"]["header_status"], "Cancelled")
        self.assertEqual(kwargs["variable_values"]["amount"], "14,000.00")
        self.assertEqual(kwargs["variable_values"]["invoice_number"], "#1322")

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_issued_invoice_entered_in_error_reads_as_cancelled(self, mock_fire):
        """`entered_in_error` is an internal distinction and must not reach the patient."""
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        invoice.status = "entered_in_error"
        invoice.save()

        mock_fire.assert_called_once()
        values = mock_fire.call_args.kwargs["variable_values"]
        self.assertEqual(mock_fire.call_args.kwargs["trigger_slug"], "invoice_cancelled")
        self.assertEqual(values["status"], "cancelled")
        self.assertEqual(values["header_status"], "Cancelled")
        self.assertNotIn("error", str(values).lower())

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_event_title_names_the_invoice_not_a_payment(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        invoice.status = "cancelled"
        invoice.save()

        self.assertEqual(mock_fire.call_args.kwargs["title"], "Invoice cancelled — #1322")

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_balanced_invoice_cancelled_fires(self, mock_fire):
        """CARE rejects draft -> balanced, so a balanced invoice was necessarily issued
        first and the patient has already been told about it."""
        invoice = self._create_invoice(status="issued")
        invoice.status = "balanced"
        invoice.save()
        mock_fire.reset_mock()

        invoice.status = "cancelled"
        invoice.save()

        mock_fire.assert_called_once()
        self.assertEqual(mock_fire.call_args.kwargs["trigger_slug"], "invoice_cancelled")
        self.assertEqual(mock_fire.call_args.kwargs["variable_values"]["status"], "cancelled")

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_balanced_invoice_entered_in_error_reads_as_cancelled(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        invoice.status = "balanced"
        invoice.save()
        mock_fire.reset_mock()

        invoice.status = "entered_in_error"
        invoice.save()

        mock_fire.assert_called_once()
        self.assertEqual(mock_fire.call_args.kwargs["variable_values"]["status"], "cancelled")

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_reaching_balanced_does_not_itself_notify(self, mock_fire):
        """Only issue and cancellation notify; being paid off is not its own message."""
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        invoice.status = "balanced"
        invoice.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_draft_invoice_cancelled_stays_silent(self, mock_fire):
        """CARE's cancel endpoint accepts a draft, but the patient never saw it."""
        invoice = self._create_invoice(status="draft")
        mock_fire.reset_mock()

        invoice.status = "cancelled"
        invoice.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_draft_invoice_entered_in_error_stays_silent(self, mock_fire):
        invoice = self._create_invoice(status="draft")
        mock_fire.reset_mock()

        invoice.status = "entered_in_error"
        invoice.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_other_updates_to_a_draft_invoice_stay_silent(self, mock_fire):
        invoice = self._create_invoice(status="draft")
        mock_fire.reset_mock()

        invoice.note = "edited while still a draft"
        invoice.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_invoice_created_already_cancelled_stays_silent(self, mock_fire):
        """Nothing was ever issued to the patient, so there is nothing to retract."""
        self._create_invoice(status="cancelled")

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_resaving_a_cancelled_invoice_does_not_re_fire(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        invoice.status = "cancelled"
        invoice.save()
        mock_fire.reset_mock()

        invoice.note = "edited after cancellation"
        invoice.save()

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_no_variable_value_is_blank(self, mock_fire):
        """A blank parameter makes Meta reject the whole send."""
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        invoice.status = "cancelled"
        invoice.save()

        values = mock_fire.call_args.kwargs["variable_values"]
        self.assertEqual([k for k, v in values.items() if not str(v).strip()], [])


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
        self.assertEqual(kwargs["variable_values"]["patient_name"], self.patient.name)

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_incomplete_payment_stays_silent(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        mock_fire.reset_mock()

        self._create_payment(outcome="partial", target_invoice=invoice)

        mock_fire.assert_not_called()

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_complete_payment_without_a_target_invoice_still_notifies(self, mock_fire):
        """A deposit against the account has no invoice to quote, but the patient should
        still hear that the payment went through."""
        payment = self._create_payment(outcome="complete", target_invoice=None)

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["related_object"], payment)
        self.assertEqual(kwargs["recipient"].phone_number, PATIENT_PHONE)
        values = kwargs["variable_values"]
        self.assertEqual(values["invoice_number"], "Not applicable")
        self.assertEqual(values["patient_name"], self.patient.name)
        self.assertEqual(values["patient_account_name"], self.account.name)

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_no_variable_value_is_blank_without_an_invoice(self, mock_fire):
        """A blank parameter makes Meta reject the whole send."""
        self._create_payment(outcome="complete", target_invoice=None)

        values = mock_fire.call_args.kwargs["variable_values"]
        self.assertEqual([k for k, v in values.items() if not str(v).strip()], [])

    @patch("care_im_wrapper.handlers.billing.fire_notification_event")
    def test_resaving_a_complete_payment_does_not_re_fire(self, mock_fire):
        invoice = self._create_invoice(status="issued")
        payment = self._create_payment(outcome="complete", target_invoice=invoice)
        mock_fire.reset_mock()

        payment.note = "edited after completion"
        payment.save()

        mock_fire.assert_not_called()
