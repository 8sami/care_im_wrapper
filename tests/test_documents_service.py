import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from care.utils.tests.base import CareAPITestBase
from django.utils import timezone

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.exceptions import PermissionDeniedError
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.documents.service import (
    DISCHARGE_SUMMARY_REPORT_TYPE,
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
            "slug": f"encounter-base-{uuid.uuid4().hex[:8]}",
            "name": "Encounter Base",
            "status": "active",
            "template_data": "<html></html>",
            "template_type": "encounter_report",
            "default_format": "pdf",
            "context": "encounter_base",
            "facility": None,
        }
        data.update(kwargs)
        return Template.objects.create(**data)

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

    def _encounter_request(self):
        """The shape resolve_encounter_document builds: an encounter, generated as a discharge summary."""
        return DocumentRequest(
            document_type="discharge_summary",
            encounter=self.encounter,
            report_type=DISCHARGE_SUMMARY_REPORT_TYPE,
        )

    def _lab_report_request(self, report):
        return DocumentRequest(document_type="diagnostic_report", encounter=self.encounter, diagnostic_report=report)

    def _patient_actor(self, patient=None):
        return Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=patient or self.patient)

    def test_patient_actor_mismatched_patient_raises_permission_denied(self):
        other_patient = self.create_patient()
        document_request = DocumentRequest(document_type="patient_summary", encounter=self.encounter)

        with self.assertRaises(PermissionDeniedError):
            get_or_create_document_link(self._patient_actor(), other_patient, document_request, provider="whatsapp")

    def _staff_actor(self):
        return Actor(user_type=ConversationSession.UserType.STAFF.value, instance=self.create_user())

    def test_staff_generation_routes_through_cores_report_authorizer_and_denial_raises(self):
        """A staff report generation must be gated by core's report authorizer (the same
        check the HTTP endpoint enforces), not the coarser can_view_patient_obj."""
        from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

        actor = self._staff_actor()
        document_request = self._encounter_request()

        with patch(
            "care.emr.reports.authorizers.utils.write_report_authorizer",
            side_effect=DRFPermissionDenied("nope"),
        ) as mock_authz:
            with self.assertRaises(PermissionDeniedError):
                get_or_create_document_link(actor, self.patient, document_request, provider="whatsapp")

        mock_authz.assert_called_once_with(
            actor.instance, document_request.report_type, str(self.encounter.external_id)
        )

    def test_staff_generation_proceeds_when_the_report_authorizer_allows(self):
        self._create_template()
        actor = self._staff_actor()
        document_request = self._encounter_request()
        fake_report_upload = MagicMock(external_id=uuid.uuid4())

        with (
            patch("care.emr.reports.authorizers.utils.write_report_authorizer") as mock_authz,
            patch(
                "care.emr.reports.report_utils.generate_and_upload_report", return_value=fake_report_upload
            ) as mock_generate,
        ):
            link = get_or_create_document_link(actor, self.patient, document_request, provider="whatsapp")

        mock_authz.assert_called_once()
        self.assertEqual(mock_generate.call_args.kwargs["report_type"], "discharge_summary")
        self.assertEqual(mock_generate.call_args.kwargs["associating_id"], str(self.encounter.external_id))
        self.assertEqual(link.object_kind, DocumentLinkObjectKind.REPORT_UPLOAD)
        self.assertEqual(link.object_external_id, fake_report_upload.external_id)

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
        # An encounter Template exists and would render, but a lab report must never fall
        # back to an encounter/discharge document (issue #21).
        self._create_template()
        report = self._create_diagnostic_report()

        with patch("care.emr.reports.report_utils.generate_and_upload_report") as mock_generate:
            link = get_or_create_document_link(
                self._patient_actor(), self.patient, self._lab_report_request(report), provider="whatsapp"
            )

        mock_generate.assert_not_called()
        self.assertEqual(link.object_kind, DocumentLinkObjectKind.DIAGNOSTIC_REPORT)

    def test_lab_report_does_not_depend_on_an_encounter_template(self):
        # No Template configured anywhere: the lab report still resolves (issue #16).
        report = self._create_diagnostic_report()

        link = get_or_create_document_link(
            self._patient_actor(), self.patient, self._lab_report_request(report), provider="whatsapp"
        )

        self.assertEqual(link.object_external_id, report.external_id)

    def test_no_active_template_raises_document_unavailable_error(self):
        document_request = DocumentRequest(document_type="patient_summary", encounter=self.encounter)

        with self.assertRaises(DocumentUnavailableError):
            get_or_create_document_link(self._patient_actor(), self.patient, document_request, provider="whatsapp")

    def test_existing_valid_link_is_reused_not_recreated(self):
        self._create_template()
        document_request = self._encounter_request()
        fake_report_upload = MagicMock(external_id=uuid.uuid4())

        with patch("care.emr.reports.report_utils.generate_and_upload_report", return_value=fake_report_upload):
            first = get_or_create_document_link(
                self._patient_actor(), self.patient, document_request, provider="whatsapp"
            )
            second = get_or_create_document_link(
                self._patient_actor(), self.patient, document_request, provider="whatsapp"
            )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DocumentLink.objects.filter(object_external_id=fake_report_upload.external_id).count(), 1)

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

    def test_build_document_url_uses_base_url_setting(self):
        link = self._link()

        with patch(
            "care_im_wrapper.documents.service.plugin_settings.DOCUMENT_LINK_BASE_URL", "https://care.example.org"
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"https://care.example.org/api/care_im_wrapper/documents/{link.token}/")

    def test_build_document_url_falls_back_to_backend_domain_when_unset(self):
        """An unset base URL must never yield a bare path -- the link goes straight out over
        a messaging provider, where a relative URL is unusable."""
        link = self._link()

        with (
            patch("care_im_wrapper.documents.service.plugin_settings.DOCUMENT_LINK_BASE_URL", ""),
            self.settings(BACKEND_DOMAIN="care.example.org"),
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"https://care.example.org/api/care_im_wrapper/documents/{link.token}/")

    def test_build_document_url_keeps_an_explicit_scheme(self):
        link = self._link()

        with patch(
            "care_im_wrapper.documents.service.plugin_settings.DOCUMENT_LINK_BASE_URL", "http://localhost:9000/"
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"http://localhost:9000/api/care_im_wrapper/documents/{link.token}/")

    def test_system_document_link_does_not_require_actor(self):
        report = self._create_diagnostic_report()

        link = get_system_document_link(self.patient, self._lab_report_request(report), provider="whatsapp")

        self.assertEqual(link.object_kind, DocumentLinkObjectKind.DIAGNOSTIC_REPORT)
        self.assertEqual(link.object_external_id, report.external_id)

    def test_rendered_document_url_points_at_the_care_fe_page(self):
        """A rendered document is drawn by care_fe, so its link must go to the frontend
        page, not the backend redirect (which has no file to redirect to)."""
        link = self._link(document_type="diagnostic_report", object_kind=DocumentLinkObjectKind.DIAGNOSTIC_REPORT)

        with patch(
            "care_im_wrapper.documents.service.plugin_settings.DOCUMENT_PAGE_BASE_URL", "https://care.example.org"
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"https://care.example.org/public/documents/{link.token}")

    def test_rendered_document_url_falls_back_to_current_domain(self):
        link = self._link(document_type="diagnostic_report", object_kind=DocumentLinkObjectKind.DIAGNOSTIC_REPORT)

        with (
            patch("care_im_wrapper.documents.service.plugin_settings.DOCUMENT_PAGE_BASE_URL", ""),
            self.settings(CURRENT_DOMAIN="care.example.org"),
        ):
            url = build_document_url(link)

        self.assertEqual(url, f"https://care.example.org/public/documents/{link.token}")
