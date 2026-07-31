"""Meta rejects a whole template send when any text parameter is blank (error 131008)."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.messaging.exceptions import WhatsAppTemplateNotConfiguredError
from care_im_wrapper.messaging.whatsapp import WhatsAppClient
from care_im_wrapper.models.notification import TemplateParameterFormat

BODY = {"type": "BODY", "text": "Hi {{patient_name}}, invoice {{invoice_number}}"}


def _make_template(**overrides):
    defaults = {
        "slug": "payment_status",
        "variable_mapping": {"patient_name": "{{ object.name }}", "invoice_number": "{{ invoice_number }}"},
        "parameter_format": TemplateParameterFormat.NAMED,
        "payload": {"components": [BODY]},
    }
    return SimpleNamespace(**{**defaults, **overrides})


class BlankParameterGuardTests(SimpleTestCase):
    def setUp(self):
        self.client = WhatsAppClient()
        self.template = _make_template()
        self.related = SimpleNamespace(name="Jane Doe")

    def test_populated_parameters_build_normally(self):
        components = self.client._build_components(self.template, self.related, {"invoice_number": "INV-1"})

        self.assertEqual(
            components,
            [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "parameter_name": "invoice_number", "text": "INV-1"},
                        {"type": "text", "parameter_name": "patient_name", "text": "Jane Doe"},
                    ],
                }
            ],
        )

    def test_empty_string_is_refused(self):
        with self.assertRaises(WhatsAppTemplateNotConfiguredError) as ctx:
            self.client._build_components(self.template, self.related, {"invoice_number": ""})

        self.assertIn("invoice_number", str(ctx.exception))
        self.assertIn("payment_status", str(ctx.exception))

    def test_whitespace_only_is_refused(self):
        """Meta treats a whitespace-only parameter the same as a missing one."""
        with self.assertRaises(WhatsAppTemplateNotConfiguredError):
            self.client._build_components(self.template, self.related, {"invoice_number": "   "})

    def test_undefined_context_value_is_refused_as_a_config_error(self):
        """A mapping naming a value the handler never supplied must fail as a config error."""
        with self.assertRaises(WhatsAppTemplateNotConfiguredError) as ctx:
            self.client._build_components(self.template, self.related, {})

        self.assertIn("invoice_number", str(ctx.exception))

    def test_attribute_resolving_to_empty_is_refused(self):
        """`{{ object.number }}` against an Invoice with no number renders "" rather than raising."""
        template = _make_template(
            variable_mapping={"patient_name": "{{ object.name }}", "invoice_number": "{{ object.number }}"},
        )

        with self.assertRaises(WhatsAppTemplateNotConfiguredError):
            self.client._build_components(template, SimpleNamespace(name="Jane Doe", number=""), {})

    def test_zero_is_not_treated_as_blank(self):
        """A legitimately falsy value is still a value -- only blank text is refused."""
        components = self.client._build_components(self.template, self.related, {"invoice_number": 0})

        texts = [p["text"] for p in components[0]["parameters"]]
        self.assertIn("0", texts)
