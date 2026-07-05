from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.data.medications import _extract_medication_name


class ExtractMedicationNameTests(SimpleTestCase):
    def test_medication_dict_with_display_key_returns_display(self):
        med = SimpleNamespace(medication={"display": "Paracetamol 500mg"})
        self.assertEqual(_extract_medication_name(med), "Paracetamol 500mg")

    def test_medication_dict_with_text_key_returns_text_when_no_display(self):
        med = SimpleNamespace(medication={"text": "Ibuprofen 200mg"})
        self.assertEqual(_extract_medication_name(med), "Ibuprofen 200mg")

    def test_medication_dict_prefers_display_over_text(self):
        med = SimpleNamespace(medication={"display": "Display Name", "text": "Text Name"})
        self.assertEqual(_extract_medication_name(med), "Display Name")

    def test_medication_dict_without_display_or_text_falls_back_to_requested_product(self):
        med = SimpleNamespace(
            medication={"code": "12345"},
            requested_product=SimpleNamespace(name="Amoxicillin"),
        )
        self.assertEqual(_extract_medication_name(med), "Amoxicillin")

    def test_requested_product_used_when_medication_is_none(self):
        med = SimpleNamespace(medication=None, requested_product=SimpleNamespace(name="Metformin"))
        self.assertEqual(_extract_medication_name(med), "Metformin")

    def test_medication_dict_without_display_text_or_requested_product_falls_back_to_str_dict(self):
        med = SimpleNamespace(medication={"code": "12345"}, requested_product=None)
        result = _extract_medication_name(med)
        self.assertTrue(result.startswith("Medication ("))
        self.assertIn("12345", result)

    def test_no_medication_and_no_requested_product_returns_unknown_medication(self):
        med = SimpleNamespace(medication=None, requested_product=None)
        self.assertEqual(_extract_medication_name(med), "Unknown medication")

    def test_medication_as_plain_string_without_requested_product_returns_string(self):
        med = SimpleNamespace(medication="Aspirin", requested_product=None)
        self.assertEqual(_extract_medication_name(med), "Aspirin")

    def test_missing_medication_attribute_entirely_returns_unknown_medication(self):
        med = SimpleNamespace(requested_product=None)
        self.assertEqual(_extract_medication_name(med), "Unknown medication")

    def test_missing_requested_product_attribute_entirely_with_medication_dict(self):
        med = SimpleNamespace(medication={"display": "Cetirizine"})
        self.assertEqual(_extract_medication_name(med), "Cetirizine")
