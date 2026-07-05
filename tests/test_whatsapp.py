from unittest.mock import MagicMock, patch

import httpx
from django.test import SimpleTestCase, override_settings

from care_im_wrapper.conversation.messages import (
    InteractivePayload,
    InteractiveType,
    OutboundMessage,
)
from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
)
from care_im_wrapper.messaging.whatsapp import WhatsAppClient

VALID_CREDENTIALS = {
    "care_im_wrapper": {
        "WHATSAPP_ACCESS_TOKEN": "test-token",
        "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
    }
}


def _make_http_status_error(status_code, error_code=None, text="error"):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text
    if error_code is not None:
        mock_response.content = b"has-content"
        mock_response.json.return_value = {"error": {"code": error_code}}
    else:
        mock_response.content = b""
    return httpx.HTTPStatusError("error", request=MagicMock(), response=mock_response)


@override_settings(PLUGIN_CONFIGS=VALID_CREDENTIALS)
class WhatsAppClientSendTextTests(SimpleTestCase):
    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_send_text_builds_correct_payload(self, mock_post):
        mock_post.return_value = MagicMock()

        client = WhatsAppClient()
        client.send_text("+919876543210", "Hello there")

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": "+919876543210",
                "type": "text",
                "text": {"body": "Hello there"},
            },
        )
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")


class WhatsAppClientMissingCredentialsTests(SimpleTestCase):
    @override_settings(
        PLUGIN_CONFIGS={"care_im_wrapper": {"WHATSAPP_ACCESS_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": ""}}
    )
    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_missing_token_and_phone_id_raises_runtime_error(self, mock_post):
        client = WhatsAppClient()
        with self.assertRaises(RuntimeError):
            client.send_text("+919876543210", "hi")
        mock_post.assert_not_called()

    @override_settings(
        PLUGIN_CONFIGS={"care_im_wrapper": {"WHATSAPP_ACCESS_TOKEN": "test-token", "WHATSAPP_PHONE_NUMBER_ID": ""}}
    )
    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_missing_phone_id_raises_runtime_error(self, mock_post):
        client = WhatsAppClient()
        with self.assertRaises(RuntimeError):
            client.send_text("+919876543210", "hi")
        mock_post.assert_not_called()


@override_settings(PLUGIN_CONFIGS=VALID_CREDENTIALS)
class WhatsAppClientSendInteractiveTests(SimpleTestCase):
    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_none_interactive_falls_back_to_send_text(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        msg = OutboundMessage(text="fallback text")
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["type"], "text")
        self.assertEqual(kwargs["json"]["text"]["body"], "fallback text")

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_reply_buttons_builds_correct_payload_and_caps_at_three(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        payload = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body="Pick one",
            action_data=[
                {"id": "OPT_1", "title": "Option 1"},
                {"id": "OPT_2", "title": "Option 2"},
                {"id": "OPT_3", "title": "Option 3"},
                {"id": "OPT_4", "title": "Option 4"},
            ],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        sent = kwargs["json"]
        self.assertEqual(sent["type"], "interactive")
        self.assertEqual(sent["interactive"]["type"], "button")
        self.assertEqual(sent["interactive"]["body"]["text"], "Pick one")
        buttons = sent["interactive"]["action"]["buttons"]
        self.assertEqual(len(buttons), 3)
        self.assertEqual(buttons[0], {"type": "reply", "reply": {"id": "OPT_1", "title": "Option 1"}})
        self.assertEqual(buttons[2], {"type": "reply", "reply": {"id": "OPT_3", "title": "Option 3"}})

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_reply_button_title_truncated_to_twenty_chars(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        long_title = "A" * 30
        payload = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body="Pick one",
            action_data=[{"id": "OPT_1", "title": long_title}],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        button = kwargs["json"]["interactive"]["action"]["buttons"][0]
        self.assertEqual(button["reply"]["title"], "A" * 20)

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_list_type_caps_rows_at_ten_across_sections(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        payload = InteractivePayload(
            type=InteractiveType.LIST,
            body="Choose",
            action_data=[
                {"title": "Section 1", "rows": [{"id": f"R{i}", "title": f"Row {i}"} for i in range(6)]},
                {"title": "Section 2", "rows": [{"id": f"R{i}", "title": f"Row {i}"} for i in range(6, 12)]},
            ],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        sections = kwargs["json"]["interactive"]["action"]["sections"]
        total_rows = sum(len(s["rows"]) for s in sections)
        self.assertEqual(total_rows, 10)

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_list_row_description_included_and_truncated(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        long_description = "B" * 100
        payload = InteractivePayload(
            type=InteractiveType.LIST,
            body="Choose",
            action_data=[
                {
                    "title": "Section 1",
                    "rows": [{"id": "R1", "title": "Row 1", "description": long_description}],
                },
            ],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        row = kwargs["json"]["interactive"]["action"]["sections"][0]["rows"][0]
        self.assertEqual(row["description"], "B" * 72)

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_list_row_without_description_key_omits_it(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        payload = InteractivePayload(
            type=InteractiveType.LIST,
            body="Choose",
            action_data=[{"title": "Section 1", "rows": [{"id": "R1", "title": "Row 1"}]}],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        row = kwargs["json"]["interactive"]["action"]["sections"][0]["rows"][0]
        self.assertNotIn("description", row)

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_cta_url_builds_correct_payload(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        payload = InteractivePayload(
            type=InteractiveType.CTA_URL,
            body="Click below",
            action_data=[{"display_text": "Open Portal", "url": "https://example.com"}],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        sent = kwargs["json"]["interactive"]
        self.assertEqual(sent["type"], "cta_url")
        self.assertEqual(sent["action"]["parameters"]["display_text"], "Open Portal")
        self.assertEqual(sent["action"]["parameters"]["url"], "https://example.com")

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_header_and_footer_included_when_set(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        payload = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body="Pick one",
            action_data=[{"id": "OPT_1", "title": "Option 1"}],
            header="Header text",
            footer="Footer text",
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        sent = kwargs["json"]["interactive"]
        self.assertEqual(sent["header"], {"type": "text", "text": "Header text"})
        self.assertEqual(sent["footer"], {"text": "Footer text"})

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_header_and_footer_omitted_when_none(self, mock_post):
        mock_post.return_value = MagicMock()
        client = WhatsAppClient()
        payload = InteractivePayload(
            type=InteractiveType.REPLY_BUTTONS,
            body="Pick one",
            action_data=[{"id": "OPT_1", "title": "Option 1"}],
        )
        msg = OutboundMessage(text="fallback", interactive=payload)
        client.send_interactive("+919876543210", msg)

        _, kwargs = mock_post.call_args
        sent = kwargs["json"]["interactive"]
        self.assertNotIn("header", sent)
        self.assertNotIn("footer", sent)


@override_settings(PLUGIN_CONFIGS=VALID_CREDENTIALS)
class WhatsAppClientErrorHandlingTests(SimpleTestCase):
    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_429_status_raises_pair_rate_limit_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = _make_http_status_error(429)
        mock_post.return_value = mock_response

        client = WhatsAppClient()
        with self.assertRaises(WhatsAppPairRateLimitError):
            client.send_text("+919876543210", "hi")

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_error_code_131056_raises_pair_rate_limit_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = _make_http_status_error(400, error_code=131056)
        mock_post.return_value = mock_response

        client = WhatsAppClient()
        with self.assertRaises(WhatsAppPairRateLimitError):
            client.send_text("+919876543210", "hi")

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_other_4xx_raises_bad_request_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = _make_http_status_error(400)
        mock_post.return_value = mock_response

        client = WhatsAppClient()
        with self.assertRaises(WhatsAppBadRequestError):
            client.send_text("+919876543210", "hi")

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_5xx_raises_server_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = _make_http_status_error(500)
        mock_post.return_value = mock_response

        client = WhatsAppClient()
        with self.assertRaises(WhatsAppServerError):
            client.send_text("+919876543210", "hi")

    @patch("care_im_wrapper.messaging.whatsapp.httpx.post")
    def test_network_error_raises_whatsapp_network_error(self, mock_post):
        mock_post.side_effect = httpx.ConnectTimeout("timed out")

        client = WhatsAppClient()
        with self.assertRaises(WhatsAppNetworkError):
            client.send_text("+919876543210", "hi")
