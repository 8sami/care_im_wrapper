from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.messaging.exceptions import WhatsAppTemplateNotConfiguredError
from care_im_wrapper.messaging.whatsapp import WhatsAppClient
from care_im_wrapper.models.notification import TemplateParameterFormat

DYNAMIC_URL_BUTTON = {
    "type": "BUTTONS",
    "buttons": [{"type": "URL", "text": "View document", "url": "https://care.example.org/documents/{{1}}"}],
}

STATIC_URL_BUTTON = {
    "type": "BUTTONS",
    "buttons": [{"type": "URL", "text": "Visit us", "url": "https://care.example.org"}],
}


def _make_template(*, variable_mapping, payload, parameter_format=TemplateParameterFormat.POSITIONAL):
    return SimpleNamespace(
        slug="document_ready_update",
        variable_mapping=variable_mapping,
        parameter_format=parameter_format,
        payload=payload,
    )


class BuildButtonComponentsTests(SimpleTestCase):
    def test_dynamic_url_button_renders_positional_button_component(self):
        client = WhatsAppClient()
        template = _make_template(
            variable_mapping={"url_suffix": "{{ document_url_suffix }}"},
            payload={"components": [DYNAMIC_URL_BUTTON]},
        )

        components = client._build_components(template, SimpleNamespace(), {"document_url_suffix": "abc123"})

        self.assertEqual(
            components,
            [{"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "abc123"}]}],
        )

    def test_named_body_template_still_uses_a_positional_button(self):
        """Meta URL-button params are positional even in NAMED templates -- the button
        must not pick up is_named from the body's own parameter_format."""
        client = WhatsAppClient()
        template = _make_template(
            variable_mapping={"patient_name": "{{ object.name }}", "url_suffix": "{{ document_url_suffix }}"},
            payload={"components": [{"type": "BODY", "text": "Hello {{patient_name}}"}, DYNAMIC_URL_BUTTON]},
            parameter_format=TemplateParameterFormat.NAMED,
        )

        components = client._build_components(template, SimpleNamespace(name="Jane"), {"document_url_suffix": "abc123"})

        button_components = [c for c in components if c["type"] == "button"]
        self.assertEqual(
            button_components,
            [{"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "abc123"}]}],
        )
        body_components = [c for c in components if c["type"] == "body"]
        self.assertEqual(body_components[0]["parameters"][0]["parameter_name"], "patient_name")

    def test_static_url_button_with_no_placeholder_is_skipped(self):
        client = WhatsAppClient()
        template = _make_template(
            variable_mapping={"unrelated": "{{ 'x' }}"},
            payload={"components": [STATIC_URL_BUTTON]},
        )

        components = client._build_components(template, SimpleNamespace(), {})

        self.assertEqual(components, [])

    def test_dynamic_url_button_without_url_suffix_mapping_raises(self):
        client = WhatsAppClient()
        template = _make_template(
            variable_mapping={"patient_name": "{{ object.name }}"},
            payload={"components": [DYNAMIC_URL_BUTTON]},
        )

        with self.assertRaises(WhatsAppTemplateNotConfiguredError):
            client._build_components(template, SimpleNamespace(name="Jane"), {})

    def test_non_url_button_type_is_ignored(self):
        client = WhatsAppClient()
        template = _make_template(
            variable_mapping={"url_suffix": "{{ document_url_suffix }}"},
            payload={"components": [{"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Stop"}]}]},
        )

        components = client._build_components(template, SimpleNamespace(), {"document_url_suffix": "abc123"})

        self.assertEqual(components, [])
