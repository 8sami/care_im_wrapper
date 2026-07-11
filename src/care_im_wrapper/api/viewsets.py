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
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from care_im_wrapper.api.spec import (
    NotificationEventReadSpec,
    NotificationEventWriteSpec,
    NotificationRecipientReadSpec,
    NotificationTemplateReadSpec,
    NotificationTriggerReadSpec,
)
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationTemplate,
    NotificationTrigger,
    TriggerType,
)
from care_im_wrapper.tasks import dispatch_notification_recipient


def _resolve_facility_root_org(facility_external_id):
    facility = get_object_or_404(Facility, external_id=facility_external_id)
    return get_object_or_404(FacilityOrganization, facility=facility, org_type="root")


class BaseViewSet(GenericViewSet):
    @action(detail=False, methods=["get"])
    def hello(self, request, *args, **kwargs):
        return Response({"message": "Hello from care_im_wrapper plugin!"})


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
        return NotificationTemplate.objects.all().order_by("name")

    def authorize_retrieve(self, instance):
        if not AuthorizationController.call("can_read_notification_template", self.request.user, instance):
            raise PermissionDenied("You do not have permission to view this notification template.")

    @action(detail=True, methods=["post"])
    def toggle_active(self, request, *args, **kwargs):
        instance = self.get_object()
        if not AuthorizationController.call("can_manage_notification_template", request.user, instance):
            raise PermissionDenied("You do not have permission to manage this notification template.")
        instance.is_active = not instance.is_active
        instance.updated_by_id = request.user.id
        instance.save(update_fields=["is_active", "updated_by_id", "modified_date"])
        return Response(NotificationTemplateReadSpec.serialize(instance).to_json())

    @action(detail=True, methods=["post"])
    def set_variable_mapping(self, request, *args, **kwargs):
        instance = self.get_object()
        if not AuthorizationController.call("can_manage_notification_template", request.user, instance):
            raise PermissionDenied("You do not have permission to manage this notification template.")
        variable_mapping = request.data.get("variable_mapping")
        if not isinstance(variable_mapping, dict):
            raise ValidationError("variable_mapping must be an object.")
        instance.variable_mapping = variable_mapping
        instance.updated_by_id = request.user.id
        instance.save(update_fields=["variable_mapping", "updated_by_id", "modified_date"])
        return Response(NotificationTemplateReadSpec.serialize(instance).to_json())


class NotificationEventViewSet(EMRCreateMixin, EMRListMixin, EMRRetrieveMixin, EMRBaseViewSet):
    database_model = NotificationEvent
    pydantic_model = NotificationEventWriteSpec
    pydantic_read_model = NotificationEventReadSpec

    def get_queryset(self):
        queryset = NotificationEvent.objects.all().order_by("-created_date").prefetch_related("recipients")

        trigger_slug = self.request.GET.get("trigger")
        if trigger_slug:
            queryset = queryset.filter(trigger__slug=trigger_slug)

        is_urgent = self.request.GET.get("is_urgent")
        if is_urgent is not None:
            queryset = queryset.filter(is_urgent=is_urgent.lower() in ("true", "1"))

        facility_external_id = self.request.GET.get("facility")
        if not facility_external_id:
            # get_accessible_facility_organizations can't answer "every org this user can see" without one given.
            if not self.request.user.is_superuser:
                raise PermissionDenied("The facility query parameter is required.")
            return queryset

        root_org = _resolve_facility_root_org(facility_external_id)
        # can_read_notification_event only inspects facility_organization_cache, so an unsaved probe event works.
        probe_event = NotificationEvent(facility_organization_cache=[root_org.id])
        if not AuthorizationController.call("can_read_notification_event", self.request.user, probe_event):
            raise PermissionDenied("You do not have permission to view notification events for this facility.")
        return queryset.filter(facility_organization_cache__contains=[root_org.id])

    def authorize_create(self, instance):
        # Manually-created events have no related_object/facility context yet, so authorize at the org level only.
        if not AuthorizationController.call("can_create_notification_event", self.request.user, None):
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
                )

            user_content_type = ContentType.objects.get_for_model(User)
            for recipient_user in users_by_external_id.values():
                NotificationRecipient.objects.create(
                    event=instance,
                    recipient_content_type=user_content_type,
                    recipient_object_id=recipient_user.id,
                    phone_number=recipient_user.phone_number,
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

        for external_id, recipient_user in candidates.items():
            org_ids = FacilityOrganizationUser.objects.filter(user=recipient_user).values_list(
                "organization_id", flat=True
            )
            orgs = FacilityOrganization.objects.filter(id__in=org_ids)
            if not any(
                AuthorizationController.call("can_list_facility_organization_users_obj", self.request.user, org)
                for org in orgs
            ):
                raise PermissionDenied(f"You do not have permission to target user {external_id}.")
        return candidates

    # Named dispatch_recipients: an @action literally named `dispatch` would shadow View.dispatch and break routing.
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

        for recipient in pending_recipients:
            dispatch_notification_recipient.delay(recipient.pk)  # pyright: ignore[reportCallIssue]

        return Response({"detail": f"Queued {len(pending_recipients)} recipient(s) for dispatch."}, status=202)


class NotificationRecipientViewSet(EMRListMixin, EMRRetrieveMixin, EMRBaseViewSet):
    database_model = NotificationRecipient
    pydantic_read_model = NotificationRecipientReadSpec

    def get_queryset(self):
        queryset = NotificationRecipient.objects.all().select_related("event").order_by("-created_date")
        event_external_id = self.request.GET.get("event")
        if event_external_id:
            queryset = queryset.filter(event__external_id=event_external_id)

        facility_external_id = self.request.GET.get("facility")
        if not facility_external_id:
            if not self.request.user.is_superuser:
                raise PermissionDenied("The facility query parameter is required.")
            return queryset

        root_org = _resolve_facility_root_org(facility_external_id)
        probe_event = NotificationEvent(facility_organization_cache=[root_org.id])
        if not AuthorizationController.call("can_read_notification_event", self.request.user, probe_event):
            raise PermissionDenied("You do not have permission to view notification recipients for this facility.")
        return queryset.filter(event__facility_organization_cache__contains=[root_org.id])

    def authorize_retrieve(self, instance):
        if not AuthorizationController.call("can_read_notification_event", self.request.user, instance.event):
            raise PermissionDenied("You do not have permission to view this notification recipient.")
