from care_im_wrapper.models import NotificationEvent


def resolve_event_orgs(event: NotificationEvent) -> list[int]:
    return event.facility_organization_cache  # pyright: ignore[reportReturnType]
