from types import SimpleNamespace

from care.utils.tests.base import CareAPITestBase
from django.core.cache import cache
from django.test import SimpleTestCase

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.lab_reports import _extract_report_name, fetch_lab_reports
from care_im_wrapper.models import ConversationSession


class ExtractReportNameTests(SimpleTestCase):
    def test_code_dict_with_text_returns_text(self):
        report = SimpleNamespace(code={"text": "Complete Blood Count"})
        self.assertEqual(_extract_report_name(report), "Complete Blood Count")

    def test_code_dict_with_display_only_returns_display(self):
        report = SimpleNamespace(code={"display": "Liver Function Test"})
        self.assertEqual(_extract_report_name(report), "Liver Function Test")

    def test_code_dict_prefers_text_over_display(self):
        # NOTE: unlike procedures._extract_service_name, this prefers "text" first,
        # then "display" — the exact opposite order. Do not assume they match.
        report = SimpleNamespace(code={"text": "Text Name", "display": "Display Name"})
        self.assertEqual(_extract_report_name(report), "Text Name")

    def test_code_dict_without_text_or_display_returns_lab_report(self):
        report = SimpleNamespace(code={"system": "http://loinc.org"})
        self.assertEqual(_extract_report_name(report), "Lab report")

    def test_code_as_plain_string_returns_string(self):
        report = SimpleNamespace(code="HbA1c")
        self.assertEqual(_extract_report_name(report), "HbA1c")

    def test_code_none_returns_lab_report(self):
        report = SimpleNamespace(code=None)
        self.assertEqual(_extract_report_name(report), "Lab report")

    def test_missing_code_attribute_returns_lab_report(self):
        report = SimpleNamespace()
        self.assertEqual(_extract_report_name(report), "Lab report")


class FetchLabReportsTests(CareAPITestBase):
    """All statuses are listed, but only finalised reports carry an external_id, so only they
    become selectable rows in the document pick-list."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.create_user()
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )

    def _actor_session(self, encounter=None):
        actor = Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)
        # Lab reports are encounter-scoped, as care_fe's diagnostic_reports tab is.
        target = self.encounter if encounter is None else encounter
        session = SimpleNamespace(
            active_patient_external_id=None,
            active_encounter_external_id=str(target.external_id),
            active_prescription_external_id="",
        )
        return actor, session

    def _create_report(self, status):
        from care.emr.models.diagnostic_report import DiagnosticReport

        service_request = self.create_service_request(
            patient=self.patient, facility=self.facility, encounter=self.encounter
        )
        return DiagnosticReport.objects.create(
            patient=self.patient, encounter=self.encounter, service_request=service_request, status=status
        )

    def test_final_report_is_selectable(self):
        report = self._create_report("final")
        actor, session = self._actor_session()

        records = fetch_lab_reports(actor, session)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].external_id, str(report.external_id))

    def test_non_final_report_is_listed_but_not_selectable(self):
        self._create_report("preliminary")
        actor, session = self._actor_session()

        records = fetch_lab_reports(actor, session)

        self.assertEqual(len(records), 1)  # still shown in the list
        self.assertEqual(records[0].external_id, "")  # but has no selectable id

    def test_mixed_statuses_list_all_but_only_final_is_selectable(self):
        self._create_report("final")
        self._create_report("preliminary")
        actor, session = self._actor_session()

        records = fetch_lab_reports(actor, session)

        self.assertEqual(len(records), 2)
        self.assertEqual(len([r for r in records if r.external_id]), 1)

    def test_another_encounters_reports_are_not_returned(self):
        other_encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )
        self._create_report("final")
        other_service_request = self.create_service_request(
            patient=self.patient, facility=self.facility, encounter=other_encounter
        )
        from care.emr.models.diagnostic_report import DiagnosticReport

        DiagnosticReport.objects.create(
            patient=self.patient,
            encounter=other_encounter,
            service_request=other_service_request,
            status="final",
        )
        actor, session = self._actor_session()

        records = fetch_lab_reports(actor, session)

        self.assertEqual(len(records), 1)
