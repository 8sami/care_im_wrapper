from datetime import timedelta
from unittest.mock import patch

from care.utils.tests.base import CareAPITestBase
from django.test import Client
from django.utils import timezone

from care_im_wrapper.models import DocumentLink, DocumentLinkObjectKind


class PublicDocumentViewTests(CareAPITestBase):
    """The endpoint the patient-facing page reads. There is no user here by design: the
    token is the capability, so these tests are mostly about what a token does *not* open."""

    def setUp(self):
        super().setUp()
        self.client = Client()
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

    def _create_report(self, **kwargs):
        from care.emr.models.diagnostic_report import DiagnosticReport

        data = {
            "patient": self.patient,
            "encounter": self.encounter,
            "facility": self.facility,
            "service_request": self.service_request,
            "status": "final",
            "conclusion": "Within normal limits.",
        }
        data.update(kwargs)
        return DiagnosticReport.objects.create(**data)

    def _create_link(self, report, **kwargs):
        data = {
            "object_kind": DocumentLinkObjectKind.DIAGNOSTIC_REPORT,
            "object_external_id": report.external_id,
            "document_type": "diagnostic_report",
            "patient_external_id": self.patient.external_id,
            "provider": "whatsapp",
            "expires_at": timezone.now() + timedelta(hours=1),
        }
        data.update(kwargs)
        return DocumentLink.objects.create(**data)

    def _get(self, token):
        return self.client.get(f"/api/care_im_wrapper/public/documents/{token}/")

    def test_valid_token_returns_the_report_without_any_authentication(self):
        report = self._create_report()
        link = self._create_link(report)

        response = self._get(link.token)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["kind"], "diagnostic_report")
        self.assertEqual(body["mode"], "render")
        # care_fe resolves the facility's letterhead by this slug.
        self.assertEqual(body["template_slug"], "diagnostic_report")
        self.assertEqual(body["report"]["id"], str(report.external_id))
        self.assertEqual(body["report"]["conclusion"], "Within normal limits.")

    def test_payload_carries_the_facility_print_template_config(self):
        """The whole point of the page: the patient sees the facility's configured
        letterhead, resolved from live config rather than baked in at send time."""
        self.facility.print_templates = [{"slug": "diagnostic_report", "watermark": {"enabled": True}}]
        self.facility.save(update_fields=["print_templates"])
        link = self._create_link(self._create_report())

        body = self._get(link.token).json()

        self.assertEqual(body["facility"]["name"], self.facility.name)
        self.assertEqual(body["facility"]["print_templates"][0]["slug"], "diagnostic_report")

    def test_facility_payload_exposes_only_letterhead_fields(self):
        """An anonymous link holder should learn nothing about the facility beyond what
        the printed page shows."""
        link = self._create_link(self._create_report())

        body = self._get(link.token).json()

        self.assertEqual(set(body["facility"].keys()), {"name", "address", "phone_number", "print_templates"})

    def test_report_without_its_own_facility_falls_back_to_the_encounters(self):
        """DiagnosticReport.facility is nullable, so the letterhead has to come from the
        encounter in that case -- and core's serializer must survive being handed None."""
        report = self._create_report(facility=None)
        link = self._create_link(report)

        response = self._get(link.token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["facility"]["name"], self.facility.name)

    def test_has_tag_condition_names_are_resolved_server_side(self):
        """care_fe resolves these through an authenticated tag_config call, which a patient
        cannot make. Both the observation's own definition and its components are walked."""
        from care.emr.models.tag_config import TagConfig

        tag = TagConfig.objects.create(
            status="active", display="Paediatric", category="patient", facility=self.facility
        )
        condition = {"metric": "patient_tag", "operation": "has_tag", "value": {"value": str(tag.external_id)}}
        report = self._create_report()
        link = self._create_link(report)

        serialized = {
            "observations": [
                {
                    "observation_definition": {
                        "qualified_ranges": [{"conditions": [dict(condition)]}],
                        "component": [{"qualified_ranges": [{"conditions": [dict(condition)]}]}],
                    }
                }
            ]
        }
        with patch(
            "care.emr.resources.diagnostic_report.spec.DiagnosticReportRetrieveSpec.serialize"
        ) as mock_serialize:
            mock_serialize.return_value.to_json.return_value = serialized
            body = self._get(link.token).json()

        definition = body["report"]["observations"][0]["observation_definition"]
        self.assertEqual(definition["qualified_ranges"][0]["conditions"][0]["tag_displays"], ["Paediatric"])
        # The component branch is a separate walk; a bug there is invisible otherwise.
        self.assertEqual(
            definition["component"][0]["qualified_ranges"][0]["conditions"][0]["tag_displays"],
            ["Paediatric"],
        )

    def test_every_attachment_is_returned_not_just_the_newest(self):
        from care.emr.models.file_upload import FileUpload

        report = self._create_report()
        for name in ("first", "second", "third"):
            FileUpload.objects.create(
                name=name,
                associating_id=str(report.external_id),
                file_type="diagnostic_report",
                file_category="unspecified",
                upload_completed=True,
            )
        link = self._create_link(report)

        with patch("care.emr.utils.file_manager.S3FilesManager.read_signed_url", return_value="https://s3.example/x"):
            body = self._get(link.token).json()

        self.assertEqual([f["name"] for f in body["files"]], ["first", "second", "third"])

    def test_archived_and_incomplete_attachments_are_excluded(self):
        from care.emr.models.file_upload import FileUpload

        report = self._create_report()
        FileUpload.objects.create(
            name="archived",
            associating_id=str(report.external_id),
            file_type="diagnostic_report",
            file_category="unspecified",
            upload_completed=True,
            is_archived=True,
        )
        FileUpload.objects.create(
            name="still-uploading",
            associating_id=str(report.external_id),
            file_type="diagnostic_report",
            file_category="unspecified",
            upload_completed=False,
        )
        link = self._create_link(report)

        body = self._get(link.token).json()

        self.assertEqual(body["files"], [])

    def test_unknown_token_is_not_distinguishable_from_an_expired_one(self):
        """Both 404 identically: a caller must not be able to learn that a token existed."""
        expired = self._create_link(self._create_report(), expires_at=timezone.now() - timedelta(seconds=1))

        self.assertEqual(self._get("does-not-exist").status_code, 404)
        self.assertEqual(self._get(expired.token).status_code, 404)

    def test_soft_deleted_link_is_rejected(self):
        link = self._create_link(self._create_report())
        link.deleted = True
        link.save(update_fields=["deleted"])

        self.assertEqual(self._get(link.token).status_code, 404)

    def test_link_to_a_deleted_report_404s_rather_than_erroring(self):
        report = self._create_report()
        link = self._create_link(report)
        report.delete()

        self.assertEqual(self._get(link.token).status_code, 404)

    def test_unregistered_document_type_404s(self):
        """A link minted for a kind nobody registered must not fall through to something else."""
        link = self._create_link(self._create_report(), document_type="not_a_real_kind")

        self.assertEqual(self._get(link.token).status_code, 404)

    def test_access_count_is_incremented_for_audit(self):
        link = self._create_link(self._create_report())

        self._get(link.token)
        self._get(link.token)

        link.refresh_from_db()
        self.assertEqual(link.access_count, 2)

    def test_rate_limited_token_is_refused(self):
        link = self._create_link(self._create_report())

        with patch("care_im_wrapper.documents.public_views.is_rate_limited", return_value=True):
            response = self._get(link.token)

        self.assertEqual(response.status_code, 429)
