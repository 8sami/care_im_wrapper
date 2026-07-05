from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.data.lab_reports import _extract_report_name


class ExtractReportNameTests(SimpleTestCase):
    def test_code_dict_with_text_returns_text(self):
        report = SimpleNamespace(code={"text": "Complete Blood Count"})
        self.assertEqual(_extract_report_name(report), "Complete Blood Count")

    def test_code_dict_with_display_only_returns_display(self):
        report = SimpleNamespace(code={"display": "Liver Function Test"})
        self.assertEqual(_extract_report_name(report), "Liver Function Test")

    def test_code_dict_prefers_text_over_display(self):
        # NOTE: unlike procedures._extract_service_name, this prefers "text" first,
        # then "display" — the exact opposite order. Do not assume they match.
        report = SimpleNamespace(code={"text": "Text Name", "display": "Display Name"})
        self.assertEqual(_extract_report_name(report), "Text Name")

    def test_code_dict_without_text_or_display_returns_lab_report(self):
        report = SimpleNamespace(code={"system": "http://loinc.org"})
        self.assertEqual(_extract_report_name(report), "Lab report")

    def test_code_as_plain_string_returns_string(self):
        report = SimpleNamespace(code="HbA1c")
        self.assertEqual(_extract_report_name(report), "HbA1c")

    def test_code_none_returns_lab_report(self):
        report = SimpleNamespace(code=None)
        self.assertEqual(_extract_report_name(report), "Lab report")

    def test_missing_code_attribute_returns_lab_report(self):
        report = SimpleNamespace()
        self.assertEqual(_extract_report_name(report), "Lab report")
