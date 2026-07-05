from django.test import SimpleTestCase

from care_im_wrapper.conversation.messages import InboundMessage
from care_im_wrapper.messaging.normalize import normalize_inbound


class NormalizeInboundTextTests(SimpleTestCase):
    def test_unregistered_channel_returns_none(self):
        payload = {"from": "919876543210", "type": "text", "text": {"body": "hi"}}
        channel = "telegram"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_missing_from_returns_none(self):
        payload = {"type": "text", "text": {"body": "hi"}}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_empty_from_returns_none(self):
        payload = {"from": "", "type": "text", "text": {"body": "hi"}}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_phone_without_plus_gets_plus_added(self):
        payload = {"from": "919876543210", "type": "text", "text": {"body": "hi"}}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertEqual(
            result, InboundMessage(phone_number="+919876543210", text="hi", channel="whatsapp", raw_id=None)
        )

    def test_phone_with_plus_unchanged(self):
        payload = {"from": "+919876543210", "type": "text", "text": {"body": "hi"}, "id": "wamid1"}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertEqual(
            result, InboundMessage(phone_number="+919876543210", text="hi", channel="whatsapp", raw_id="wamid1")
        )

    def test_text_body_is_stripped(self):
        payload = {"from": "+919876543210", "type": "text", "text": {"body": "  hi  "}}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertEqual(
            result, InboundMessage(phone_number="+919876543210", text="hi", channel="whatsapp", raw_id=None)
        )

    def test_empty_text_body_returns_none(self):
        payload = {"from": "+919876543210", "type": "text", "text": {"body": ""}}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_missing_text_key_returns_none(self):
        payload = {"from": "+919876543210", "type": "text"}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_unknown_message_type_returns_none(self):
        payload = {"from": "+919876543210", "type": "sticker"}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_missing_type_key_returns_none(self):
        payload = {"from": "+919876543210"}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)


class NormalizeInboundInteractiveTests(SimpleTestCase):
    def test_button_reply_extracts_button_id(self):
        payload = {
            "from": "+919876543210",
            "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": "OPT_1"}},
        }
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertEqual(
            result, InboundMessage(phone_number="+919876543210", text="OPT_1", channel="whatsapp", raw_id=None)
        )

    def test_list_reply_extracts_row_id(self):
        payload = {
            "from": "+919876543210",
            "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": "ROW_3"}},
            "id": "wamid2",
        }
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertEqual(
            result, InboundMessage(phone_number="+919876543210", text="ROW_3", channel="whatsapp", raw_id="wamid2")
        )

    def test_unhandled_interactive_subtype_returns_none(self):
        payload = {"from": "+919876543210", "type": "interactive", "interactive": {"type": "nfm_reply"}}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)

    def test_interactive_missing_interactive_key_returns_none(self):
        payload = {"from": "+919876543210", "type": "interactive"}
        channel = "whatsapp"
        result = normalize_inbound(payload, channel)
        self.assertIsNone(result)
