from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.conversation import menus, renderers
from care_im_wrapper.conversation.menus import Action, Scope, menu_for
from care_im_wrapper.data import (
    appointments,
    lab_reports,
    medications,
    patient_summary,
    procedures,
)
from care_im_wrapper.documents import resolvers as document_resolvers
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


def _session(user_type="patient", menu_context=ConversationSession.MenuContext.MAIN, has_alternatives=True):
    return SimpleNamespace(
        user_type=user_type,
        menu_context=menu_context,
        active_encounter_has_alternatives=has_alternatives,
    )


class MainMenuTests(SimpleTestCase):
    """The main menu is PatientHome's tabs plus the info card -- nothing encounter-scoped."""

    def test_main_menu_has_exactly_three_entries(self):
        self.assertEqual(set(menus._MAIN_MENU.keys()), {"1", "2", "3"})

    def test_entry_1_opens_the_encounter_picker(self):
        option = menus._MAIN_MENU["1"]
        self.assertEqual(option.label, "Encounters")
        self.assertIs(option.action, Action.OPEN_ENCOUNTER)

    def test_entry_2_is_appointments_wired_to_appointments_module(self):
        option = menus._MAIN_MENU["2"]
        self.assertEqual(option.label, "Appointments")
        self.assertIs(option.fetcher, appointments.fetch_appointments)
        self.assertIs(option.renderer, renderers.render_appointments)

    def test_entry_3_is_patient_summary_wired_to_patient_summary_module(self):
        option = menus._MAIN_MENU["3"]
        self.assertEqual(option.label, "Patient summary")
        self.assertIs(option.fetcher, patient_summary.fetch_summary)
        self.assertIs(option.renderer, renderers.render_summary)

    def test_appointments_stay_patient_scoped(self):
        """TokenBooking does carry a nullable `associated_encounter`, but care_fe lists
        appointments on PatientHome, not on an encounter tab -- so this menu does too."""
        self.assertIs(menus._MAIN_MENU["2"].scope, Scope.PATIENT)

    def test_no_main_menu_option_needs_an_encounter(self):
        for key, option in menus._MAIN_MENU.items():
            self.assertIs(option.scope, Scope.PATIENT, msg=key)


class StaffMainMenuTests(SimpleTestCase):
    def test_staff_main_menu_has_exactly_four_entries(self):
        self.assertEqual(set(menus._STAFF_MAIN_MENU.keys()), {"1", "2", "3", "4"})

    def test_staff_main_menu_contains_all_patient_entries_unchanged(self):
        for key in menus._MAIN_MENU:
            self.assertEqual(menus._STAFF_MAIN_MENU[key], menus._MAIN_MENU[key])

    def test_entry_4_is_patient_lookup_with_no_fetcher(self):
        option = menus._STAFF_MAIN_MENU["4"]
        self.assertEqual(option.label, "Patient lookup")
        self.assertIsNone(option.fetcher)
        self.assertIs(option.action, Action.PATIENT_SEARCH)


class EncounterMenuTests(SimpleTestCase):
    """The sub-menu is EncounterShow's tabs, one to one."""

    def test_encounter_menu_has_exactly_five_entries(self):
        self.assertEqual(set(menus._ENCOUNTER_MENU.keys()), {"1", "2", "3", "4", "5"})

    def test_entry_1_is_medications_scoped_to_a_prescription(self):
        option = menus._ENCOUNTER_MENU["1"]
        self.assertEqual(option.label, "Medications")
        self.assertIs(option.fetcher, medications.fetch_prescriptions)
        self.assertIs(option.renderer, renderers.render_prescriptions)
        self.assertIs(option.scope, Scope.PRESCRIPTION)

    def test_entry_2_is_procedures_scoped_to_the_encounter(self):
        option = menus._ENCOUNTER_MENU["2"]
        self.assertEqual(option.label, "Procedures")
        self.assertIs(option.fetcher, procedures.fetch_procedures)
        self.assertIs(option.renderer, renderers.render_procedures)
        self.assertIs(option.scope, Scope.ENCOUNTER)

    def test_entry_3_is_lab_reports_with_its_document_resolver(self):
        option = menus._ENCOUNTER_MENU["3"]
        self.assertEqual(option.label, "Lab reports")
        self.assertIs(option.fetcher, lab_reports.fetch_lab_reports)
        self.assertIs(option.renderer, renderers.render_lab_reports)
        self.assertIs(option.document_resolver, document_resolvers.resolve_diagnostic_report_document)
        self.assertIs(option.scope, Scope.ENCOUNTER)

    def test_entry_4_is_the_discharge_summary_resolved_without_a_pick_list(self):
        """What the retired "Encounter details" option uniquely provided."""
        option = menus._ENCOUNTER_MENU["4"]
        self.assertEqual(option.label, "Discharge summary")
        self.assertIs(option.document_resolver, document_resolvers.resolve_encounter_document)
        self.assertIs(option.action, Action.ENCOUNTER_DOCUMENT)
        self.assertIsNone(option.fetcher)

    def test_entry_5_changes_encounter_with_no_gap_before_it(self):
        option = menus._ENCOUNTER_MENU["5"]
        self.assertEqual(option.label, "Change encounter")
        self.assertIs(option.action, Action.OPEN_ENCOUNTER)

    def test_every_fetching_option_needs_the_encounter(self):
        for key, option in menus._ENCOUNTER_MENU.items():
            if option.fetcher is not None:
                self.assertIn(option.scope, (Scope.ENCOUNTER, Scope.PRESCRIPTION), msg=key)


class MenuDescriptionTests(SimpleTestCase):
    """Every option carries a one-line description of what it holds, so the reader can tell
    the options apart before tapping. Provider clamps to 72 chars, so stay under it."""

    def test_every_option_across_both_menus_has_a_description(self):
        for menu in (menus._STAFF_MAIN_MENU, menus._ENCOUNTER_MENU):
            for key, option in menu.items():
                self.assertTrue(option.description, msg=f"{key}: {option.label}")

    def test_descriptions_fit_the_provider_row_limit(self):
        limit = int(plugin_settings.WHATSAPP_DESCRIPTION_TRUNCATE)
        for menu in (menus._STAFF_MAIN_MENU, menus._ENCOUNTER_MENU):
            for option in menu.values():
                self.assertLessEqual(len(option.description), limit, msg=option.label)


class MenuForTests(SimpleTestCase):
    def test_patient_in_main_context_gets_the_patient_main_menu(self):
        self.assertIs(menu_for(_session()), menus._MAIN_MENU)

    def test_staff_in_main_context_gets_the_staff_main_menu(self):
        self.assertIs(menu_for(_session(user_type="staff")), menus._STAFF_MAIN_MENU)

    def test_encounter_context_gets_the_sub_menu_regardless_of_user_type(self):
        for user_type in ("patient", "staff"):
            session = _session(user_type=user_type, menu_context=ConversationSession.MenuContext.ENCOUNTER)
            self.assertIs(menu_for(session), menus._ENCOUNTER_MENU, msg=user_type)

    def test_a_single_encounter_drops_change_encounter_from_the_sub_menu(self):
        session = _session(menu_context=ConversationSession.MenuContext.ENCOUNTER, has_alternatives=False)

        menu = menu_for(session)

        self.assertNotIn("Change encounter", [option.label for option in menu.values()])
        # The rest of the sub-menu is unchanged.
        self.assertEqual(list(menu.keys()), ["1", "2", "3", "4"])

    def test_alternatives_restore_change_encounter(self):
        session = _session(menu_context=ConversationSession.MenuContext.ENCOUNTER, has_alternatives=True)

        self.assertIn("Change encounter", [o.label for o in menu_for(session).values()])


class MenuRowBudgetTests(SimpleTestCase):
    """Both menus plus their trailing 0 row must fit a provider's interactive list."""

    def setUp(self):
        self.cap = int(plugin_settings.DEFAULT_MAX_INTERACTIVE_ROWS)

    def test_staff_main_menu_and_logout_fit_the_row_cap(self):
        self.assertLessEqual(len(menus._STAFF_MAIN_MENU) + 1, self.cap)

    def test_encounter_menu_and_back_fit_the_row_cap(self):
        self.assertLessEqual(len(menus._ENCOUNTER_MENU) + 1, self.cap)
