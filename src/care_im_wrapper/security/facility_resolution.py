from care_im_wrapper.models import NotificationEvent


def resolve_event_facility(event: NotificationEvent) -> int | None:
    """The facility an event is scoped to, or None when it could not be resolved.

    Callers must treat None as "no facility context" themselves -- see
    NotificationAccess._check_in_event_facility, which makes it superuser-only.
    """
    return event.facility_id  # pyright: ignore[reportReturnType]
