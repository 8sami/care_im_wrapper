from django.test import SimpleTestCase

from care_im_wrapper.conversation.renderers import (
    _truncate,
    render_appointments,
    render_encounters,
    render_lab_reports,
    render_medications,
    render_patient_search_results,
    render_procedures,
    render_summary,
)
from care_im_wrapper.data.records import (
    AppointmentRecord,
    EncounterRecord,
    LabReportRecord,
    MedicationRecord,
    PatientSummary,
    ProcedureRecord,
)


class TruncateTests(SimpleTestCase):
    def test_short_text_returned_unchanged(self):
        self.assertEqual(_truncate("short text", 4096), "short text")

    def test_text_at_exact_limit_returned_unchanged(self):
        text = "A" * 4096
        self.assertEqual(_truncate(text, 4096), text)

    def test_text_over_limit_is_truncated_with_suffix(self):
        text = "A" * 5000
        expected = "A" * (4096 - 20) + "\n... (truncated)"
        self.assertEqual(_truncate(text, 4096), expected)


class RenderMedicationsTests(SimpleTestCase):
    def test_single_record_without_dosage_or_note(self):
        records = [MedicationRecord(name="Paracetamol", status="Active")]

        result = render_medications(records, 4096)

        expected = "Your recent medications:\n\n1. *Paracetamol* (Active)"
        self.assertEqual(result.text, expected)
        self.assertIsNone(result.interactive)

    def test_record_with_dosage_and_note_appends_both_lines(self):
        records = [MedicationRecord(name="Med2", status="Stopped", dosage="1 tab daily", note="After food")]

        result = render_medications(records, 4096)

        expected = "Your recent medications:\n\n1. *Med2* (Stopped)\n   Dosage: _1 tab daily_\n   Note: After food"
        self.assertEqual(result.text, expected)

    def test_multiple_records_are_numbered_sequentially(self):
        records = [
            MedicationRecord(name="Med A", status="Active"),
            MedicationRecord(name="Med B", status="Active"),
        ]

        result = render_medications(records, 4096)

        expected = "Your recent medications:\n\n1. *Med A* (Active)\n2. *Med B* (Active)"
        self.assertEqual(result.text, expected)


class RenderEncountersTests(SimpleTestCase):
    def test_single_record_formats_facility_date_and_status(self):
        records = [
            EncounterRecord(
                date="05 Mar 2024", facility="City Hospital", status="In Progress", encounter_class="Inpatient"
            )
        ]

        result = render_encounters(records, 4096)

        expected = "Your recent encounters:\n\n1. *City Hospital* — 05 Mar 2024 (In Progress)"
        self.assertEqual(result.text, expected)


class RenderAppointmentsTests(SimpleTestCase):
    def test_single_record_formats_practitioner_location_and_detail_line(self):
        records = [
            AppointmentRecord(
                practitioner="Jane Doe",
                location="Ward A",
                status="Booked",
                date="05 Mar 2024",
                time_slot="09:00 am - 09:30 am",
            )
        ]

        result = render_appointments(records, 4096)

        expected = (
            "Your recent appointments:\n\n1. *Jane Doe* at *Ward A*\n   05 Mar 2024 — 09:00 am - 09:30 am (Booked)"
        )
        self.assertEqual(result.text, expected)


class RenderLabReportsTests(SimpleTestCase):
    def test_single_record_formats_name_date_and_status(self):
        records = [LabReportRecord(name="CBC", date="05 Mar 2024", status="Completed")]

        result = render_lab_reports(records, 4096)

        expected = "Your recent lab reports:\n\n1. *CBC* — 05 Mar 2024 (Completed)"
        self.assertEqual(result.text, expected)


class RenderProceduresTests(SimpleTestCase):
    def test_single_record_formats_name_date_and_status(self):
        records = [ProcedureRecord(name="Blood Test", date="05 Mar 2024", status="Completed")]

        result = render_procedures(records, 4096)

        expected = "Your recent procedures:\n\n1. *Blood Test* — 05 Mar 2024 (Completed)"
        self.assertEqual(result.text, expected)


class RenderSummaryTests(SimpleTestCase):
    def test_fully_populated_summary(self):
        summary = PatientSummary(
            name="Jane Doe",
            date_of_birth="15 Jun 1990",
            blood_group="A Positive",
            gender="Female",
            phone="+919876543210",
        )

        result = render_summary(summary, 4096)

        expected = (
            "Patient Summary\n\n"
            "*Name:* Jane Doe\n"
            "*Date of Birth:* 15 Jun 1990\n"
            "*Blood Group:* A Positive\n"
            "*Gender:* Female\n"
            "*Phone:* +919876543210"
        )
        self.assertEqual(result.text, expected)

    def test_none_fields_fall_back_to_not_recorded(self):
        summary = PatientSummary(name=None, date_of_birth=None, blood_group=None, gender=None, phone=None)

        result = render_summary(summary, 4096)

        expected = (
            "Patient Summary\n\n"
            "*Name:* Not recorded\n"
            "*Date of Birth:* Not recorded\n"
            "*Blood Group:* Not recorded\n"
            "*Gender:* Not recorded\n"
            "*Phone:* Not recorded"
        )
        self.assertEqual(result.text, expected)


class RenderPatientSearchResultsTests(SimpleTestCase):
    def test_numbers_each_result_line(self):
        result = render_patient_search_results(
            "Search results. Reply with the number to select:",
            ["Jane Doe (+919876543210)", "John Roe (+911111111111)"],
            4096,
        )

        expected = (
            "Search results. Reply with the number to select:\n\n"
            "1. Jane Doe (+919876543210)\n"
            "2. John Roe (+911111111111)"
        )
        self.assertEqual(result.text, expected)

    def test_empty_results_list_returns_only_prompt(self):
        result = render_patient_search_results("Search results:", [], 4096)

        self.assertEqual(result.text, "Search results:\n")
