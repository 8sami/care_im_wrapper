import enum

from care.security.permissions.constants import Permission, PermissionContext  # pyright: ignore[reportMissingImports]
from care.security.roles.role import (  # pyright: ignore[reportMissingImports]
    ADMIN_ROLE,
    ADMINISTRATOR,
    DOCTOR_ROLE,
    FACILITY_ADMIN_ROLE,
    NURSE_ROLE,
    STAFF_ROLE,
)


class NotificationPermissions(enum.Enum):
    can_read_notification_template = Permission(
        "Can view notification templates",
        "",
        PermissionContext.GENERIC,
        [ADMIN_ROLE, STAFF_ROLE, FACILITY_ADMIN_ROLE, DOCTOR_ROLE, NURSE_ROLE, ADMINISTRATOR],
    )
    can_manage_notification_template = Permission(
        "Can enable/disable notification templates",
        "",
        PermissionContext.GENERIC,
        [ADMIN_ROLE, FACILITY_ADMIN_ROLE, ADMINISTRATOR],
    )
    can_read_notification_event = Permission(
        "Can view notification events and their recipients/status",
        "",
        PermissionContext.FACILITY,
        [ADMIN_ROLE, STAFF_ROLE, FACILITY_ADMIN_ROLE, DOCTOR_ROLE, NURSE_ROLE, ADMINISTRATOR],
    )
    can_create_notification_event = Permission(
        "Can manually create a notification event for a manual-type trigger",
        "",
        PermissionContext.FACILITY,
        [ADMIN_ROLE, STAFF_ROLE, FACILITY_ADMIN_ROLE, ADMINISTRATOR],
    )
    can_dispatch_notification_event = Permission(
        "Can manually trigger dispatch of pending recipients for an event",
        "",
        PermissionContext.FACILITY,
        [ADMIN_ROLE, FACILITY_ADMIN_ROLE, ADMINISTRATOR],
    )
