"""Fetch appointment details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import (
    ENTERED_IN_ERROR_STATUS,
    cached_fetch,
    describe_resource,
    humanize_choice,
    humanize_date,
    humanize_time,
)
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.pagination import Page, map_page, paginate_or_raise
from care_im_wrapper.data.records import AppointmentRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_appointments(actor: Actor, session: ConversationSession) -> Page:
    """patient: returns one page of their own appointments, newest first."""
    from care.emr.models.scheduling.booking import TokenBooking  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = (
        TokenBooking.objects.filter(patient=patient)
        .exclude(status=ENTERED_IN_ERROR_STATUS)
        .select_related(
            "token_slot__resource__user",
            "token_slot__resource__facility",
            "token_slot__resource__location",
            "token_slot__resource__healthcare_service",
        )
    )
    page = paginate_or_raise(queryset.order_by("-booked_on"), session)

    mapped = map_page(page, _extract_booking_info)
    if not mapped.records and mapped.number == 0:
        raise NoDataError

    return mapped


def _extract_booking_info(booking) -> AppointmentRecord | None:
    slot = getattr(booking, "token_slot", None)
    status = humanize_choice(getattr(booking, "status", None))
    resource = getattr(slot, "resource", None) if slot else None

    subject = describe_resource(resource)
    facility = "Unknown"

    if resource:
        facility_obj = getattr(resource, "facility", None)
        if facility_obj:
            facility = getattr(facility_obj, "name", None) or "Unknown"

    date_str = ""
    time_slot = ""
    if slot and hasattr(slot, "start_datetime"):
        date_str = humanize_date(slot.start_datetime)
        time_slot = f"{humanize_time(slot.start_datetime)} - {humanize_time(slot.end_datetime)}"

    if not date_str or not time_slot:
        return None

    return AppointmentRecord(
        subject=subject,
        facility=facility,
        status=status,
        date=date_str,
        time_slot=time_slot,
    )
