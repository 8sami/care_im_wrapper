from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "care_im_wrapper"


class CareImWrapperConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("Care IM Wrapper")

    def ready(self) -> None:
        # ready() must import handlers or @receiver decorators never register
        import care_im_wrapper.handlers.booking  # noqa: F401
        import care_im_wrapper.handlers.meta  # noqa: F401
