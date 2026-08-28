from unittest.mock import patch

from care.emr.models.organization import FacilityOrganization
from care.utils.tests.base import CareAPITestBase
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status

from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationEvent,
    NotificationRecipient,
    NotificationTemplate,
    NotificationTrigger,
    TemplateApprovalStatus,
    TriggerType,
)
from care_im_wrapper.security.permissions import NotificationPermissions


class NotificationAPITestBase(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.facility = self.create_facility(user=self.user)
        # Facility.save() creates the root org the viewsets resolve ?facility= to.
        self.root_org = FacilityOrganization.objects.get(facility=self.facility, org_type="root")
        self.organization = self.create_organization()
        self.template = self._create_template()
        self.trigger = self._create_trigger()
        self.client.force_authenticate(user=self.user)

    # Slugs are prefixed because the migrations seed the real appointment/patient/invoice
    # triggers and NotificationTrigger.slug is unique.
    def _create_template(self, slug="im_test_template", **kwargs):
        data = {
            "name": "Appointment confirmed",
            "slug": slug,
            "category": NotificationCategory.UTILITY,
            "approval_status": TemplateApprovalStatus.ACTIVE,
        }
        data.update(kwargs)
        return NotificationTemplate.objects.create(**data)

    def _create_trigger(self, slug="im_test_trigger", **kwargs):
        data = {
            "name": "Appointment confirmed",
            "slug": slug,
            "trigger_type": TriggerType.SIGNAL,
            "template_slug": self.template.slug,
        }
        data.update(kwargs)
        return NotificationTrigger.objects.create(**data)

    def _create_event(self, facility_id=None, **kwargs):
        """NotificationEvent.save() derives facility_id from related_object, so the scope is
        written afterwards with an update() save() cannot overwrite."""
        data = {
            "template": self.template,
            "trigger": self.trigger,
            "title": "Your appointment is confirmed",
        }
        data.update(kwargs)
        event = NotificationEvent.objects.create(**data)
        if facility_id is not None:
            NotificationEvent.objects.filter(pk=event.pk).update(facility_id=facility_id)
            event.refresh_from_db()
        return event

    def _create_recipient(self, event, patient=None, **kwargs):
        patient = patient or self.create_patient()
        data = {
            "event": event,
            "recipient_content_type": ContentType.objects.get_for_model(patient),
            "recipient_object_id": patient.id,
            "phone_number": "+919876543210",
        }
        data.update(kwargs)
        return NotificationRecipient.objects.create(**data)

    def grant_in_organization(self, permission):
        """GENERIC-context permission, checked against OrganizationUser."""
        role = self.create_role_with_permissions([permission.name])
        self.attach_role_organization_user(self.organization, self.user, role)

    def grant_in_facility(self, permission):
        """FACILITY-context permission, checked against the event's facility."""
        role = self.create_role_with_permissions([permission.name])
        self.attach_role_facility_organization_user(self.root_org, self.user, role)


class TestPluginSchemaGeneration(CareAPITestBase):
    """The plugin is mounted into core's URLconf, so a view attribute drf-spectacular
    chokes on takes down /api/schema/ for the whole of CARE. An @action named `schema`
    shadowed DRF's own view attribute and did exactly that.
    """

    def test_plugin_routes_are_in_the_generated_schema(self):
        from drf_spectacular.generators import SchemaGenerator

        paths = SchemaGenerator().get_schema(request=None, public=True)["paths"]

        self.assertIn(reverse("notification-templates-list"), paths)
        self.assertIn(reverse("notification-events-list"), paths)
        # The variable-schema action is named apart from its route; the FE calls .../schema/.
        self.assertIn("/api/care_im_wrapper/notification-templates/{external_id}/schema/", paths)


class TestNotificationTemplateViewSet(NotificationAPITestBase):
    def setUp(self):
        super().setUp()
        self.detail_url = reverse(
            "notification-templates-detail",
            kwargs={"external_id": self.template.external_id},
        )

    def test_retrieve_denied_without_read_permission(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retrieve_with_read_permission(self):
        self.grant_in_organization(NotificationPermissions.can_read_notification_template)

        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["slug"], self.template.slug)

    def test_toggle_active_denied_with_only_read_permission(self):
        self.grant_in_organization(NotificationPermissions.can_read_notification_template)
        url = reverse(
            "notification-templates-toggle-active",
            kwargs={"external_id": self.template.external_id},
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.template.refresh_from_db()
        self.assertTrue(self.template.is_active)

    def test_toggle_active_with_manage_permission(self):
        self.grant_in_organization(NotificationPermissions.can_manage_notification_template)
        url = reverse(
            "notification-templates-toggle-active",
            kwargs={"external_id": self.template.external_id},
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.template.refresh_from_db()
        self.assertFalse(self.template.is_active)

    def test_sync_denied_without_manage_permission(self):
        with patch("care_im_wrapper.api.viewsets.sync_notification_templates.delay") as mock_delay:
            response = self.client.post(reverse("notification-templates-sync"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_delay.assert_not_called()

    def test_sync_with_manage_permission(self):
        self.grant_in_organization(NotificationPermissions.can_manage_notification_template)

        with patch("care_im_wrapper.api.viewsets.sync_notification_templates.delay") as mock_delay:
            response = self.client.post(reverse("notification-templates-sync"))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()


class TestTemplateVariableMapping(NotificationAPITestBase):
    def setUp(self):
        super().setUp()
        self.trigger.context_slug = "token_booking"
        self.trigger.save()
        self.schema_url = reverse(
            "notification-templates-schema",
            kwargs={"external_id": self.template.external_id},
        )
        self.set_url = reverse(
            "notification-templates-set-variable-mapping",
            kwargs={"external_id": self.template.external_id},
        )

    def _set_mapping(self, mapping):
        return self.client.post(self.set_url, {"variable_mapping": mapping}, format="json")

    def test_set_mapping_denied_with_only_read_permission(self):
        self.grant_in_organization(NotificationPermissions.can_read_notification_template)

        response = self._set_mapping({"1": "{{ object.patient.name }}"})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_set_valid_mapping(self):
        self.grant_in_organization(NotificationPermissions.can_manage_notification_template)

        response = self._set_mapping({"1": "{{ object.patient.name }}"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.template.refresh_from_db()
        self.assertEqual(self.template.variable_mapping, {"1": "{{ object.patient.name }}"})

    def test_invalid_mapping_is_rejected_per_placeholder_without_saving(self):
        self.grant_in_organization(NotificationPermissions.can_manage_notification_template)

        response = self._set_mapping({"1": "{{ object.patient.name }}", "2": "{{ object.patient.nope }}"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.data["errors"]), ["2"])
        self.template.refresh_from_db()
        self.assertIsNone(self.template.variable_mapping)

    def test_variable_schema_denied_without_read_permission(self):
        response = self.client.get(self.schema_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_variable_schema_returns_the_linked_context_fields(self):
        self.grant_in_organization(NotificationPermissions.can_read_notification_template)

        response = self.client.get(self.schema_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["slug"] for c in response.data["contexts"]], ["token_booking"])
        self.assertIn("patient", [f["key"] for f in response.data["object_fields"]])

    def test_variable_schema_is_empty_for_a_template_with_no_trigger(self):
        self.grant_in_organization(NotificationPermissions.can_read_notification_template)
        orphan = self._create_template(slug="im_test_orphan")
        url = reverse(
            "notification-templates-schema",
            kwargs={"external_id": orphan.external_id},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["object_fields"], [])

    def test_preview_renders_a_draft_against_the_context_stub(self):
        self.grant_in_organization(NotificationPermissions.can_manage_notification_template)
        url = reverse(
            "notification-templates-preview-variable-mapping",
            kwargs={"external_id": self.template.external_id},
        )

        response = self.client.post(
            url,
            {"variable_mapping": {"1": "{{ object.patient.name }}"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["rendered"]["1"])


class TestNotificationEventListScoping(NotificationAPITestBase):
    def setUp(self):
        super().setUp()
        self.event = self._create_event(facility_id=self.root_org.facility_id)
        self.list_url = reverse("notification-events-list")

    def test_an_event_without_a_related_object_keeps_its_assigned_facility(self):
        """The other half of save(): with no related object there is nothing to derive a
        facility from, so an assigned one must survive. Events seeded by
        seed_notification_test_data --create-event have no related object, and are invisible
        in the facility-scoped list if this stops holding."""
        event = NotificationEvent.objects.create(
            template=self.template,
            trigger=self.trigger,
            title="Seeded by hand",
            facility_id=self.facility.id,
        )

        event.refresh_from_db()
        self.assertEqual(event.facility_id, self.facility.id)

    def test_a_signal_event_takes_its_facility_from_the_related_object(self):
        """save() must keep overriding facility_id whenever a related object is present, so a
        signal-fired event cannot be pointed at a facility other than its object's."""
        patient = self.create_patient()
        event = NotificationEvent(template=self.template, trigger=self.trigger, title="Signal fired")
        event.related_object = patient
        event.facility_id = self.facility.id
        event.save()

        event.refresh_from_db()
        self.assertNotEqual(event.facility_id, self.facility.id)

    def test_list_requires_a_facility_for_a_non_superuser(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_may_list_across_facilities(self):
        self.client.force_authenticate(user=self.create_super_user())

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_list_denied_without_permission_in_the_named_facility(self):
        response = self.client.get(self.list_url, {"facility": str(self.facility.external_id)})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_allowed_for_a_child_organization_member(self):
        """Staff are normally attached to a department org, not the facility root. Scoping
        the check to the root org alone locked them out of a permission they hold."""
        department = self.create_facility_organization(facility=self.facility, org_type="dept")
        role = self.create_role_with_permissions([NotificationPermissions.can_read_notification_event.name])
        self.attach_role_facility_organization_user(department, self.user, role)

        response = self.client.get(self.list_url, {"facility": str(self.facility.external_id)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([r["title"] for r in response.data["results"]], [self.event.title])

    def test_list_excludes_another_facilitys_events(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)
        other_facility = self.create_facility(user=self.create_user())
        other_root = FacilityOrganization.objects.get(facility=other_facility, org_type="root")
        self._create_event(facility_id=other_root.facility_id, title="Elsewhere")

        response = self.client.get(self.list_url, {"facility": str(self.facility.external_id)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([r["title"] for r in response.data["results"]], [self.event.title])

    def test_filter_by_is_urgent(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)
        self._create_event(facility_id=self.root_org.facility_id, title="Urgent", is_urgent=True)

        response = self.client.get(
            self.list_url,
            {"facility": str(self.facility.external_id), "is_urgent": "true"},
        )

        self.assertEqual([r["title"] for r in response.data["results"]], ["Urgent"])

    def test_unparseable_is_urgent_is_rejected_rather_than_read_as_false(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)

        response = self.client.get(
            self.list_url,
            {"facility": str(self.facility.external_id), "is_urgent": "maybe"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_by_trigger(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)
        other_trigger = self._create_trigger(slug="im_test_cancelled")
        self._create_event(facility_id=self.root_org.facility_id, title="Cancelled", trigger=other_trigger)

        response = self.client.get(
            self.list_url,
            {"facility": str(self.facility.external_id), "trigger": "im_test_cancelled"},
        )

        self.assertEqual([r["title"] for r in response.data["results"]], ["Cancelled"])


class TestNotificationEventDispatch(NotificationAPITestBase):
    def setUp(self):
        super().setUp()
        self.event = self._create_event(facility_id=self.root_org.facility_id)
        self.recipient = self._create_recipient(self.event)
        self.url = reverse(
            "notification-events-dispatch",
            kwargs={"external_id": self.event.external_id},
        )

    def _dispatch(self):
        with patch("care_im_wrapper.api.viewsets.dispatch_notification_recipient.delay") as mock_delay:
            response = self.client.post(self.url)
        return response, mock_delay

    def test_dispatch_denied_with_only_read_permission(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)

        response, mock_delay = self._dispatch()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_delay.assert_not_called()

    def test_dispatch_queues_pending_recipients(self):
        """The FE sends no ?facility=, so this also pins that a detail route does not
        require it -- requiring it 403s every non-superuser."""
        self.grant_in_facility(NotificationPermissions.can_dispatch_notification_event)

        response, mock_delay = self._dispatch()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once_with(self.recipient.pk)

    def test_dispatch_clears_a_stale_claim(self):
        from datetime import timedelta

        from django.utils import timezone

        from care_im_wrapper.settings import plugin_settings

        self.grant_in_facility(NotificationPermissions.can_dispatch_notification_event)
        stale_age = int(plugin_settings.DISPATCH_CLAIM_STALE_SECONDS) + 60
        self.recipient.dispatch_started_at = timezone.now() - timedelta(seconds=stale_age)
        self.recipient.save(update_fields=["dispatch_started_at"])

        response, mock_delay = self._dispatch()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.recipient.refresh_from_db()
        self.assertIsNone(self.recipient.dispatch_started_at)

    def test_dispatch_leaves_a_live_claim_alone(self):
        """A claim younger than the stale cutoff belongs to a worker that may still be
        inside its send; clearing it would deliver the message twice."""
        from django.utils import timezone

        self.grant_in_facility(NotificationPermissions.can_dispatch_notification_event)
        claimed_at = timezone.now()
        self.recipient.dispatch_started_at = claimed_at
        self.recipient.save(update_fields=["dispatch_started_at"])

        response, mock_delay = self._dispatch()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.dispatch_started_at, claimed_at)

    def test_dispatch_refused_when_nothing_is_pending(self):
        self.grant_in_facility(NotificationPermissions.can_dispatch_notification_event)
        self.recipient.latest_status = "sent"
        self.recipient.save(update_fields=["latest_status"])

        response, mock_delay = self._dispatch()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_delay.assert_not_called()

    def test_already_sent_recipients_are_not_queued_again(self):
        self.grant_in_facility(NotificationPermissions.can_dispatch_notification_event)
        self.recipient.latest_status = "delivered"
        self.recipient.save(update_fields=["latest_status"])
        pending = self._create_recipient(self.event)

        response, mock_delay = self._dispatch()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once_with(pending.pk)


class TestNotificationRecipientViewSet(NotificationAPITestBase):
    def setUp(self):
        super().setUp()
        self.event = self._create_event(facility_id=self.root_org.facility_id)
        self.patient = self.create_patient(name="Jane Doe", phone_number="+919876543210")
        self.recipient = self._create_recipient(self.event, patient=self.patient)
        self.list_url = reverse("notification-recipients-list")

    def test_list_denied_without_permission_in_the_named_facility(self):
        response = self.client.get(self.list_url, {"facility": str(self.facility.external_id)})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_excludes_another_facilitys_recipients(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)
        other_facility = self.create_facility(user=self.create_user())
        other_root = FacilityOrganization.objects.get(facility=other_facility, org_type="root")
        self._create_recipient(self._create_event(facility_id=other_root.facility_id, title="Elsewhere"))

        response = self.client.get(self.list_url, {"facility": str(self.facility.external_id)})

        self.assertEqual(response.data["count"], 1)

    def test_filter_by_event(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)
        self._create_recipient(self._create_event(facility_id=self.root_org.facility_id, title="Another"))

        response = self.client.get(
            self.list_url,
            {
                "facility": str(self.facility.external_id),
                "event": str(self.event.external_id),
            },
        )

        self.assertEqual(response.data["count"], 1)

    def test_the_delivery_log_masks_the_recipients_number(self):
        self.grant_in_facility(NotificationPermissions.can_read_notification_event)

        response = self.client.get(self.list_url, {"facility": str(self.facility.external_id)})

        row = response.data["results"][0]
        self.assertEqual(row["recipient_name"], "Jane Doe")
        self.assertNotIn("9876543210", row["recipient_phone"])
