from datetime import timedelta

from care.emr.api.viewsets.base import (  # pyright: ignore[reportMissingImports]
    EMRBaseViewSet,
    EMRCreateMixin,
    EMRListMixin,
    EMRRetrieveMixin,
)
from care.emr.models.organization import (  # pyright: ignore[reportMissingImports]
    FacilityOrganization,
    FacilityOrganizationUser,
)
from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]
from care.facility.models.facility import Facility  # pyright: ignore[reportMissingImports]
from care.security.authorization.base import AuthorizationController  # pyright: ignore[reportMissingImports]
from care.users.models import User  # pyright: ignore[reportMissingImports]
from care.utils.shortcuts import get_object_or_404  # pyright: ignore[reportMissingImports]
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from care_im_wrapper.api.spec import (
    NotificationEventReadSpec,
    NotificationEventWriteSpec,
    NotificationRecipientReadSpec,
    NotificationTemplateReadSpec,
    NotificationTriggerReadSpec,
)
from care_im_wrapper.messaging.registry import resolve_channel
from care_im_wrapper.messaging.variables import resolve_variable
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationTemplate,
    NotificationTrigger,
    TriggerType,
)
from care_im_wrapper.reports.schema import (
    build_notification_schema,
    build_preview,
    resolve_template_context_slugs,
)
from care_im_wrapper.reports.validation import validate_variable_mapping
from care_im_wrapper.settings import plugin_settings
from care_im_wrapper.tasks import dispatch_notification_recipient, sync_notification_templates


def _authorized_facility_id(request, resource_label: str) -> int | None:
    """Shared facility scoping for the event/recipient list views: resolves the ``facility``
    query param to its id after checking the caller may read notification events there.
    Returns None when no facility is given and the caller is a superuser (unscoped);
    raises PermissionDenied otherwise. ``resource_label`` only tunes the error message.
    """
    facility_external_id = request.GET.get("facility")
    if not facility_external_id:
        # get_accessible_facility_organizations can't answer "every org this user can see"
        # without one given, so a non-superuser must scope explicitly.
        if not request.user.is_superuser:
            raise PermissionDenied("The facility query parameter is required.")
        return None

    facility = get_object_or_404(Facility, external_id=facility_external_id)
    # can_read_notification_event only inspects facility_id, so an unsaved probe works.
    probe_event = NotificationEvent(facility_id=facility.id)
    if not AuthorizationController.call("can_read_notification_event", request.user, probe_event):
        raise PermissionDenied(f"You do not have permission to view {resource_label} for this facility.")
    return facility.id


#: The facility scope every event/recipient list is read through. Documented here rather than
#: on each viewset because both resolve it via _authorized_facility_root_org_id.
_FACILITY_PARAM = OpenApiParameter(
    name="facility",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.QUERY,
    description=(
        "External id of the facility to scope results to. Required for every caller except a "
        "superuser, who may omit it to read across all facilities."
    ),
)


def _parse_bool_param(request, name: str) -> bool | None:
    """Parses a tri-state boolean query param: None when absent, else true/false. Rejects
    an unrecognised value rather than silently treating it as false."""
    raw = request.GET.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise ValidationError(f"{name} must be one of true, false, 1, 0.")


class NotificationTriggerViewSet(EMRListMixin, EMRRetrieveMixin, EMRBaseViewSet):
    database_model = NotificationTrigger
    pydantic_read_model = NotificationTriggerReadSpec

    def get_queryset(self):
        return NotificationTrigger.objects.filter(is_active=True).order_by("name")

    def authorize_retrieve(self, instance):
        # Triggers are global config like templates, not sensitive facility data — any authenticated user may read them.
        if not self.request.user.is_authenticated:
            raise PermissionDenied("Authentication required.")


class NotificationTemplateViewSet(EMRListMixin, EMRRetrieveMixin, EMRBaseViewSet):
    database_model = NotificationTemplate
    pydantic_read_model = NotificationTemplateReadSpec

    def get_queryset(self):
        queryset = NotificationTemplate.objects.all().order_by("name")
        # Only the list is gated here. EMRListMixin.list has no authorize hook, so a
        # queryset-level check is all that governs it -- but get_object() runs this too, and
        # gating every action would 403 the detail routes that carry their own manage check.
        if self.action != "list":
            return queryset
        if not AuthorizationController.call("can_read_notification_template", self.request.user, None):
            raise PermissionDenied("You do not have permission to view notification templates.")
        return queryset

    def authorize_retrieve(self, instance):
        if not AuthorizationController.call("can_read_notification_template", self.request.user, instance):
            raise PermissionDenied("You do not have permission to view this notification template.")

    @extend_schema(
        request=None,
        responses=NotificationTemplateReadSpec,
        description="Flips is_active on this template. Returns the updated template.",
    )
    @action(detail=True, methods=["post"])
    def toggle_active(self, request, *args, **kwargs):
        instance = self.get_object()
        if not AuthorizationController.call("can_manage_notification_template", request.user, instance):
            raise PermissionDenied("You do not have permission to manage this notification template.")
        instance.is_active = not instance.is_active
        instance.updated_by_id = request.user.id
        instance.save(update_fields=["is_active", "updated_by_id", "modified_date"])
        return Response(NotificationTemplateReadSpec.serialize(instance).to_json())

    @extend_schema(
        request=None,
        responses={202: OpenApiTypes.OBJECT},
        description="Queues a background pull of the provider's approved template catalogue.",
    )
    @action(detail=False, methods=["post"])
    def sync(self, request, *args, **kwargs):
        if not AuthorizationController.call("can_manage_notification_template", request.user, None):
            raise PermissionDenied("You do not have permission to sync notification templates.")
        sync_notification_templates.delay()  # pyright: ignore[reportCallIssue]
        return Response({"detail": "Notification template sync queued."}, status=202)

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses={200: NotificationTemplateReadSpec, 400: OpenApiTypes.OBJECT},
        description=(
            "Saves the template's variable_mapping. Body is {'variable_mapping': {placeholder: "
            "expression}}. On failure returns 400 with per-placeholder errors under 'errors'."
        ),
    )
    @action(detail=True, methods=["post"])
    def set_variable_mapping(self, request, *args, **kwargs):
        instance = self.get_object()
        if not AuthorizationController.call("can_manage_notification_template", request.user, instance):
            raise PermissionDenied("You do not have permission to manage this notification template.")
        variable_mapping = request.data.get("variable_mapping")
        if not isinstance(variable_mapping, dict):
            raise ValidationError("variable_mapping must be an object.")
        # Per-key errors so the FE can surface each on its own placeholder field.
        errors = validate_variable_mapping(instance, variable_mapping)
        if errors:
            return Response({"errors": errors}, status=400)
        instance.variable_mapping = variable_mapping
        instance.updated_by_id = request.user.id
        instance.save(update_fields=["variable_mapping", "updated_by_id", "modified_date"])
        return Response(NotificationTemplateReadSpec.serialize(instance).to_json())

    # `schema` is DRF's own view attribute (the AutoSchema descriptor drf-spectacular reads);
    # a method of that name shadows it and breaks /api/schema/ for the whole of CARE. The
    # method is named apart from the route, which stays at .../schema/ for the FE.
    @extend_schema(
        responses=OpenApiTypes.OBJECT,
        description=(
            "Browsable field schema for this template's variable_mapping, unioned across every "
            "trigger that renders it. Empty groups when no trigger is linked yet."
        ),
    )
    @action(detail=True, methods=["get"], url_path="schema", url_name="schema")
    def variable_schema(self, request, *args, **kwargs):
        """Browsable field schema for this template's variable_mapping, unioned across
        every trigger that renders it. Empty groups when no trigger is linked yet."""
        instance = self.get_object()
        if not AuthorizationController.call("can_read_notification_template", request.user, instance):
            raise PermissionDenied("You do not have permission to view this notification template.")
        context_slugs = resolve_template_context_slugs(instance)
        return Response(build_notification_schema(context_slugs))

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        description=(
            "Dry-runs an unsaved variable_mapping draft against a preview stub of the linked "
            "context. Returns 'rendered' per placeholder, plus 'errors' for any that failed."
        ),
    )
    @action(detail=True, methods=["post"])
    def preview_variable_mapping(self, request, *args, **kwargs):
        """Renders an unsaved variable_mapping draft against a preview stub of the linked
        context, using the same resolve_variable() a real send uses."""
        instance = self.get_object()
        if not AuthorizationController.call("can_manage_notification_template", request.user, instance):
            raise PermissionDenied("You do not have permission to manage this notification template.")
        variable_mapping = request.data.get("variable_mapping")
        if not isinstance(variable_mapping, dict):
            raise ValidationError("variable_mapping must be an object.")

        # Unlike variable_schema(), which unions every context, preview uses only the first.
        context_slugs = resolve_template_context_slugs(instance)
        preview = build_preview(context_slugs[0]) if context_slugs else None
        if preview is None:
            return Response(
                {"rendered": {}, "detail": "This template is not linked to a trigger with a known context yet."}
            )

        preview_object, extra_context = preview
        rendered: dict[str, str] = {}
        errors: dict[str, str] = {}
        for key, expr in variable_mapping.items():
            if not isinstance(expr, str):
                errors[key] = "Expression must be a string."
                continue
            try:
                rendered[key] = resolve_variable(expr, preview_object, extra_context)
            except Exception as exc:  # surface the render error per key, not a 500
                errors[key] = str(exc)

        body: dict[str, object] = {"rendered": rendered}
        if errors:
            body["errors"] = errors
        return Response(body)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            _FACILITY_PARAM,
            OpenApiParameter(
                name="trigger",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Slug of the trigger to filter events by.",
            ),
            OpenApiParameter(
                name="is_urgent",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description="Filter by urgency. One of true, false, 1, 0.",
            ),
        ]
    )
)
class NotificationEventViewSet(EMRCreateMixin, EMRListMixin, EMRRetrieveMixin, EMRBaseViewSet):
    database_model = NotificationEvent
    pydantic_model = NotificationEventWriteSpec
    pydantic_read_model = NotificationEventReadSpec

    def get_queryset(self):
        queryset = (
            NotificationEvent.objects.all()
            .order_by("-created_date")
            .prefetch_related(
                "recipients",
                "recipients__recipient",
                "recipients__recipient_content_type",
                "recipients__status_events",
            )
        )

        trigger_slug = self.request.GET.get("trigger")
        if trigger_slug:
            queryset = queryset.filter(trigger__slug=trigger_slug)

        is_urgent = _parse_bool_param(self.request, "is_urgent")
        if is_urgent is not None:
            queryset = queryset.filter(is_urgent=is_urgent)

        # Only a list is scoped by the query param. A detail route addresses one event, whose
        # own facility_id is the better scope -- and requiring ?facility= there would 403
        # every non-superuser hitting retrieve or dispatch, which send no such param.
        if self.action != "list":
            return queryset

        facility_id = _authorized_facility_id(self.request, "notification events")
        if facility_id is None:
            return queryset
        return queryset.filter(facility_id=facility_id)

    def authorize_retrieve(self, instance):
        if not AuthorizationController.call("can_read_notification_event", self.request.user, instance):
            raise PermissionDenied("You do not have permission to view this notification event.")

    def authorize_create(self, instance):
        # `instance` here is the write spec, not the model -- core calls authorize_create before
        # de_serialize (EMRCreateMixin.handle_create). The permission is checked in the facility
        # the caller named: can_create_notification_event is declared PermissionContext.FACILITY
        # and the UI gates on facility.permissions, so an org-level check would disagree with both.
        facility = get_object_or_404(Facility, external_id=instance.facility)
        # can_read/create only inspect facility_id, so an unsaved probe works (as in _authorized_facility_id).
        facility_context = NotificationEvent(facility_id=facility.id)

        if not AuthorizationController.call("can_create_notification_event", self.request.user, facility_context):
            raise PermissionDenied("You do not have permission to create notification events.")

    def perform_create(self, instance):
        if instance.trigger.trigger_type != TriggerType.MANUAL:
            raise ValidationError("Only manual-type triggers can be used to create events via this endpoint.")

        instance.created_by_id = self.request.user.id
        instance.updated_by_id = self.request.user.id

        patient_external_ids = getattr(instance, "_recipient_patient_ids", [])
        user_external_ids = getattr(instance, "_recipient_user_ids", [])

        # Resolve + authorize every recipient before touching the DB, so a caller can't target
        # patients/users outside their accessible facility organizations.
        patients_by_external_id = self._authorized_recipient_patients(patient_external_ids)
        users_by_external_id = self._authorized_recipient_users(user_external_ids)

        with transaction.atomic():  # pyright: ignore[reportGeneralTypeIssues]
            instance.save()

            patient_content_type = ContentType.objects.get_for_model(Patient)
            for patient in patients_by_external_id.values():
                NotificationRecipient.objects.create(
                    event=instance,
                    recipient_content_type=patient_content_type,
                    recipient_object_id=patient.id,
                    phone_number=patient.phone_number,
                    # Match the signal path: send on the recipient's own channel, not a
                    # hardcoded default (registry.resolve_channel is provider-agnostic).
                    provider=resolve_channel(patient.phone_number),
                )

            user_content_type = ContentType.objects.get_for_model(User)
            for recipient_user in users_by_external_id.values():
                NotificationRecipient.objects.create(
                    event=instance,
                    recipient_content_type=user_content_type,
                    recipient_object_id=recipient_user.id,
                    phone_number=recipient_user.phone_number,
                    provider=resolve_channel(recipient_user.phone_number),
                )

    def _authorized_recipient_patients(self, external_ids):
        if not external_ids:
            return {}
        candidates = Patient.objects.filter(external_id__in=external_ids)
        accessible = AuthorizationController.call("get_filtered_patients", candidates, self.request.user)
        by_external_id = {str(p.external_id): p for p in accessible}
        missing = {str(eid) for eid in external_ids} - by_external_id.keys()
        if missing:
            raise PermissionDenied(f"You do not have permission to target patient(s): {', '.join(sorted(missing))}.")
        return by_external_id

    def _authorized_recipient_users(self, external_ids):
        if not external_ids:
            return {}
        candidates = {str(u.external_id): u for u in User.objects.filter(external_id__in=external_ids)}
        missing = {str(eid) for eid in external_ids} - candidates.keys()
        if missing:
            raise ValidationError(f"Unknown user id(s): {', '.join(sorted(missing))}.")

        # Bulk-fetch every candidate's org memberships in one query, then group by user,
        # rather than two queries per user in the loop below.
        memberships = FacilityOrganizationUser.objects.filter(user__in=candidates.values()).values_list(
            "user_id", "organization_id"
        )
        org_ids_by_user_id: dict[int, set[int]] = {}
        for user_id, org_id in memberships:
            org_ids_by_user_id.setdefault(user_id, set()).add(org_id)
        all_org_ids = {org_id for org_ids in org_ids_by_user_id.values() for org_id in org_ids}
        orgs_by_id = {org.id: org for org in FacilityOrganization.objects.filter(id__in=all_org_ids)}

        for external_id, recipient_user in candidates.items():
            orgs = [orgs_by_id[oid] for oid in org_ids_by_user_id.get(recipient_user.id, set())]
            if not any(
                AuthorizationController.call("can_list_facility_organization_users_obj", self.request.user, org)
                for org in orgs
            ):
                raise PermissionDenied(f"You do not have permission to target user {external_id}.")
        return candidates

    # Named dispatch_recipients: an @action literally named `dispatch` would shadow View.dispatch and break routing.
    @extend_schema(
        request=None,
        responses={202: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        description=(
            "Queues every not-yet-sent recipient of this event for delivery. 400 when the event "
            "has no pending recipients left."
        ),
    )
    @action(detail=True, methods=["post"], url_path="dispatch", url_name="dispatch")
    def dispatch_recipients(self, request, *args, **kwargs):
        instance = self.get_object()
        if not AuthorizationController.call("can_dispatch_notification_event", request.user, instance):
            raise PermissionDenied("You do not have permission to dispatch this notification event.")

        pending_recipients = list(instance.recipients.filter(latest_status__isnull=True))
        if not pending_recipients:
            return Response(
                {"errors": [{"type": "invalid_state", "msg": "No pending recipients to dispatch."}]},
                status=400,
            )

        # Operator's explicit "send now", so release claims a dead worker left behind. Only
        # stale ones: a claim younger than the cutoff may belong to a worker still inside its
        # send, and clearing that one queues a second task that delivers the message twice.
        stale_cutoff = timezone.now() - timedelta(seconds=int(plugin_settings.DISPATCH_CLAIM_STALE_SECONDS))
        instance.recipients.filter(latest_status__isnull=True, dispatch_started_at__lt=stale_cutoff).update(
            dispatch_started_at=None
        )
        for recipient in pending_recipients:
            dispatch_notification_recipient.delay(recipient.pk)  # pyright: ignore[reportCallIssue]

        return Response({"detail": f"Queued {len(pending_recipients)} recipient(s) for dispatch."}, status=202)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            _FACILITY_PARAM,
            OpenApiParameter(
                name="event",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="External id of the notification event to list recipients for.",
            ),
        ]
    )
)
class NotificationRecipientViewSet(EMRListMixin, EMRRetrieveMixin, EMRBaseViewSet):
    database_model = NotificationRecipient
    pydantic_read_model = NotificationRecipientReadSpec

    def get_queryset(self):
        queryset = (
            NotificationRecipient.objects.all()
            .select_related("event", "recipient_content_type")
            .prefetch_related("recipient", "status_events")
            .order_by("-created_date")
        )
        event_external_id = self.request.GET.get("event")
        if event_external_id:
            queryset = queryset.filter(event__external_id=event_external_id)

        # As on the event viewset: the query param scopes a list, the object's own event
        # scopes a retrieve.
        if self.action != "list":
            return queryset

        facility_id = _authorized_facility_id(self.request, "notification recipients")
        if facility_id is None:
            return queryset
        return queryset.filter(event__facility_id=facility_id)

    def authorize_retrieve(self, instance):
        if not AuthorizationController.call("can_read_notification_event", self.request.user, instance.event):
            raise PermissionDenied("You do not have permission to view this notification recipient.")
