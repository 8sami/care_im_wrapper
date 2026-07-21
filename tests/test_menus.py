from django.test import SimpleTestCase

from care_im_wrapper.conversation import menus, renderers
from care_im_wrapper.data import (
    appointments,
    encounters,
    lab_reports,
    medications,
    patient_summary,
    procedures,
)
from care_im_wrapper.documents import resolvers as document_resolvers


class PatientMenuTests(SimpleTestCase):
    def test_patient_menu_has_exactly_six_entries(self):
        self.assertEqual(set(menus._PATIENT_MENU.keys()), {"1", "2", "3", "4", "5", "6"})

    def test_entry_1_is_encounter_details_wired_to_encounters_module(self):
        label, fetcher, renderer, document_resolver = menus._PATIENT_MENU["1"]
        self.assertEqual(label, "Encounter details")
        self.assertIs(fetcher, encounters.fetch_encounters)
        self.assertIs(renderer, renderers.render_encounters)
        self.assertIs(document_resolver, document_resolvers.resolve_encounter_document)

    def test_entry_2_is_current_medications_wired_to_medications_module(self):
        label, fetcher, renderer, document_resolver = menus._PATIENT_MENU["2"]
        self.assertEqual(label, "Current medications")
        self.assertIs(fetcher, medications.fetch_medications)
        self.assertIs(renderer, renderers.render_medications)
        self.assertIsNone(document_resolver)

    def test_entry_3_is_procedures_wired_to_procedures_module(self):
        label, fetcher, renderer, document_resolver = menus._PATIENT_MENU["3"]
        self.assertEqual(label, "Procedures")
        self.assertIs(fetcher, procedures.fetch_procedures)
        self.assertIs(renderer, renderers.render_procedures)
        self.assertIsNone(document_resolver)

    def test_entry_4_is_appointments_wired_to_appointments_module(self):
        label, fetcher, renderer, document_resolver = menus._PATIENT_MENU["4"]
        self.assertEqual(label, "Appointments")
        self.assertIs(fetcher, appointments.fetch_appointments)
        self.assertIs(renderer, renderers.render_appointments)
        self.assertIsNone(document_resolver)

    def test_entry_5_is_lab_reports_wired_to_lab_reports_module(self):
        label, fetcher, renderer, document_resolver = menus._PATIENT_MENU["5"]
        self.assertEqual(label, "Lab reports")
        self.assertIs(fetcher, lab_reports.fetch_lab_reports)
        self.assertIs(renderer, renderers.render_lab_reports)
        self.assertIs(document_resolver, document_resolvers.resolve_diagnostic_report_document)

    def test_entry_6_is_patient_summary_wired_to_patient_summary_module(self):
        label, fetcher, renderer, document_resolver = menus._PATIENT_MENU["6"]
        self.assertEqual(label, "Patient summary")
        self.assertIs(fetcher, patient_summary.fetch_summary)
        self.assertIs(renderer, renderers.render_summary)
        self.assertIsNone(document_resolver)


class StaffMenuTests(SimpleTestCase):
    def test_staff_menu_has_exactly_seven_entries(self):
        self.assertEqual(set(menus._STAFF_MENU.keys()), {"1", "2", "3", "4", "5", "6", "7"})

    def test_staff_menu_contains_all_patient_menu_entries_unchanged(self):
        for key in menus._PATIENT_MENU:
            self.assertEqual(menus._STAFF_MENU[key], menus._PATIENT_MENU[key])

    def test_entry_7_is_patient_lookup_with_no_fetcher_or_renderer(self):
        label, fetcher, renderer, document_resolver = menus._STAFF_MENU["7"]
        self.assertEqual(label, "Patient lookup")
        self.assertIsNone(fetcher)
        self.assertIsNone(renderer)
        self.assertIsNone(document_resolver)
