from unittest.mock import MagicMock, patch

from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.tasks import notify_document_ready


class DiagnosticReportTestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )

    def _create_service_request(self, status="active"):
        return self.create_service_request(
            patient=self.patient, facility=self.facility, encounter=self.encounter, status=status
        )

    def _create_report(self, service_request, status="final"):
        from care.emr.models.diagnostic_report import DiagnosticReport

        return DiagnosticReport.objects.create(
            patient=self.patient,
            encounter=self.encounter,
            service_request=service_request,
            status=status,
        )


class ServiceRequestCompletedSignalTests(DiagnosticReportTestBase):
    """The document is released when the ServiceRequest is marked completed, not when the
    DiagnosticReport reaches 'final'.

    The signal only enqueues -- locating the document can render a PDF, which must not run
    inside the clinical write. Each case is wrapped in captureOnCommitCallbacks because the
    receiver enqueues via transaction.on_commit, which TestCase would otherwise never run.
    """

    def _complete(self, service_request):
        with (
            patch("care_im_wrapper.handlers.diagnostic_report.notify_document_ready") as mock_task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            service_request.status = "completed"
            service_request.save()
        return mock_task

    def test_completing_the_order_enqueues_the_notification_task(self):
        service_request = self._create_service_request()
        report = self._create_report(service_request)

        mock_task = self._complete(service_request)

        mock_task.delay.assert_called_once_with(str(report.external_id))

    def test_creating_an_order_already_completed_does_not_enqueue(self):
        with (
            patch("care_im_wrapper.handlers.diagnostic_report.notify_document_ready") as mock_task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            service_request = self._create_service_request(status="completed")
            self._create_report(service_request)

        mock_task.delay.assert_not_called()

    def test_saving_an_already_completed_order_does_not_re_enqueue(self):
        service_request = self._create_service_request(status="completed")
        self._create_report(service_request)

        with (
            patch("care_im_wrapper.handlers.diagnostic_report.notify_document_ready") as mock_task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            service_request.save()  # no status change

        mock_task.delay.assert_not_called()

    def test_transition_to_a_non_completed_status_does_not_enqueue(self):
        service_request = self._create_service_request(status="draft")
        self._create_report(service_request)

        with (
            patch("care_im_wrapper.handlers.diagnostic_report.notify_document_ready") as mock_task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            service_request.status = "active"
            service_request.save()

        mock_task.delay.assert_not_called()

    def test_completing_an_order_with_no_final_report_enqueues_nothing(self):
        """Covers both a non-report order and one whose report was voided before completion."""
        service_request = self._create_service_request()
        self._create_report(service_request, status="entered_in_error")

        mock_task = self._complete(service_request)

        mock_task.delay.assert_not_called()

    def test_nothing_is_enqueued_until_the_transaction_commits(self):
        service_request = self._create_service_request()
        self._create_report(service_request)

        with patch("care_im_wrapper.handlers.diagnostic_report.notify_document_ready") as mock_task:
            with self.captureOnCommitCallbacks(execute=False):
                service_request.status = "completed"
                service_request.save()
                # Still inside the transaction: a worker must not be able to look for this
                # report before the write that finalised it is durable.
                mock_task.delay.assert_not_called()


class NotifyDocumentReadyTaskTests(DiagnosticReportTestBase):
    """The worker half: mint a link for the report, then fire its notification event."""

    def _patched(self, link=None, link_error=None):
        kwargs = {"side_effect": link_error} if link_error else {"return_value": link}
        return (
            patch("care_im_wrapper.tasks.get_system_document_link", **kwargs),
            patch("care_im_wrapper.tasks.fire_notification_event"),
        )

    def test_fires_notification_with_the_document_link_token(self):
        service_request = self._create_service_request()
        report = self._create_report(service_request)
        get_link, fire = self._patched(link=MagicMock(token="abc123token"))

        with get_link as mock_get_link, fire as mock_fire:
            notify_document_ready(str(report.external_id))

        mock_get_link.assert_called_once()
        self.assertEqual(mock_get_link.call_args.args[0].pk, self.patient.pk)

        mock_fire.assert_called_once()
        kwargs = mock_fire.call_args.kwargs
        self.assertEqual(kwargs["trigger_slug"], "document_ready_update")
        self.assertEqual(kwargs["related_object"], report)
        self.assertEqual(kwargs["recipient"].content_object, self.patient)
        self.assertEqual(kwargs["recipient"].phone_number, self.patient.phone_number)
        self.assertEqual(
            kwargs["variable_values"],
            {"document_type": "diagnostic_report", "document_url_suffix": "abc123token"},
        )

    def test_document_unavailable_skips_notification_without_crashing(self):
        service_request = self._create_service_request()
        report = self._create_report(service_request)
        get_link, fire = self._patched(link_error=DocumentUnavailableError("no template"))

        with get_link, fire as mock_fire:
            notify_document_ready(str(report.external_id))  # must not raise

        mock_fire.assert_not_called()

    def test_a_report_deleted_before_the_task_ran_is_a_no_op(self):
        import uuid

        _, fire = self._patched(link=MagicMock(token="tok"))

        with fire as mock_fire:
            notify_document_ready(str(uuid.uuid4()))  # must not raise

        mock_fire.assert_not_called()
