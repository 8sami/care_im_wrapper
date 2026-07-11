from care.security.authorization.base import (  # pyright: ignore[reportMissingImports]
    AuthorizationController,
    AuthorizationHandler,
)

from care_im_wrapper.models import NotificationEvent, NotificationTemplate
from care_im_wrapper.security.facility_resolution import resolve_event_orgs
from care_im_wrapper.security.permissions import NotificationPermissions


class NotificationAccess(AuthorizationHandler):
    def can_read_notification_template(self, user, template: NotificationTemplate) -> bool:
        return self.check_permission_in_organization(
            [NotificationPermissions.can_read_notification_template.name], user
        )

    def can_manage_notification_template(self, user, template: NotificationTemplate) -> bool:
        return self.check_permission_in_organization(
            [NotificationPermissions.can_manage_notification_template.name], user
        )

    def can_read_notification_event(self, user, event: NotificationEvent) -> bool:
        # No resolvable facility > empty orgs > superuser-only, falls out of the check below naturally.
        return self.check_permission_in_facility_organization(
            [NotificationPermissions.can_read_notification_event.name], user, orgs=resolve_event_orgs(event)
        )

    def can_create_notification_event(self, user, event_or_facility_context: NotificationEvent | None = None) -> bool:
        # No context yet (facility_organization_cache is only computed in NotificationEvent.save()) > org-level check.
        if event_or_facility_context is None:
            return self.check_permission_in_organization(
                [NotificationPermissions.can_create_notification_event.name], user
            )
        return self.check_permission_in_facility_organization(
            [NotificationPermissions.can_create_notification_event.name],
            user,
            orgs=resolve_event_orgs(event_or_facility_context),
        )

    def can_dispatch_notification_event(self, user, event: NotificationEvent) -> bool:
        return self.check_permission_in_facility_organization(
            [NotificationPermissions.can_dispatch_notification_event.name], user, orgs=resolve_event_orgs(event)
        )


def register_notification_authorization() -> None:
    AuthorizationController.register_override_controller(NotificationAccess)
