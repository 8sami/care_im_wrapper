import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from care_im_wrapper.models import DocumentLink, DocumentLinkObjectKind
from care_im_wrapper.settings import plugin_settings


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


class DocumentLinkTokenTests(TestCase):
    def test_save_auto_generates_a_token_when_blank(self):
        link = _make_link()
        self.assertTrue(link.token)
        self.assertGreaterEqual(len(link.token), 32)

    def test_save_does_not_overwrite_an_existing_token(self):
        link = _make_link()
        original_token = link.token
        link.access_count += 1
        link.save(update_fields=["access_count"])
        self.assertEqual(link.token, original_token)

    def test_token_is_unique(self):
        first = _make_link()
        second = _make_link()
        self.assertNotEqual(first.token, second.token)


class DocumentLinkIsValidTests(TestCase):
    def test_is_valid_true_before_expiry(self):
        link = _make_link(expires_at=timezone.now() + timedelta(hours=1))
        self.assertTrue(link.is_valid())

    def test_is_valid_false_after_expiry(self):
        link = _make_link(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(link.is_valid())

    def test_is_valid_false_when_soft_deleted(self):
        link = _make_link()
        link.deleted = True
        self.assertFalse(link.is_valid())

    def test_default_manager_excludes_soft_deleted_rows(self):
        link = _make_link()
        link.delete()  # BaseModel.delete() soft-deletes
        self.assertFalse(DocumentLink.objects.filter(pk=link.pk).exists())


class DocumentLinkResolveFileObjectTests(TestCase):
    def test_report_upload_kind_resolves_via_report_upload_model(self):
        link = _make_link(object_kind=DocumentLinkObjectKind.REPORT_UPLOAD)
        fake_report_upload = MagicMock()

        with patch("care.emr.models.report.report_upload.ReportUpload.objects") as mock_manager:
            mock_manager.get.return_value = fake_report_upload
            result = link.resolve_file_object()

        mock_manager.get.assert_called_once_with(external_id=link.object_external_id)
        self.assertIs(result, fake_report_upload)

    def test_file_upload_kind_resolves_via_file_upload_model(self):
        link = _make_link(object_kind=DocumentLinkObjectKind.FILE_UPLOAD)
        fake_file_upload = MagicMock()

        with patch("care.emr.models.file_upload.FileUpload.objects") as mock_manager:
            mock_manager.get.return_value = fake_file_upload
            result = link.resolve_file_object()

        mock_manager.get.assert_called_once_with(external_id=link.object_external_id)
        self.assertIs(result, fake_file_upload)


class DocumentLinkMintReadUrlTests(TestCase):
    def test_mint_read_url_uses_configured_presign_ttl(self):
        link = _make_link()
        fake_file_object = MagicMock()
        fake_file_object.files_manager.read_signed_url.return_value = "https://example.com/signed"

        with patch.object(DocumentLink, "resolve_file_object", return_value=fake_file_object):
            url = link.mint_read_url()

        fake_file_object.files_manager.read_signed_url.assert_called_once_with(
            fake_file_object, duration=int(plugin_settings.DOCUMENT_PRESIGN_TTL_SECONDS)
        )
        self.assertEqual(url, "https://example.com/signed")
