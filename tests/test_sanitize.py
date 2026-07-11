from django.test import SimpleTestCase

from care_im_wrapper.core.sanitize import mask_phone_number, normalize_phone_number


class NormalizePhoneNumberTests(SimpleTestCase):
    def test_empty_string_returns_empty(self):
        self.assertEqual(normalize_phone_number(""), "")

    def test_strips_spaces_and_dashes(self):
        self.assertEqual(normalize_phone_number("+91 987-654-3210"), "+919876543210")

    def test_strips_letters(self):
        self.assertEqual(normalize_phone_number("+91abc9876543210"), "+919876543210")

    def test_plus_only_in_middle_is_kept_too(self):
        self.assertEqual(normalize_phone_number("91+9876543210"), "91+9876543210")


class MaskPhoneNumberTests(SimpleTestCase):
    def test_short_number_under_5_chars_returned_unmasked(self):
        self.assertEqual(mask_phone_number("+91"), "+91")

    def test_length_equal_to_prefix_plus_suffix_returned_unmasked(self):
        # prefix_len=4 + suffix_len=3 = 7
        self.assertEqual(mask_phone_number("1234567"), "1234567")

    def test_standard_indian_mobile_masked(self):
        # input "+919876543210" (len 13)
        # prefix = "+919" (4), suffix = "210" (3)
        # mask = "*" * (13 - 4 - 3) = "******" (6)
        self.assertEqual(mask_phone_number("+919876543210"), "+919******210")
