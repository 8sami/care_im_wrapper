import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from care_im_wrapper.models import DocumentLink, DocumentLinkObjectKind
from tests.utils import override_test_cache


def _url(token: str) -> str:
    return reverse("im-wrapper-document-redirect", kwargs={"token": token})


def _make_link(**kwargs):
    data = {
        "object_kind": DocumentLinkObjectKind.FILE_UPLOAD,
        "object_external_id": uuid.uuid4(),
        "document_type": "diagnostic_report",
        "patient_external_id": uuid.uuid4(),
        "provider": "whatsapp",
        "expires_at": timezone.now() + timedelta(hours=1),
    }
    data.update(kwargs)
    return DocumentLink.objects.create(**data)


@override_test_cache()
class DocumentRedirectTests(TestCase):
    def test_unknown_token_returns_404(self):
        response = self.client.get(_url("does-not-exist"))
        self.assertEqual(response.status_code, 404)

    def test_expired_token_returns_404(self):
        link = _make_link(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(_url(link.token))
        self.assertEqual(response.status_code, 404)

    def test_soft_deleted_token_returns_404(self):
        link = _make_link()
        link.delete()
        response = self.client.get(_url(link.token))
        self.assertEqual(response.status_code, 404)

    def test_valid_token_redirects_to_fresh_presign_and_increments_access_count(self):
        link = _make_link()

        with patch.object(DocumentLink, "mint_read_url", return_value="https://example.com/signed-pdf"):
            response = self.client.get(_url(link.token))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://example.com/signed-pdf")

        link.refresh_from_db()
        self.assertEqual(link.access_count, 1)

    def test_repeated_valid_access_increments_access_count_each_time(self):
        link = _make_link()

        with patch.object(DocumentLink, "mint_read_url", return_value="https://example.com/signed-pdf"):
            self.client.get(_url(link.token))
            self.client.get(_url(link.token))

        link.refresh_from_db()
        self.assertEqual(link.access_count, 2)

    def test_mint_read_url_failure_returns_404_not_a_500(self):
        link = _make_link()

        with patch.object(DocumentLink, "mint_read_url", side_effect=RuntimeError("s3 unreachable")):
            response = self.client.get(_url(link.token))

        self.assertEqual(response.status_code, 404)

    def test_rate_limited_client_gets_429_before_token_is_even_looked_up(self):
        link = _make_link()

        with (
            patch("care_im_wrapper.documents.views.is_rate_limited", return_value=True) as mock_rate_limited,
            patch.object(DocumentLink, "mint_read_url") as mock_mint,
        ):
            response = self.client.get(_url(link.token))

        self.assertEqual(response.status_code, 429)
        mock_rate_limited.assert_called_once()
        mock_mint.assert_not_called()

    def test_unknown_and_expired_tokens_are_indistinguishable(self):
        expired_link = _make_link(expires_at=timezone.now() - timedelta(seconds=1))

        unknown_response = self.client.get(_url("totally-made-up-token"))
        expired_response = self.client.get(_url(expired_link.token))

        self.assertEqual(unknown_response.status_code, expired_response.status_code)
        self.assertEqual(unknown_response.content, expired_response.content)
