from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.documents.resolvers import (
    resolve_diagnostic_report_document,
    resolve_encounter_document,
)


class DocumentResolversTests(CareAPITestBase):
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

    def _create_report(self, **kwargs):
        from care.emr.models.diagnostic_report import DiagnosticReport

        data = {
            "patient": self.patient,
            "encounter": self.encounter,
            "service_request": self.service_request,
            "status": "final",
        }
        data.update(kwargs)
        return DiagnosticReport.objects.create(**data)

    def test_unknown_external_id_returns_none(self):
        result = resolve_diagnostic_report_document(self.patient, "00000000-0000-0000-0000-000000000000")
        self.assertIsNone(result)

    def test_returns_document_request_referencing_the_report_and_its_encounter(self):
        report = self._create_report()

        result = resolve_diagnostic_report_document(self.patient, str(report.external_id))

        self.assertIsNotNone(result)
        self.assertEqual(result.document_type, "diagnostic_report")
        self.assertEqual(result.diagnostic_report, report)
        self.assertEqual(result.encounter, self.encounter)

    def test_resolves_the_selected_report_not_merely_the_latest(self):
        selected = self._create_report()
        self._create_report()  # newer, must be ignored

        result = resolve_diagnostic_report_document(self.patient, str(selected.external_id))

        self.assertEqual(result.diagnostic_report, selected)

    def test_another_patients_report_is_not_reachable_by_id(self):
        """The id comes back off the wire in session.candidates -- it must stay patient-scoped."""
        report = self._create_report()
        other_patient = self.create_patient()

        result = resolve_diagnostic_report_document(other_patient, str(report.external_id))

        self.assertIsNone(result)

    def _create_template(self, **kwargs):
        import uuid

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

    def test_encounter_document_resolves_to_the_generated_discharge_summary(self):
        report_upload = self._create_discharge_summary_report()

        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertIsNotNone(result)
        self.assertEqual(result.encounter, self.encounter)
        self.assertEqual(result.document_type, "discharge_summary")
        self.assertEqual(result.report_upload, report_upload)
        self.assertIsNone(result.diagnostic_report)

    def test_encounter_with_no_generated_discharge_summary_returns_none(self):
        """Issue #27: no staff-generated discharge summary must never trigger generation --
        the resolver must hand back nothing rather than a request that would render one."""
        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertIsNone(result)

    def test_encounter_document_resolves_to_the_latest_generated_report(self):
        self._create_discharge_summary_report(name="older")
        latest = self._create_discharge_summary_report(name="latest")

        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertEqual(result.report_upload, latest)

    def test_a_report_generated_long_ago_is_still_served(self):
        """No age cutoff: staff may have generated this days or weeks before the request."""
        from datetime import timedelta

        from django.utils import timezone

        old = self._create_discharge_summary_report()
        old.created_date = timezone.now() - timedelta(days=30)
        old.save(update_fields=["created_date"])

        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertEqual(result.report_upload, old)

    def test_archived_discharge_summary_report_is_ignored(self):
        self._create_discharge_summary_report(is_archived=True)

        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertIsNone(result)

    def test_incomplete_discharge_summary_report_is_ignored(self):
        self._create_discharge_summary_report(upload_completed=False)

        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertIsNone(result)

    def test_a_report_of_another_type_is_not_treated_as_a_discharge_summary(self):
        self._create_discharge_summary_report(report_type="encounter_report")

        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertIsNone(result)

    def test_encounter_document_unknown_id_returns_none(self):
        result = resolve_encounter_document(self.patient, "00000000-0000-0000-0000-000000000000")
        self.assertIsNone(result)

    def test_another_patients_encounter_is_not_reachable_by_id(self):
        other_patient = self.create_patient()
        self._create_discharge_summary_report()

        result = resolve_encounter_document(other_patient, str(self.encounter.external_id))

        self.assertIsNone(result)
