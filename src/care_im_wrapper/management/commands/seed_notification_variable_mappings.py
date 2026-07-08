from django.core.management.base import BaseCommand

from care_im_wrapper.models.notification import NotificationTemplate
from care_im_wrapper.settings import plugin_settings
from care_im_wrapper.tasks import sync_notification_templates


class Command(BaseCommand):
    help = (
        "Syncs templates from Meta, then applies plugin_settings.NOTIFICATION_TEMPLATE_VARIABLE_MAPPINGS "
        "to them. Safe to re-run: idempotent, and only touches templates present in that setting."
    )

    def handle(self, *args, **options) -> None:
        sync_notification_templates()

        mappings = plugin_settings.NOTIFICATION_TEMPLATE_VARIABLE_MAPPINGS
        for slug, variable_mapping in mappings.items():
            updated = NotificationTemplate.objects.filter(slug=slug).update(variable_mapping=variable_mapping)
            if updated:
                self.stdout.write(self.style.SUCCESS(f"variable_mapping set for '{slug}'"))
            else:
                self.stdout.write(
                    self.style.WARNING(f"No NotificationTemplate with slug='{slug}' (not yet approved by Meta?)")
                )
