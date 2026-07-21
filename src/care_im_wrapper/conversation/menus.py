from care_im_wrapper.conversation import renderers
from care_im_wrapper.data import (
    appointments,
    encounters,
    lab_reports,
    medications,
    patient_summary,
    procedures,
)
from care_im_wrapper.documents import resolvers as document_resolvers

# Each entry: (display_label, fetcher_fn, renderer_fn, document_resolver_fn), where fetcher_fn
# and renderer_fn are None for entries that don't fetch data, and document_resolver_fn is set
# only where the item's records map to distinct documents.
_PATIENT_MENU = {
    "1": (
        "Encounter details",
        encounters.fetch_encounters,
        renderers.render_encounters,
        document_resolvers.resolve_encounter_document,
    ),
    "2": ("Current medications", medications.fetch_medications, renderers.render_medications, None),
    "3": ("Procedures", procedures.fetch_procedures, renderers.render_procedures, None),
    "4": ("Appointments", appointments.fetch_appointments, renderers.render_appointments, None),
    "5": (
        "Lab reports",
        lab_reports.fetch_lab_reports,
        renderers.render_lab_reports,
        document_resolvers.resolve_diagnostic_report_document,
    ),
    "6": ("Patient summary", patient_summary.fetch_summary, renderers.render_summary, None),
}

_STAFF_MENU = {
    **_PATIENT_MENU,
    "7": ("Patient lookup", None, None, None),
}
