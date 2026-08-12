"""Seeds the fixtures the frontend end-to-end suite needs.

Two of them cannot come from anywhere else:

* **A manual trigger.** Every trigger seeded by migration is ``signal``-typed, and
  ``NotificationEventViewSet.perform_create`` rejects anything else, so without this the
  create-notification screen has an empty trigger dropdown and a disabled submit button.
* **Templates.** Real templates arrive from the provider through ``sync_notification_templates``,
  which needs Meta credentials. CI has none, so the suite seeds its own -- one active and one
  inactive, because the create screen must be shown to offer only active ones.

Idempotent: re-running updates the same rows rather than duplicating them.
"""

from django.core.management.base import BaseCommand

from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationEvent,
    NotificationTemplate,
    NotificationTrigger,
    TemplateApprovalStatus,
    TemplateParameterFormat,
    TriggerType,
)

TRIGGER_SLUG = "e2e_manual_notification"
ACTIVE_TEMPLATE_SLUG = "e2e_active_template"
INACTIVE_TEMPLATE_SLUG = "e2e_inactive_template"

# Mirrors the shape sync_notification_templates writes for a NAMED template, so the
# variable-mapping screen parses this the same way it parses a real synced one.
ACTIVE_TEMPLATE_PAYLOAD = {
    "name": ACTIVE_TEMPLATE_SLUG,
    "language": "en",
    "category": "UTILITY",
    "parameter_format": "NAMED",
    "components": [
        {
            "type": "BODY",
            "text": "Hello {{patient_name}},\n\nThis is a test notification sent on {{date}}.",
            "example": {
                "body_text_named_params": [
                    {"param_name": "patient_name", "example": "Yachana Desai"},
                    {"param_name": "date", "example": "9 August 2025"},
                ]
            },
        }
    ],
}

INACTIVE_TEMPLATE_PAYLOAD = {
    "name": INACTIVE_TEMPLATE_SLUG,
    "language": "en",
    "category": "UTILITY",
    "parameter_format": "POSITIONAL",
    "components": [
        {
            "type": "BODY",
            "text": "Inactive test template body {{1}}.",
            "example": {"body_text": [["placeholder"]]},
        }
    ],
}


class Command(BaseCommand):
    help = (
        "Seeds the manual trigger and the two templates the frontend Playwright suite needs. "
        "Idempotent. Pass --reset to also delete events created against the seeded trigger."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete every event created against the seeded trigger first, for a repeatable list.",
        )

    def handle(self, *args, **options) -> None:
        active_template, created = NotificationTemplate.objects.update_or_create(
            slug=ACTIVE_TEMPLATE_SLUG,
            defaults={
                "name": "E2E Active Template",
                "category": NotificationCategory.UTILITY,
                "approval_status": TemplateApprovalStatus.ACTIVE,
                "is_active": True,
                "language_code": "en",
                "payload": ACTIVE_TEMPLATE_PAYLOAD,
                "parameter_format": TemplateParameterFormat.NAMED,
                # Left unmapped on purpose: the variable-mapping screen is under test, and a
                # template that already maps every variable gives it nothing to do.
                "variable_mapping": None,
            },
        )
        self._report("template", ACTIVE_TEMPLATE_SLUG, created)

        _, created = NotificationTemplate.objects.update_or_create(
            slug=INACTIVE_TEMPLATE_SLUG,
            defaults={
                "name": "E2E Inactive Template",
                "category": NotificationCategory.UTILITY,
                "approval_status": TemplateApprovalStatus.ACTIVE,
                "is_active": False,
                "language_code": "en",
                "payload": INACTIVE_TEMPLATE_PAYLOAD,
                "parameter_format": TemplateParameterFormat.POSITIONAL,
                "variable_mapping": None,
            },
        )
        self._report("template", INACTIVE_TEMPLATE_SLUG, created)

        trigger, created = NotificationTrigger.objects.update_or_create(
            slug=TRIGGER_SLUG,
            defaults={
                "name": "E2E Manual Notification",
                "description": "Manual trigger used by the frontend end-to-end suite.",
                "trigger_type": TriggerType.MANUAL,
                "is_active": True,
                "template_slug": active_template.slug,
                # No context_slug: a manual event has no related object to build a context from,
                # and a non-empty value is validated against NOTIFICATION_CONTEXT_REGISTRY on save.
                "context_slug": "",
            },
        )
        self._report("trigger", TRIGGER_SLUG, created)

        if options["reset"]:
            deleted, _ = NotificationEvent.objects.filter(trigger=trigger).delete()
            self.stdout.write(self.style.WARNING(f"deleted {deleted} row(s) for trigger '{TRIGGER_SLUG}'"))  # pyright: ignore[reportAttributeAccessIssue]

    def _report(self, kind: str, slug: str, created: bool) -> None:
        verb = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {kind} '{slug}'"))  # pyright: ignore[reportAttributeAccessIssue]
