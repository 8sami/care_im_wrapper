"""The two menus the conversation navigates, mirroring care_fe's data hierarchy.

care_fe splits clinical data across a patient level (PatientHome's tabs: encounters,
appointments) and an encounter level (EncounterShow's tabs: medicines, service_requests,
diagnostic_reports). The main menu here is the first, the encounter sub-menu the second.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from care_im_wrapper.conversation import renderers
from care_im_wrapper.data import (
    appointments,
    lab_reports,
    medications,
    patient_summary,
    procedures,
)
from care_im_wrapper.documents import resolvers as document_resolvers
from care_im_wrapper.models import ConversationSession


class Scope(Enum):
    """What a menu option's fetcher needs resolved before it can run."""

    PATIENT = "patient"
    ENCOUNTER = "encounter"
    PRESCRIPTION = "prescription"


class Action(Enum):
    """A menu option that navigates instead of fetching a list."""

    PATIENT_SEARCH = "patient_search"
    OPEN_ENCOUNTER = "open_encounter"
    ENCOUNTER_DOCUMENT = "encounter_document"


ENCOUNTERS_LABEL = "Encounters"


@dataclass(frozen=True)
class MenuOption:
    """One row of a menu. `fetcher` None means `action` carries the behaviour instead.

    `description` is the second line the provider shows under the row title, so the reader
    knows what an option holds before tapping it.
    """

    label: str
    description: str = ""
    fetcher: Callable[..., Any] | None = None
    renderer: Callable[..., Any] | None = None
    document_resolver: Callable[..., Any] | None = None
    scope: Scope = Scope.PATIENT
    action: Action | None = None


_MAIN_MENU: dict[str, MenuOption] = {
    "1": MenuOption(
        label=ENCOUNTERS_LABEL,
        description="A visit's medications, procedures & reports",
        action=Action.OPEN_ENCOUNTER,
    ),
    "2": MenuOption(
        label="Appointments",
        description="Your upcoming & past bookings",
        fetcher=appointments.fetch_appointments,
        renderer=renderers.render_appointments,
    ),
    "3": MenuOption(
        label="Patient summary",
        description="Name, date of birth, blood group & phone",
        fetcher=patient_summary.fetch_summary,
        renderer=renderers.render_summary,
    ),
}

_STAFF_MAIN_MENU: dict[str, MenuOption] = {
    **_MAIN_MENU,
    "4": MenuOption(
        label="Patient lookup",
        description="Find a patient by name or phone number",
        action=Action.PATIENT_SEARCH,
    ),
}

_ENCOUNTER_MENU: dict[str, MenuOption] = {
    "1": MenuOption(
        label="Medications",
        description="Prescriptions & dosage for this visit",
        fetcher=medications.fetch_prescriptions,
        renderer=renderers.render_prescriptions,
        scope=Scope.PRESCRIPTION,
    ),
    "2": MenuOption(
        label="Procedures",
        description="Procedures ordered during this visit",
        fetcher=procedures.fetch_procedures,
        renderer=renderers.render_procedures,
        scope=Scope.ENCOUNTER,
    ),
    "3": MenuOption(
        label="Lab reports",
        description="Diagnostic reports, with PDFs to download",
        fetcher=lab_reports.fetch_lab_reports,
        renderer=renderers.render_lab_reports,
        document_resolver=document_resolvers.resolve_diagnostic_report_document,
        scope=Scope.ENCOUNTER,
    ),
    "4": MenuOption(
        label="Discharge summary",
        description="Download this visit's discharge summary",
        document_resolver=document_resolvers.resolve_encounter_document,
        scope=Scope.ENCOUNTER,
        action=Action.ENCOUNTER_DOCUMENT,
    ),
    "5": MenuOption(
        label="Change encounter",
        description="Switch to a different visit",
        action=Action.OPEN_ENCOUNTER,
    ),
}


def menu_for(session: ConversationSession) -> dict[str, MenuOption]:
    """The menu the session should be shown: user type x menu context, and -- inside an
    encounter -- whether there are other encounters to switch to.

    With a single encounter, "Change encounter" would only reopen the one already open, so
    it is dropped rather than shown as a dead end.
    """
    if session.menu_context == ConversationSession.MenuContext.ENCOUNTER:
        if getattr(session, "active_encounter_has_alternatives", False):
            return _ENCOUNTER_MENU
        return {key: option for key, option in _ENCOUNTER_MENU.items() if option.action is not Action.OPEN_ENCOUNTER}
    if session.user_type == ConversationSession.UserType.STAFF.value:
        return _STAFF_MAIN_MENU
    return _MAIN_MENU
