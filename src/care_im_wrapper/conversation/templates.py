from __future__ import annotations

from typing import Any

_MESSAGES: dict[str, str] = {
    "not_found": "Sorry, we couldn't find an account linked to your number.",
    "yob_prompt": "Please reply with your year of birth (e.g. 1990).",
    "yob_invalid": "Please enter a valid 4-digit year (e.g. 1990).",
    "yob_wrong": "That doesn't match. You have *{remaining}* attempt(s) remaining.",
    "cooldown": "Your account is locked. Please try again in {minutes} minutes.",
    "select_account": "Multiple accounts found. Please select one by replying with its number:",
    "invalid_choice": "Please reply with a valid number from the list.",
    "choose_option": "Please choose an option:",
    "logout_confirm": "You have been logged out. Send any message to start again.",
    "session_expired": "Your session has expired. Please send any message to re-authenticate.",
    "permission_denied": "You don't have permission to view this information.",
    "no_data": "No {label} found on record.",
    "fetch_error": "Could not retrieve that information. Please try again.",
    "patient_search_prompt": "Enter the patient's phone number or name to search.",
    "patient_search_results": "Search results. Reply with the number to select:",
    "no_patients_found": "No patients found matching that search.",
    "patient_selected": "Viewing records for *{name}*. What would you like to see?",
    "greeting": "Hello, *{name}*! How can I help you today?",
    # Data fetcher headers
    "encounters_header": "Your recent encounters:",
    "medications_header": "Your recent medications:",
    "procedures_header": "Your recent procedures:",
    "appointments_header": "Your recent appointments:",
    "lab_reports_header": "Your recent lab reports:",
    "summary_header": "Patient Summary",
    "rate_limit_exceeded": "Too many messages. Please wait a moment before trying again.",
    # Per-record line templates
    "medication_line": "*{name}* ({status})",
    "medication_dosage": "Dosage: _{dosage}_",
    "encounter_line": "{date} — {facility} ({status})",
    "appointment_line": "*{practitioner}* at *{location}*",
    "appointment_detail": "{date} — {time_slot} ({status})",
    "lab_report_line": "*{name}* — {date} ({status})",
    "procedure_line": "*{name}* — {date} ({status})",
    "summary_name": "*Name:* {value}",
    "summary_dob": "*Date of Birth:* {value}",
    "summary_blood_group": "*Blood Group:* {value}",
    "summary_gender": "*Gender:* {value}",
    "summary_phone": "*Phone:* {value}",
    "summary_not_recorded": "Not recorded",
}


def _msg(key: str, **kwargs: Any) -> str:
    """Formats a template from the predefined messages dictionary."""
    template = _MESSAGES[key]

    if kwargs:
        template = template.format(**kwargs)
    return template
