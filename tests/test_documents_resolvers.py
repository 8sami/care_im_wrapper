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

    def test_encounter_document_resolves_to_a_discharge_summary_request(self):
        result = resolve_encounter_document(self.patient, str(self.encounter.external_id))

        self.assertIsNotNone(result)
        self.assertEqual(result.encounter, self.encounter)
        self.assertEqual(result.report_type, "discharge_summary")
        self.assertEqual(result.document_type, "discharge_summary")
        self.assertIsNone(result.diagnostic_report)

    def test_encounter_document_unknown_id_returns_none(self):
        result = resolve_encounter_document(self.patient, "00000000-0000-0000-0000-000000000000")
        self.assertIsNone(result)

    def test_another_patients_encounter_is_not_reachable_by_id(self):
        other_patient = self.create_patient()

        result = resolve_encounter_document(other_patient, str(self.encounter.external_id))

        self.assertIsNone(result)
