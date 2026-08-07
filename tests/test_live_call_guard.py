from django.test import SimpleTestCase

from care_im_wrapper.messaging.whatsapp import WhatsAppClient
from tests import LiveProviderCallError


class LiveCallGuardTests(SimpleTestCase):
    """The suite runs with real provider credentials in PLUGIN_CONFIGS, so an unpatched
    send is one HTTP call away from messaging a real patient. These pin the guard that
    turns that into a test failure."""

    def test_unpatched_send_raises_instead_of_reaching_the_provider(self):
        with self.assertRaises(LiveProviderCallError):
            WhatsAppClient().send_text("+919876543210", "should never leave the suite")

    def test_unpatched_template_listing_raises(self):
        with self.assertRaises(LiveProviderCallError):
            WhatsAppClient().list_templates()

    def test_non_provider_hosts_are_left_alone(self):
        import httpx

        # Not a real request: an unroutable host proves the guard delegated rather than
        # raising its own error.
        with self.assertRaises(httpx.RequestError):
            httpx.get("http://127.0.0.1:1/never-listening", timeout=0.05)
