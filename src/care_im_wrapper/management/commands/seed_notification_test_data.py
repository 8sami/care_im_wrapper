"""Seeds the fixtures the frontend end-to-end suite needs, and creates events for it.

Two things cannot come from anywhere else:

* **Templates.** Real templates arrive from the provider through ``sync_notification_templates``,
  which needs Meta credentials. CI has none, so the suite seeds its own -- one active and one
  inactive, because the templates screen must be shown to offer only active ones.
* **Dispatchable events.** Events are only ever created by signal handlers, each of which needs
  its own domain object (a booking, an invoice) to fire. ``--create-event`` makes one directly
  so the dispatch tests have something pending without staging that whole domain setup.

Seeding is idempotent: re-running updates the same rows rather than duplicating them.
"""

from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]
from care.facility.models.facility import Facility  # pyright: ignore[reportMissingImports]
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from care_im_wrapper.messaging.registry import resolve_channel
from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationEvent,
    NotificationRecipient,
    NotificationTemplate,
    NotificationTrigger,
    TemplateApprovalStatus,
    TemplateParameterFormat,
    TriggerType,
)

TRIGGER_SLUG = "e2e_seeded_notification"
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
    """Progress messages go to stderr so stdout carries only the created event's id."""

    help = (
        "Seeds the trigger and the two templates the frontend Playwright suite needs. "
        "Idempotent. --create-event adds one event for the dispatch tests and prints its id; "
        "--reset deletes every event created against the seeded trigger."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete every event created against the seeded trigger first, for a repeatable list.",
        )
        parser.add_argument(
            "--create-event",
            metavar="TITLE",
            help="Create one event with this title and print its external id.",
        )
        parser.add_argument(
            "--facility",
            metavar="EXTERNAL_ID",
            help="Facility the created event belongs to. Required with --create-event.",
        )
        parser.add_argument(
            "--no-recipient",
            action="store_true",
            help="Create the event with no recipients, for the 'nothing pending' states.",
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
                "name": "E2E Seeded Notification",
                "description": "Trigger the frontend end-to-end suite files its events under.",
                "trigger_type": TriggerType.SIGNAL,
                "is_active": True,
                "template_slug": active_template.slug,
                # No context_slug: these events carry no related object to build a context
                # from, and a non-empty value is validated against the registry on save.
                "context_slug": "",
            },
        )
        self._report("trigger", TRIGGER_SLUG, created)

        if options["reset"]:
            deleted, _ = NotificationEvent.objects.filter(trigger=trigger).delete()
            self.stderr.write(f"deleted {deleted} row(s) for trigger '{TRIGGER_SLUG}'")

        if options["create_event"]:
            self._create_event(
                trigger=trigger,
                template=active_template,
                title=options["create_event"],
                facility_external_id=options["facility"],
                with_recipient=not options["no_recipient"],
            )

    def _create_event(
        self,
        *,
        trigger: NotificationTrigger,
        template: NotificationTemplate,
        title: str,
        facility_external_id: str | None,
        with_recipient: bool,
    ) -> None:
        if not facility_external_id:
            raise CommandError("--facility is required with --create-event.")
        facility = Facility.objects.filter(external_id=facility_external_id).first()
        if facility is None:
            raise CommandError(f"No facility with external_id '{facility_external_id}'.")

        # No related object, so NotificationEvent.save() keeps the facility assigned here --
        # without it the event is invisible in the facility-scoped list the tests read.
        event = NotificationEvent.objects.create(
            trigger=trigger,
            template=template,
            title=title,
            facility_id=facility.id,
        )

        if with_recipient:
            patient = Patient.objects.exclude(phone_number="").first()
            if patient is None:
                raise CommandError("No patient with a phone number to notify; load fixtures first.")
            NotificationRecipient.objects.create(
                event=event,
                recipient_content_type=ContentType.objects.get_for_model(Patient),
                recipient_object_id=patient.id,
                phone_number=patient.phone_number,
                provider=resolve_channel(patient.phone_number),
            )

        # Bare id on its own line: the Playwright helper reads it off stdout.
        self.stdout.write(str(event.external_id))

    def _report(self, kind: str, slug: str, created: bool) -> None:
        verb = "created" if created else "updated"
        self.stderr.write(f"{verb} {kind} '{slug}'")
