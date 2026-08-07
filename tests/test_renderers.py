from django.test import SimpleTestCase

from care_im_wrapper.conversation.renderers import (
    _TRUNCATION_MARKER,
    numbered_block,
    render_appointments,
    render_lab_reports,
    render_prescriptions,
    render_procedures,
    render_summary,
)
from care_im_wrapper.data.records import (
    AppointmentRecord,
    DosageLine,
    LabReportRecord,
    MedicationRecord,
    PatientSummary,
    PrescriptionRecord,
    ProcedureRecord,
)


def _line(dosage="1 tablets", frequency="1-0-1 (Twice a day)", duration="5 days", sig="Via Oral route", additional=()):
    return DosageLine(
        dosage=dosage,
        frequency=frequency,
        additional_instructions=tuple(additional),
        duration=duration,
        sig=sig,
        is_non_unit_dose=False,
    )


def _medication(name="Amoxicillin", status="Active", lines=None, note=None, is_inactive=False):
    return MedicationRecord(
        name=name,
        status=status,
        is_inactive=is_inactive,
        lines=(_line(),) if lines is None else tuple(lines),
        note=note,
    )


def _prescription(name="Discharge medications", medications=None, **kwargs):
    defaults = dict(
        status="Active",
        prescribed_by="Dr. Ada Lovelace",
        prescribed_on="30 Jul 2026",
        facility="City Care Hospital",
        note=None,
    )
    defaults.update(kwargs)
    return PrescriptionRecord(
        name=name,
        medications=(_medication(),) if medications is None else tuple(medications),
        **defaults,
    )


class TruncateTests(SimpleTestCase):
    def test_block_within_budget_is_not_marked(self):
        block = numbered_block("Header", ["short text"], 4096)

        self.assertNotIn(_TRUNCATION_MARKER, block)

    def test_block_over_budget_is_marked_and_fits(self):
        block = numbered_block("Header", ["A" * 5000], 4096)

        self.assertLessEqual(len(block), 4096)
        self.assertTrue(block.endswith(_TRUNCATION_MARKER))


class RenderPrescriptionsTests(SimpleTestCase):
    """Two levels: a prescription, then the medications on it. Mirrors care's own read."""

    def test_prescription_header_carries_prescriber_and_facility(self):
        result = render_prescriptions([_prescription()], 4096)

        self.assertIn("*Discharge medications* (_Active_)", result.text)
        self.assertIn("Prescribed on: _30 Jul 2026_", result.text)
        self.assertIn("Prescribed by: _Dr. Ada Lovelace_", result.text)
        self.assertIn("Facility: _City Care Hospital_", result.text)
        self.assertIsNone(result.interactive)

    def test_untitled_prescription_still_has_a_label(self):
        result = render_prescriptions([_prescription(name=None)], 4096)

        self.assertIn("*Prescription* (_Active_)", result.text)

    def test_missing_prescriber_is_marked_not_recorded_rather_than_omitted(self):
        """care_fe prints "-" for an absent value; silence would read as if there were."""
        result = render_prescriptions([_prescription(prescribed_by=None)], 4096)

        self.assertIn("Prescribed by: _-_", result.text)

    def test_medication_renders_all_four_care_fe_columns(self):
        result = render_prescriptions([_prescription()], 4096)

        self.assertIn("*Amoxicillin* (_Active_)", result.text)
        self.assertIn("Dosage: _1 tablets_", result.text)
        self.assertIn("Frequency: _1-0-1 (Twice a day)_", result.text)
        self.assertIn("Duration: _5 days_", result.text)
        self.assertIn("Instructions: _Via Oral route_", result.text)

    def test_missing_column_values_render_as_not_recorded(self):
        line = _line(dosage="", frequency="", duration="", sig="")
        result = render_prescriptions([_prescription(medications=[_medication(lines=[line])])], 4096)

        self.assertIn("Dosage: _-_", result.text)
        self.assertIn("Frequency: _-_", result.text)
        self.assertIn("Duration: _-_", result.text)
        self.assertIn("Instructions: _-_", result.text)

    def test_additional_instructions_are_listed_under_the_frequency(self):
        line = _line(additional=("Take with food", "Avoid alcohol"))
        result = render_prescriptions([_prescription(medications=[_medication(lines=[line])])], 4096)

        self.assertIn("Take with food", result.text)
        self.assertIn("Avoid alcohol", result.text)

    def test_a_tapered_course_numbers_each_step(self):
        """The safety case: without per-step blocks a reader cannot tell which dose lasts."""
        lines = [_line(dosage="2 tablets", duration="3 days"), _line(dosage="1 tablets", duration="4 days")]
        result = render_prescriptions([_prescription(medications=[_medication(lines=lines)])], 4096)

        self.assertIn("Step 1:", result.text)
        self.assertIn("Step 2:", result.text)
        step1 = result.text.index("Step 1:")
        step2 = result.text.index("Step 2:")
        self.assertIn("2 tablets", result.text[step1:step2])
        self.assertIn("3 days", result.text[step1:step2])
        self.assertIn("1 tablets", result.text[step2:])
        self.assertIn("4 days", result.text[step2:])

    def test_a_single_instruction_is_not_numbered(self):
        result = render_prescriptions([_prescription()], 4096)

        self.assertNotIn("Step 1:", result.text)

    def test_medication_without_dosage_instructions_says_so(self):
        result = render_prescriptions([_prescription(medications=[_medication(lines=[])])], 4096)

        self.assertIn("_No dosage instructions recorded._", result.text)

    def test_medication_note_is_shown(self):
        result = render_prescriptions([_prescription(medications=[_medication(note="After food")])], 4096)

        self.assertIn("After food", result.text)

    def test_prescription_note_is_shown(self):
        result = render_prescriptions([_prescription(note="Complete the full course")], 4096)

        self.assertIn("Complete the full course", result.text)

    def test_prescription_with_no_medications_says_so(self):
        result = render_prescriptions([_prescription(medications=[])], 4096)

        self.assertIn("_No medications on this prescription._", result.text)

    def test_multiple_prescriptions_are_numbered_sequentially(self):
        result = render_prescriptions([_prescription(name="First"), _prescription(name="Second")], 4096)

        self.assertIn("1.  *First*", result.text)
        self.assertIn("2.  *Second*", result.text)


class RenderAppointmentsTests(SimpleTestCase):
    def test_single_record_formats_subject_facility_and_detail_line(self):
        records = [
            AppointmentRecord(
                subject="Jane Doe",
                facility="Ward A",
                status="Booked",
                date="05 Mar 2024",
                time_slot="09:00 am - 09:30 am",
            )
        ]

        result = render_appointments(records, 4096)

        expected = (
            "Your recent appointments:\n\n"
            "1.  *Jane Doe* (_Booked_)\n"
            "       Facility: _Ward A_\n"
            "       Date: _05 Mar 2024_\n"
            "       Time: _09:00 am - 09:30 am_"
        )
        self.assertEqual(result.text, expected)


class RenderLabReportsTests(SimpleTestCase):
    def test_single_record_formats_name_date_and_status(self):
        records = [LabReportRecord(name="CBC", date="05 Mar 2024", status="Completed")]

        result = render_lab_reports(records, 4096)

        expected = "Your recent lab reports:\n\n1.  *CBC* (_Completed_)\n       Date: _05 Mar 2024_"
        self.assertEqual(result.text, expected)


class RenderProceduresTests(SimpleTestCase):
    def test_single_record_formats_name_date_and_status(self):
        records = [ProcedureRecord(name="Blood Test", date="05 Mar 2024", status="Completed")]

        result = render_procedures(records, 4096)

        expected = "Your recent procedures:\n\n1.  *Blood Test* (_Completed_)\n       Date: _05 Mar 2024_"
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
            "Name: _Jane Doe_\n"
            "Date of Birth: _15 Jun 1990_\n"
            "Blood Group: _A Positive_\n"
            "Gender: _Female_\n"
            "Phone: _+919876543210_"
        )
        self.assertEqual(result.text, expected)

    def test_none_fields_fall_back_to_not_recorded(self):
        summary = PatientSummary(name=None, date_of_birth=None, blood_group=None, gender=None, phone=None)

        result = render_summary(summary, 4096)

        expected = (
            "Patient Summary\n\n"
            "Name: _Not recorded_\n"
            "Date of Birth: _Not recorded_\n"
            "Blood Group: _Not recorded_\n"
            "Gender: _Not recorded_\n"
            "Phone: _Not recorded_"
        )
        self.assertEqual(result.text, expected)
