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
        # An event with no resolvable facility (resolve_event_orgs(event) == []) is
        # visible to superusers only, not any facility staff role — this falls out of
        # check_permission_in_facility_organization's own organization_id__in=[]
        # filter, not special-cased here.
        return self.check_permission_in_facility_organization(
            [NotificationPermissions.can_read_notification_event.name], user, orgs=resolve_event_orgs(event)
        )

    def can_create_notification_event(self, user, event: NotificationEvent | None = None, *, facility=None) -> bool:
        if facility is not None:
            return self.check_permission_in_facility_organization(
                [NotificationPermissions.can_create_notification_event.name], user, facility=facility
            )
        if event is None:
            msg = "can_create_notification_event requires either event or facility"
            raise ValueError(msg)
        return self.check_permission_in_facility_organization(
            [NotificationPermissions.can_create_notification_event.name], user, orgs=resolve_event_orgs(event)
        )

    def can_dispatch_notification_event(self, user, event: NotificationEvent) -> bool:
        return self.check_permission_in_facility_organization(
            [NotificationPermissions.can_dispatch_notification_event.name], user, orgs=resolve_event_orgs(event)
        )


AuthorizationController.register_override_controller(NotificationAccess)
