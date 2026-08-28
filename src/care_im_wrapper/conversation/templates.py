from __future__ import annotations

from typing import Any

# Formatting convention (WhatsApp markdown), applied uniformly across every message:
#   *bold*   -> the entity a line is about: a record's name/subject in its header
#               (`*{name}* (_{status}_)`), a name addressed in prose (`Hello, *{name}*`),
#               and a command the reader types (`Reply *a* for all`).
#   _italic_ -> a value being reported: the detail after a label (`Dosage: _{dosage}_`),
#               the status in a header, and standalone note phrases (`_No dosage..._`).
#   plain    -> chrome that names nothing: headers ("Your medications:"), menu labels,
#               titles, prompts, and status/help lines.
# Keep new messages in step with this; the renderers and pickers all read from here.
_MESSAGES: dict[str, str] = {
    "not_found": "Sorry, we couldn't find an account linked to your number.",
    "yob_prompt": "Please reply with your year of birth (e.g. 1990).",
    "yob_invalid": "Please enter a valid 4-digit year (e.g. 1990).",
    "yob_wrong": "That doesn't match. You have *{remaining}* attempt(s) remaining.",
    "cooldown": "Your account is locked. Please try again in {minutes} minutes.",
    "select_account": "Multiple accounts found. Please select one by replying with its number:",
    "invalid_choice": "Sorry, that wasn't one of the options. Please pick one from the list below.",
    "choose_option": "Please choose an option:",
    "logout_confirm": "You have been logged out. Send any message to start again.",
    "session_expired": "Your session has expired. Please send any message to re-authenticate.",
    "permission_denied": "You don't have permission to view this information.",
    "no_data": "No {label} found on record.",
    "fetch_error": "Could not retrieve that information. Please try again.",
    "patient_search_prompt": "Enter the patient's phone number or name to search.",
    "patient_search_results": "Search results. Reply with the number to select:",
    "no_patients_found": "No patients found matching that search.",
    "greeting": "Hello, *{name}*! How can I help you today?",
    # Scope line. Built from clauses because either half can be absent: a patient reading
    # their own records has no patient clause, the main menu has no encounter clause.
    # Bold falls on the subject -- the line titles the records under it, and what they are is
    # what the reader is scanning for. The encounter and patient qualify that, so they stay
    # plain rather than competing with it.
    "viewing": "Viewing *{subject}*",
    "viewing_encounter": "for encounter {encounter}",
    "viewing_patient": "for patient {patient}",
    "subject_records": "records",
    "select_encounter_prompt": "Which encounter would you like to open?",
    "select_encounter": "Select encounter",
    "encounters_title": "Encounters",
    "encounter_label": "{facility} — {date} ({encounter_class}, {status})",
    "encounter_menu_title": "Encounter",
    "back_to_main_menu": "Back to main menu",
    "back_to_main_menu_hint": "Leave this encounter",
    "logout_hint": "End this session",
    # Menu options. Here rather than in menus.py so every string the reader sees comes
    # from one table -- the menu is the surface they see most.
    "menu_encounters": "Encounters",
    "menu_encounters_hint": "A visit's medications, procedures & reports",
    "menu_appointments": "Appointments",
    "menu_appointments_hint": "Your upcoming & past bookings",
    "menu_patient_summary": "Patient summary",
    "menu_patient_summary_hint": "Name, date of birth, blood group & phone",
    "menu_patient_lookup": "Patient lookup",
    "menu_patient_lookup_hint": "Find a patient by name or phone number",
    "menu_medications": "Medications",
    "menu_medications_hint": "Prescriptions & dosage for this visit",
    "menu_procedures": "Procedures",
    "menu_procedures_hint": "Procedures ordered during this visit",
    "menu_lab_reports": "Lab reports",
    "menu_lab_reports_hint": "Diagnostic reports, with PDFs to download",
    "menu_discharge_summary": "Discharge summary",
    "menu_discharge_summary_hint": "Download this visit's discharge summary",
    "menu_change_encounter": "Change encounter",
    "menu_change_encounter_hint": "Switch to a different visit",
    "select_prescription_prompt": "Which prescription would you like to see? Reply *a* for all.",
    "select_prescription": "Select prescription",
    "prescriptions_title": "Prescriptions",
    # Plain, unlike the prescription block's italic version below: this one is a list-row
    # description, and providers show row text verbatim rather than rendering markdown.
    "prescription_choice_by": "Prescribed by: {prescribed_by}",
    "all_prescriptions": "All prescriptions",
    "view_all_medications": "View all medications",
    # Data fetcher headers
    "prescriptions_header": "Your medications:",
    "procedures_header": "Your recent procedures:",
    "appointments_header": "Your recent appointments:",
    "lab_reports_header": "Your recent lab reports:",
    "summary_header": "Patient Summary",
    # Menu / navigation chrome
    "logout": "Logout",
    "view_menu": "View Menu",
    "menu_title": "Menu",
    "select_patient": "Select Patient",
    "patients_title": "Patients",
    "select": "Select",
    "accounts_title": "Accounts",
    "account_line": "*{name}* (_{user_type}_)",
    # Document selection (pull path)
    "select_document_prompt": "Select from the list:",
    "select_document": "Select",
    "documents_title": "Reports",
    "view_document": "View document",
    "document_footer": "To pick another or go back, reopen the list above.",
    "document_text": "Your document is ready to view",
    "document_unavailable": "That document isn't available yet.",
    "back": "Back to menu",
    "page_indicator": "Page {page}",
    "page_hint_next": "Send *n* for the next page",
    "page_hint_prev": "Send *p* for the previous page",
    "next_page": "Next page",
    "prev_page": "Previous page",
    "page_last": "You're already on the last page.",
    "page_first": "You're already on the first page.",
    "page_nothing_open": "Pick something from the menu first, then use *n* and *p* to page through it.",
    "prescription_line": "*{name}* (_{status}_)",
    "prescription_date": "Prescribed on: _{date}_",
    "prescription_untitled": "Prescription",
    "medications_on_date": "*Prescribed {date}*",
    "prescription_prescribed_by": "Prescribed by: _{prescribed_by}_",
    "prescription_facility": "Facility: _{facility}_",
    "prescription_note": "Note: _{note}_",
    "prescription_no_medications": "_No medications on this prescription._",
    "medication_line": "*{name}* (_{status}_)",
    "medication_step": "Step {step}:",
    "medication_dosage": "Dosage: _{dosage}_",
    "medication_frequency": "Frequency: _{frequency}_",
    "medication_duration": "Duration: _{duration}_",
    "medication_instructions": "Instructions: _{instructions}_",
    "medication_note": "Note: _{note}_",
    "medication_no_dosage": "_No dosage instructions recorded._",
    "appointment_line": "*{subject}* (_{status}_)",
    "appointment_facility": "Facility: _{facility}_",
    "appointment_date": "Date: _{date}_",
    "appointment_time": "Time: _{time_slot}_",
    "lab_report_line": "*{name}* (_{status}_)",
    "lab_report_date": "Date: _{date}_",
    "procedure_line": "*{name}* (_{status}_)",
    "procedure_date": "Date: _{date}_",
    "summary_name": "Name: _{value}_",
    "summary_dob": "Date of Birth: _{value}_",
    "summary_blood_group": "Blood Group: _{value}_",
    "summary_gender": "Gender: _{value}_",
    "summary_phone": "Phone: _{value}_",
    "summary_not_recorded": "Not recorded",
}


def _msg(key: str, **kwargs: Any) -> str:
    """Formats a template from the predefined messages dictionary."""
    template = _MESSAGES[key]

    if kwargs:
        template = template.format(**kwargs)
    return template
