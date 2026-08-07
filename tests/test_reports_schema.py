from django.test import SimpleTestCase, TestCase

from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationTemplate,
    NotificationTrigger,
    TriggerType,
)
from care_im_wrapper.reports.schema import (
    _merge_field_trees,
    build_context_schema,
    build_notification_schema,
    build_preview,
    resolve_template_context_slugs,
)


class TestBuildContextSchema(SimpleTestCase):
    def test_unregistered_slug_returns_none(self):
        self.assertIsNone(build_context_schema("no-such-context"))

    def test_nested_context_is_walked_into(self):
        schema = build_context_schema("token_booking")

        patient = next(f for f in schema["object_fields"] if f["key"] == "patient")
        self.assertTrue(patient["is_nested_context"])
        self.assertIn("name", [f["key"] for f in patient["fields"]])

    def test_extra_context_fields_are_separate_from_object_fields(self):
        schema = build_context_schema("token_booking")

        self.assertIn("doctor_name", [f["key"] for f in schema["extra_context_fields"]])
        self.assertNotIn("doctor_name", [f["key"] for f in schema["object_fields"]])


class TestMergeFieldTrees(SimpleTestCase):
    """Two triggers with different contexts on one template must yield one schema."""

    def test_repeated_key_appears_once(self):
        merged = _merge_field_trees([[{"key": "a"}], [{"key": "a"}]])

        self.assertEqual([f["key"] for f in merged], ["a"])

    def test_nested_fields_of_a_shared_key_are_unioned(self):
        merged = _merge_field_trees(
            [
                [{"key": "patient", "fields": [{"key": "name"}]}],
                [{"key": "patient", "fields": [{"key": "phone"}]}],
            ]
        )

        self.assertEqual([f["key"] for f in merged[0]["fields"]], ["name", "phone"])

    def test_merging_does_not_mutate_the_input_trees(self):
        first = [{"key": "patient", "fields": [{"key": "name"}]}]

        _merge_field_trees([first, [{"key": "patient", "fields": [{"key": "phone"}]}]])

        self.assertEqual([f["key"] for f in first[0]["fields"]], ["name"])


class TestBuildNotificationSchema(SimpleTestCase):
    def test_no_slugs_yields_empty_groups(self):
        self.assertEqual(
            build_notification_schema([]),
            {"contexts": [], "object_fields": [], "extra_context_fields": []},
        )

    def test_unknown_slug_is_skipped_rather_than_raising(self):
        schema = build_notification_schema(["token_booking", "no-such-context"])

        self.assertEqual([c["slug"] for c in schema["contexts"]], ["token_booking"])

    def test_two_contexts_are_unioned(self):
        schema = build_notification_schema(["token_booking", "patient"])
        keys = [f["key"] for f in schema["object_fields"]]

        self.assertIn("token_slot", keys)
        self.assertIn("name", keys)


class TestBuildPreview(SimpleTestCase):
    def test_unregistered_slug_returns_none(self):
        self.assertIsNone(build_preview("no-such-context"))

    def test_preview_object_short_circuits_fields_to_preview_values(self):
        preview_object, extra = build_preview("token_booking")

        self.assertTrue(preview_object.patient.name)
        self.assertTrue(extra["doctor_name"])


class TestResolveTemplateContextSlugs(TestCase):
    def setUp(self):
        self.template = NotificationTemplate.objects.create(
            name="Test", slug="im_test_template", category=NotificationCategory.UTILITY
        )

    def _create_trigger(self, slug, context_slug):
        return NotificationTrigger.objects.create(
            name=slug,
            slug=slug,
            trigger_type=TriggerType.SIGNAL,
            template_slug=self.template.slug,
            context_slug=context_slug,
        )

    def test_template_with_no_trigger_has_no_contexts(self):
        self.assertEqual(resolve_template_context_slugs(self.template), [])

    def test_trigger_without_context_slug_is_excluded(self):
        self._create_trigger("im_test_a", "")

        self.assertEqual(resolve_template_context_slugs(self.template), [])

    def test_two_triggers_sharing_a_context_yield_it_once(self):
        self._create_trigger("im_test_a", "token_booking")
        self._create_trigger("im_test_b", "token_booking")

        self.assertEqual(resolve_template_context_slugs(self.template), ["token_booking"])

    def test_triggers_for_another_template_are_ignored(self):
        NotificationTrigger.objects.create(
            name="other",
            slug="im_test_other",
            trigger_type=TriggerType.SIGNAL,
            template_slug="some_other_template",
            context_slug="patient",
        )

        self.assertEqual(resolve_template_context_slugs(self.template), [])
