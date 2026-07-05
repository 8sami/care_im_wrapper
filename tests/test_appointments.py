from datetime import datetime
from datetime import timezone as dt_timezone
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from care_im_wrapper.data.appointments import _extract_booking_info


def _make_slot(start=None, end=None, resource=None, has_start_datetime=True):
    if not has_start_datetime:
        slot = SimpleNamespace(resource=resource)
        return slot
    return SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)


@override_settings(TIME_ZONE="UTC")
class ExtractBookingInfoTests(SimpleTestCase):
    def _valid_times(self):
        start = datetime(2024, 3, 5, 9, 0, tzinfo=dt_timezone.utc)
        end = datetime(2024, 3, 5, 9, 30, tzinfo=dt_timezone.utc)
        return start, end

    def test_location_resource_type_uses_location_name(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type="location",
            location=SimpleNamespace(name="Ward A"),
            user=None,
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertIsNotNone(record)
        self.assertEqual(record.location, "Ward A")

    def test_healthcare_service_resource_type_uses_service_name(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type="healthcare_service",
            healthcare_service=SimpleNamespace(name="Cardiology OPD"),
            user=None,
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.location, "Cardiology OPD")

    def test_other_resource_type_falls_back_to_facility_name(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type="user",
            facility=SimpleNamespace(name="City Hospital"),
            user=None,
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.location, "City Hospital")

    def test_no_resource_type_falls_back_to_facility_name(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type=None,
            facility=SimpleNamespace(name="City Hospital"),
            user=None,
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.location, "City Hospital")

    def test_no_resource_at_all_returns_unknown_location(self):
        start, end = self._valid_times()
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=None)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.location, "Unknown")

    def test_practitioner_uses_first_and_last_name_stripped(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type="user",
            facility=None,
            user=SimpleNamespace(first_name="Jane", last_name="Doe"),
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.practitioner, "Jane Doe")

    def test_practitioner_with_no_user_is_unknown(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(resource_type="user", facility=None, user=None)
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.practitioner, "Unknown")

    def test_practitioner_with_empty_names_is_unknown(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type="user",
            facility=None,
            user=SimpleNamespace(first_name="", last_name=""),
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.practitioner, "Unknown")

    def test_missing_slot_returns_none(self):
        booking = SimpleNamespace(token_slot=None, status="booked")

        record = _extract_booking_info(booking)

        self.assertIsNone(record)

    def test_slot_without_start_datetime_attribute_returns_none(self):
        slot = _make_slot(resource=None, has_start_datetime=False)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertIsNone(record)

    def test_full_valid_booking_returns_complete_record(self):
        start, end = self._valid_times()
        resource = SimpleNamespace(
            resource_type="location",
            location=SimpleNamespace(name="Ward A"),
            user=SimpleNamespace(first_name="Jane", last_name="Doe"),
        )
        slot = SimpleNamespace(start_datetime=start, end_datetime=end, resource=resource)
        booking = SimpleNamespace(token_slot=slot, status="booked")

        record = _extract_booking_info(booking)

        self.assertEqual(record.practitioner, "Jane Doe")
        self.assertEqual(record.location, "Ward A")
        self.assertEqual(record.status, "Booked")
        self.assertEqual(record.date, "05 Mar 2024")
        self.assertEqual(record.time_slot, "09:00 am - 09:30 am")
