import datetime

from care.utils.tests.base import CareAPITestBase

from care_im_wrapper.auth.resolver import resolve_phone_number

TEST_PHONE = "+919876543210"


class ResolvePhoneNumberTests(CareAPITestBase):
    def test_no_matching_patient_or_user_returns_not_found(self):
        result = resolve_phone_number(TEST_PHONE)

        self.assertFalse(result.found)
        self.assertEqual(result.identities, [])

    def test_patient_with_year_of_birth_is_included(self):
        patient = self.create_patient(
            phone_number=TEST_PHONE,
            name="Jane Doe",
            year_of_birth=1990,
            date_of_birth=None,
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertTrue(result.found)
        self.assertEqual(len(result.identities), 1)
        identity = result.identities[0]
        self.assertEqual(identity.user_type, "patient")
        self.assertEqual(identity.user_id, patient.id)
        self.assertEqual(identity.year_of_birth, 1990)
        self.assertEqual(identity.full_name, "Jane Doe")
        self.assertEqual(identity.phone_number, TEST_PHONE)

    def test_patient_with_only_date_of_birth_derives_year(self):
        self.create_patient(
            phone_number=TEST_PHONE,
            name="John Roe",
            year_of_birth=None,
            date_of_birth=datetime.date(1985, 6, 15),
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertTrue(result.found)
        self.assertEqual(len(result.identities), 1)
        self.assertEqual(result.identities[0].year_of_birth, 1985)

    def test_patient_with_no_year_of_birth_and_no_date_of_birth_is_skipped(self):
        self.create_patient(
            phone_number=TEST_PHONE,
            name="No Birth Info",
            year_of_birth=None,
            date_of_birth=None,
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertFalse(result.found)
        self.assertEqual(result.identities, [])

    def test_active_staff_user_with_date_of_birth_is_included(self):
        user = self.create_user(
            phone_number=TEST_PHONE,
            date_of_birth=datetime.date(1988, 3, 10),
            is_active=True,
            first_name="Staff",
            last_name="Member",
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertTrue(result.found)
        self.assertEqual(len(result.identities), 1)
        identity = result.identities[0]
        self.assertEqual(identity.user_type, "staff")
        self.assertEqual(identity.user_id, user.id)
        self.assertEqual(identity.year_of_birth, 1988)
        self.assertEqual(identity.full_name, user.get_full_name())
        self.assertEqual(identity.phone_number, TEST_PHONE)

    def test_inactive_staff_user_is_excluded(self):
        self.create_user(
            phone_number=TEST_PHONE,
            date_of_birth=datetime.date(1988, 3, 10),
            is_active=False,
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertFalse(result.found)
        self.assertEqual(result.identities, [])

    def test_active_staff_user_without_date_of_birth_is_skipped(self):
        self.create_user(
            phone_number=TEST_PHONE,
            date_of_birth=None,
            is_active=True,
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertFalse(result.found)
        self.assertEqual(result.identities, [])

    def test_patient_and_staff_user_with_same_phone_both_included(self):
        patient = self.create_patient(
            phone_number=TEST_PHONE,
            name="Jane Doe",
            year_of_birth=1990,
            date_of_birth=None,
        )
        user = self.create_user(
            phone_number=TEST_PHONE,
            date_of_birth=datetime.date(1988, 3, 10),
            is_active=True,
        )

        result = resolve_phone_number(TEST_PHONE)

        self.assertTrue(result.found)
        self.assertEqual(len(result.identities), 2)
        user_types = [identity.user_type for identity in result.identities]
        self.assertEqual(user_types, ["patient", "staff"])
        self.assertEqual(result.identities[0].user_id, patient.id)
        self.assertEqual(result.identities[1].user_id, user.id)
