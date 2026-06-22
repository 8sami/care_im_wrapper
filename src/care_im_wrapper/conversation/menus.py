from care_im_wrapper.data import (
    appointments,
    encounters,
    lab_reports,
    medications,
    patient_summary,
    procedures,
)

_PATIENT_MENU = {
    "1": ("Encounter details", encounters.fetch_encounters),
    "2": ("Current medications", medications.fetch_medications),
    "3": ("Procedures", procedures.fetch_procedures),
    "4": ("Appointments", appointments.fetch_appointments),
    "5": ("Lab reports", lab_reports.fetch_lab_reports),
    "6": ("Patient summary", patient_summary.fetch_summary),
}

_STAFF_MENU = {
    **_PATIENT_MENU,
    "7": ("Patient lookup", None),
}
