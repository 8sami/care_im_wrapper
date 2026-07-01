from care_im_wrapper.conversation import renderers
from care_im_wrapper.data import (
    appointments,
    encounters,
    lab_reports,
    medications,
    patient_summary,
    procedures,
)

# Each entry: (display_label, fetcher_fn, renderer_fn)
# renderer_fn is None for entries that don't fetch data (e.g. patient lookup).
_PATIENT_MENU = {
    "1": ("Encounter details", encounters.fetch_encounters, renderers.render_encounters),
    "2": ("Current medications", medications.fetch_medications, renderers.render_medications),
    "3": ("Procedures", procedures.fetch_procedures, renderers.render_procedures),
    "4": ("Appointments", appointments.fetch_appointments, renderers.render_appointments),
    "5": ("Lab reports", lab_reports.fetch_lab_reports, renderers.render_lab_reports),
    "6": ("Patient summary", patient_summary.fetch_summary, renderers.render_summary),
}

_STAFF_MENU = {
    **_PATIENT_MENU,
    "7": ("Patient lookup", None, None),
}
