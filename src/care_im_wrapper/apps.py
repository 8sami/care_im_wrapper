from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "care_im_wrapper"


class CareImWrapperConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("Care IM Wrapper")

    def ready(self) -> None:
        # ready() must import handlers or @receiver decorators never register
        from config.celery_app import app  # pyright: ignore[reportMissingImports]

        import care_im_wrapper.handlers.booking  # noqa: F401
        import care_im_wrapper.handlers.meta  # noqa: F401
        from care_im_wrapper.settings import plugin_settings
        from care_im_wrapper.tasks import dispatch_pending_notification_recipients

        @app.on_after_finalize.connect
        def _register_periodic_tasks(sender, **kwargs):  # noqa: ANN001, ANN003, ANN202
            sender.add_periodic_task(
                plugin_settings.NOTIFICATION_DISPATCH_INTERVAL_SECONDS,
                dispatch_pending_notification_recipients.s(),  # pyright: ignore[reportFunctionMemberAccess]
                name="care_im_wrapper: dispatch pending notification recipients",
            )
