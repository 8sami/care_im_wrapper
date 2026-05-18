from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "care_im_wrapper"


class Care_im_wrapperConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("Care_im_wrapper")

    def ready(self):
        import care_im_wrapper.signals  # noqa F401
