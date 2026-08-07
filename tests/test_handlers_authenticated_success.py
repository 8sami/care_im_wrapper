from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from care_im_wrapper.conversation.handlers import Outbound, _handle_authenticated
from care_im_wrapper.conversation.menus import MenuOption
from care_im_wrapper.conversation.messages import InteractiveType, OutboundMessage
from care_im_wrapper.models import ConversationSession

PHONE = "+919876543210"
CHANNEL = "whatsapp"
GREETING = "Please choose an option:"  # len == 24
MENU_TEXT = "1. Test Label\n0. Logout"
LIMIT = 1024  # WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT default
SEPARATOR = 2  # the blank line between the data and the prompt below it
#: The longest data page that still shares one interactive body with the prompt.
FITS = LIMIT - len(GREETING) - SEPARATOR


def _make_actor():
    return SimpleNamespace(user_type="patient", instance=SimpleNamespace(id=1))


class HandleAuthenticatedSuccessPathTests(TestCase):
    def setUp(self):
        self.session = ConversationSession.objects.create(
            phone_number=PHONE,
            provider=CHANNEL,
            state=ConversationSession.State.AUTHENTICATED,
            user_type="patient",
            user_id=42,
        )

    def _patch_menu(self, renderer_text):
        fetcher = MagicMock(return_value="fake_data")
        renderer = MagicMock(return_value=OutboundMessage(text=renderer_text))
        entry = {"1": MenuOption(label="Test Label", fetcher=fetcher, renderer=renderer)}
        return patch.dict("care_im_wrapper.conversation.menus._MAIN_MENU", entry, clear=True)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_short_data_sends_single_combined_message(self, mock_resolve_actor):
        """An unpaged reply keeps the View Menu interactive list -- buttons are only for
        pagination, so a short list is not routed through them."""
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu("DATA"):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 1)
        call_msg = outbox[0].message

        expected_full_text = "DATA\n\nPlease choose an option:\n\n1. Test Label\n0. Logout"
        self.assertEqual(call_msg.text, expected_full_text)
        self.assertEqual(call_msg.interactive.body, "DATA\n\nPlease choose an option:")
        self.assertEqual(call_msg.interactive.type, InteractiveType.LIST)
        self.assertEqual(call_msg.interactive.button_label, "View Menu")
        self.assertEqual(
            call_msg.interactive.action_data,
            [
                {
                    "title": "Menu",
                    "rows": [
                        {"id": "1", "title": "Test Label"},
                        {"id": "0", "title": "Logout", "description": "End this session"},
                    ],
                }
            ],
        )

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_data_exceeding_limit_splits_into_two_messages(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        long_text = "A" * (FITS + 1)
        outbox: list[Outbound] = []

        with self._patch_menu(long_text):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 2)

        first = outbox[0]
        self.assertEqual(first.message.text, long_text)
        self.assertIsNone(first.message.interactive)
        self.assertTrue(first.pace)

        second = outbox[1]
        # The plain-text half of the follow-up keeps the menu, so a provider that cannot
        # draw rows still shows what they were.
        self.assertEqual(second.message.text, f"{GREETING}\n\n{MENU_TEXT}")
        self.assertEqual(second.message.interactive.type, InteractiveType.LIST)
        self.assertEqual(second.message.interactive.body, GREETING)
        self.assertFalse(second.pace)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_data_filling_the_body_exactly_does_not_split(self, mock_resolve_actor):
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu("A" * FITS):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 1)
        self.assertEqual(len(outbox[0].message.interactive.body), LIMIT)

    @patch("care_im_wrapper.conversation.handlers.resolve_actor")
    def test_one_character_over_the_body_splits(self, mock_resolve_actor):
        """The separator counts: a body one character over is one the provider will reject."""
        mock_resolve_actor.return_value = _make_actor()
        outbox: list[Outbound] = []

        with self._patch_menu("A" * (FITS + 1)):
            _handle_authenticated(self.session, PHONE, "1", CHANNEL, outbox)

        self.assertEqual(len(outbox), 2)
