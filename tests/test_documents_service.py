import uuid
from datetime import timedelta
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.utils import timezone

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.exceptions import PermissionDeniedError
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.documents.service import (
    DocumentRequest,
    build_document_url,
    get_or_create_document_link,
    get_system_document_link,
)
from care_im_wrapper.models import ConversationSession, DocumentLink, DocumentLinkObjectKind


class DocumentServiceTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )
        self.service_request = self.create_service_request(
            patient=self.patient, facility=self.facility, encounter=self.encounter
        )

    def _create_template(self, **kwargs):
        from care.emr.models.report.template import Template

        data = {
            "slug": f"discharge-summary-{uuid.uuid4().hex[:8]}",
            "name": "Discharge Summary",
            "status": "active",
            "template_data": "<html></html>",
            "template_type": "discharge_summary",
            "default_format": "pdf",
            "context": "encounter_base",
            "facility": None,
        }
        data.update(kwargs)
        return Template.objects.create(**data)

    def _create_discharge_summary_report(self, **kwargs):
        from care.emr.models.report.report_upload import ReportUpload

        data = {
            "template": self._create_template(),
            "name": "discharge-summary",
            "associating_id": str(self.encounter.external_id),
            "report_type": "discharge_summary",
            "upload_completed": True,
        }
        data.update(kwargs)
        return ReportUpload.objects.create(**data)

    def _create_diagnostic_report(self, **kwargs):
        from care.emr.models.diagnostic_report import DiagnosticReport

        data = {
            "patient": self.patient,
            "encounter": self.encounter,
            "service_request": self.service_request,
            "status": "final",
        }
        data.update(kwargs)
        return DiagnosticReport.objects.create(**data)

    def _create_diagnostic_file(self, report, **kwargs):
        from care.emr.models.file_upload import FileUpload

        data = {
            "name": "lab-result",
            "associating_id": str(report.external_id),
            "file_type": "diagnostic_report",
            "file_category": "unspecified",
            "upload_completed": True,
        }
        data.update(kwargs)
        return FileUpload.objects.create(**data)

    def _encounter_request(self, report_upload):
        """The shape resolve_encounter_document builds: an encounter's generated discharge summary."""
        return DocumentRequest(document_type="discharge_summary", encounter=self.encounter, report_upload=report_upload)

    def _lab_report_request(self, report):
        return DocumentRequest(document_type="diagnostic_report", encounter=self.encounter, diagnostic_report=report)

    def _patient_actor(self, patient=None):
        return Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=patient or self.patient)

    def test_patient_actor_mismatched_patient_raises_permission_denied(self):
        other_patient = self.create_patient()
        report_upload = self._create_discharge_summary_report()
        document_request = self._encounter_request(report_upload)

        with self.assertRaises(PermissionDeniedError):
            get_or_create_document_link(self._patient_actor(), other_patient, document_request, provider="whatsapp")

    def test_discharge_summary_link_addresses_the_generated_report(self):
        report_upload = self._create_discharge_summary_report()
        document_request = self._encounter_request(report_upload)

        link = get_or_create_document_link(self._patient_actor(), self.patient, document_request, provider="whatsapp")

        self.assertEqual(link.object_kind, DocumentLinkObjectKind.REPORT_UPLOAD)
        self.assertEqual(link.object_external_id, report_upload.external_id)

    def test_discharge_summary_never_generates_a_new_report(self):
        """Issue #27: an encounter with a discharge summary staff already generated must be
        served that report, never a freshly generated one."""
        report_upload = self._create_discharge_summary_report()
        document_request = self._encounter_request(report_upload)

        with patch("care.emr.reports.report_utils.generate_and_upload_report") as mock_generate:
            link = get_or_create_document_link(
                self._patient_actor(), self.patient, document_request, provider="whatsapp"
            )

        mock_generate.assert_not_called()
        self.assertEqual(link.object_kind, DocumentLinkObjectKind.REPORT_UPLOAD)
        self.assertEqual(link.object_external_id, report_upload.external_id)

    def test_document_request_with_no_subject_raises_document_unavailable_error(self):
        document_request = DocumentRequest(document_type="discharge_summary", encounter=self.encounter)

        with self.assertRaises(DocumentUnavailableError):
            get_or_create_document_link(self._patient_actor(), self.patient, document_request, provider="whatsapp")

    def test_lab_report_link_addresses_the_report_not_a_file(self):
        """The public page renders the report and shows uploads as attachments inside it,
        so the link must carry the report -- not one of its files."""
        report = self._create_diagnostic_report()
        self._create_diagnostic_file(report)

        link = get_or_create_document_link(
            self._patient_actor(), self.patient, self._lab_report_request(report), provider="whatsapp"
        )

        self.assertEqual(link.object_kind, DocumentLinkObjectKind.DIAGNOSTIC_REPORT)
        self.assertEqual(link.object_external_id, report.external_id)

    def test_lab_report_without_an_uploaded_file_is_still_deliverable(self):
        """An attachment is supporting material, not the report. A report with none still
        has patient details, observations and a conclusion to render."""
        report = self._create_diagnostic_report()

        link = get_or_create_document_link(
            self._patient_actor(), self.patient, self._lab_report_request(report), provider="whatsapp"
        )

        self.assertEqual(link.object_external_id, report.external_id)

    def test_lab_report_never_generates_an_encounter_report(self):
        # A lab report must never fall back to an encounter/discharge document (issue #21).
        report = self._create_diagnostic_report()

        with patch("care.emr.reports.report_utils.generate_and_upload_report") as mock_generate:
            link = get_or_create_document_link(
                self._patient_actor(), self.patient, self._lab_report_request(report), provider="whatsapp"
            )

        mock_generate.assert_not_called()
        self.assertEqual(link.object_kind, DocumentLinkObjectKind.DIAGNOSTIC_REPORT)

    def test_existing_valid_link_is_reused_not_recreated(self):
        report_upload = self._create_discharge_summary_report()
        document_request = self._encounter_request(report_upload)

        first = get_or_create_document_link(self._patient_actor(), self.patient, document_request, provider="whatsapp")
        second = get_or_create_document_link(self._patient_actor(), self.patient, document_request, provider="whatsapp")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DocumentLink.objects.filter(object_external_id=report_upload.external_id).count(), 1)

    def test_expired_existing_link_is_not_reused(self):
        report = self._create_diagnostic_report()
        DocumentLink.objects.create(
            object_kind=DocumentLinkObjectKind.DIAGNOSTIC_REPORT,
            object_external_id=report.external_id,
            document_type="diagnostic_report",
            patient_external_id=self.patient.external_id,
            provider="whatsapp",
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        link = get_or_create_document_link(
            self._patient_actor(), self.patient, self._lab_report_request(report), provider="whatsapp"
        )

        self.assertEqual(DocumentLink.objects.filter(object_external_id=report.external_id).count(), 2)
        self.assertTrue(link.is_valid())

    def _link(self, document_type="discharge_summary", object_kind=DocumentLinkObjectKind.REPORT_UPLOAD):
        return DocumentLink.objects.create(
            object_kind=object_kind,
            object_external_id=uuid.uuid4(),
            document_type=document_type,
            patient_external_id=self.patient.external_id,
            provider="whatsapp",
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def test_document_url_is_the_care_fe_page_for_every_kind(self):
        """One address for every document type: the page decides what to do with the
        token, so a provider template needs no per-type base url."""
        rendered = self._link(
            document_type="diagnostic_report",
            object_kind=DocumentLinkObjectKind.DIAGNOSTIC_REPORT,
        )
        stored = self._link()

        with patch(
            "care_im_wrapper.documents.service.plugin_settings.DOCUMENT_PAGE_BASE_URL",
            "https://care.example.org",
        ):
            self.assertEqual(
                build_document_url(rendered),
                f"https://care.example.org/public/documents/{rendered.token}",
            )
            self.assertEqual(
                build_document_url(stored),
                f"https://care.example.org/public/documents/{stored.token}",
            )

    def test_document_url_falls_back_to_current_domain(self):
        """An unset base must never yield a bare path -- the link goes straight out over a
        messaging provider, where a relative url is unusable."""
        link = self._link()

        with (
            patch("care_im_wrapper.documents.service.plugin_settings.DOCUMENT_PAGE_BASE_URL", ""),
            self.settings(CURRENT_DOMAIN="care.example.org"),
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"https://care.example.org/public/documents/{link.token}")

    def test_document_url_keeps_an_explicit_scheme(self):
        link = self._link()

        with patch(
            "care_im_wrapper.documents.service.plugin_settings.DOCUMENT_PAGE_BASE_URL",
            "http://localhost:4000/",
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"http://localhost:4000/public/documents/{link.token}")

    def test_system_document_link_does_not_require_actor(self):
        report = self._create_diagnostic_report()

        link = get_system_document_link(self.patient, self._lab_report_request(report), provider="whatsapp")

        self.assertEqual(link.object_kind, DocumentLinkObjectKind.DIAGNOSTIC_REPORT)
        self.assertEqual(link.object_external_id, report.external_id)
