"""Validation of a variable_mapping draft before it is saved.

A mapping that passes here but is wrong renders blank -- or is refused by the provider --
at send time, on a real patient's message. These are the rules that stop that.
"""

from django.test import TestCase

from care_im_wrapper.models.notification import (
    NotificationCategory,
    NotificationTemplate,
    NotificationTrigger,
    TriggerType,
)
from care_im_wrapper.reports.validation import validate_variable_mapping


class TestValidateVariableMapping(TestCase):
    def setUp(self):
        self.template = NotificationTemplate.objects.create(
            name="Appointment confirmed",
            slug="im_test_template",
            category=NotificationCategory.UTILITY,
            provider="whatsapp",
        )
        NotificationTrigger.objects.create(
            name="Appointment confirmed",
            slug="im_test_trigger",
            trigger_type=TriggerType.SIGNAL,
            template_slug=self.template.slug,
            context_slug="token_booking",
        )

    def _errors(self, mapping):
        return validate_variable_mapping(self.template, mapping)

    def test_known_object_path_is_accepted(self):
        self.assertEqual(self._errors({"1": "{{ object.patient.name }}"}), {})

    def test_known_extra_context_key_is_accepted(self):
        self.assertEqual(self._errors({"1": "{{ doctor_name }}"}), {})

    def test_deeply_nested_known_path_is_accepted(self):
        self.assertEqual(self._errors({"1": "{{ object.token_slot.resource.user.full_name }}"}), {})

    def test_template_engine_global_is_not_treated_as_an_unknown_variable(self):
        self.assertEqual(self._errors({"1": "{{ current_date }}"}), {})

    def test_unknown_object_field_is_rejected(self):
        self.assertIn("1", self._errors({"1": "{{ object.patient.nope }}"}))

    def test_unknown_bare_variable_is_rejected(self):
        self.assertIn("1", self._errors({"1": "{{ mystery }}"}))

    def test_broken_jinja_syntax_is_rejected(self):
        self.assertIn("1", self._errors({"1": "{{ object.patient.name"}))

    def test_only_the_offending_placeholder_is_reported(self):
        errors = self._errors({"1": "{{ object.patient.name }}", "2": "{{ nope }}"})

        self.assertEqual(list(errors), ["2"])


class TestProviderFormattingRules(TestCase):
    """Meta rejects these at send time, so they are caught before saving."""

    def setUp(self):
        self.template = NotificationTemplate.objects.create(
            name="Appointment confirmed",
            slug="im_test_template",
            category=NotificationCategory.UTILITY,
            provider="whatsapp",
        )

    def _errors(self, expr):
        return validate_variable_mapping(self.template, {"1": expr})

    def test_newline_is_rejected(self):
        self.assertIn("1", self._errors("{{ object.patient.name }}\n"))

    def test_five_consecutive_spaces_are_rejected(self):
        self.assertIn("1", self._errors("{{ a }}     {{ b }}"))

    def test_blank_expression_is_rejected(self):
        self.assertIn("1", self._errors("   "))

    def test_plain_text_outside_a_jinja_expression_is_rejected(self):
        self.assertIn("1", self._errors("just text"))


class TestUnconfiguredTemplate(TestCase):
    """A template no trigger points at has no field tree, so field existence is skipped --
    syntax and provider rules still apply."""

    def setUp(self):
        self.template = NotificationTemplate.objects.create(
            name="Orphan",
            slug="im_test_orphan",
            category=NotificationCategory.UTILITY,
            provider="whatsapp",
        )

    def test_unresolvable_name_is_allowed_without_a_context(self):
        self.assertEqual(validate_variable_mapping(self.template, {"1": "{{ anything.at.all }}"}), {})

    def test_provider_rules_are_still_enforced(self):
        self.assertIn("1", validate_variable_mapping(self.template, {"1": "{{ a }}\n"}))


class TestMappingCompleteness(TestCase):
    """A mapping filling only some of the approved body's placeholders sends fewer
    parameters than the template declares, which the provider rejects outright."""

    def setUp(self):
        self.template = NotificationTemplate.objects.create(
            name="Two placeholders",
            slug="im_test_two_placeholders",
            category=NotificationCategory.UTILITY,
            provider="whatsapp",
            payload={"components": [{"type": "BODY", "text": "Hello {{1}}, your {{2}} is ready"}]},
        )

    def test_partial_mapping_reports_the_missing_placeholder(self):
        errors = validate_variable_mapping(self.template, {"1": "{{ object.name }}"})

        self.assertIn("2", errors)
        self.assertNotIn("1", errors)

    def test_complete_mapping_is_accepted(self):
        mapping = {"1": "{{ object.name }}", "2": "{{ object.name }}"}

        self.assertEqual(validate_variable_mapping(self.template, mapping), {})

    def test_empty_mapping_is_allowed_as_unconfigured(self):
        self.assertEqual(validate_variable_mapping(self.template, {}), {})
