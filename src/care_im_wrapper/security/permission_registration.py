from care.security.permissions.base import PermissionController  # pyright: ignore[reportMissingImports]

from care_im_wrapper.security.permissions import NotificationPermissions


def register_notification_permissions() -> None:
    PermissionController.register_permission_handler(NotificationPermissions)
