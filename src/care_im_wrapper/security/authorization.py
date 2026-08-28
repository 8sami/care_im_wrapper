from care.security.authorization.base import (  # pyright: ignore[reportMissingImports]
    AuthorizationController,
    AuthorizationHandler,
)

from care_im_wrapper.models import NotificationEvent, NotificationTemplate
from care_im_wrapper.security.facility_resolution import resolve_event_facility
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

    def _check_in_event_facility(self, permission: str, user, event: NotificationEvent) -> bool:
        """Whether `user` holds `permission` anywhere in the facility `event` belongs to.

        An event belongs to a facility, not to a department inside it, so this scopes by
        facility the way core's own facility-wide handlers do (see
        care/security/authorization/invoice.py). Scoping by the root organization instead
        admits only users with a membership row on that one org, locking out the
        department-level staff these permissions are granted to.

        An unresolvable facility is superuser-only. That has to be decided here: passing
        facility=None to check_permission_in_facility_organization drops the filter
        entirely, which would grant the event to anyone holding the permission in *any*
        facility.
        """
        facility_id = resolve_event_facility(event)
        if facility_id is None:
            return bool(user.is_superuser)
        return self.check_permission_in_facility_organization([permission], user, facility=facility_id)

    def can_read_notification_event(self, user, event: NotificationEvent) -> bool:
        return self._check_in_event_facility(NotificationPermissions.can_read_notification_event.name, user, event)

    def can_dispatch_notification_event(self, user, event: NotificationEvent) -> bool:
        return self._check_in_event_facility(NotificationPermissions.can_dispatch_notification_event.name, user, event)


def register_notification_authorization() -> None:
    AuthorizationController.register_override_controller(NotificationAccess)
