from datetime import datetime
from datetime import timezone as dt_timezone
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from care_im_wrapper.data.base import (
    cached_fetch,
    humanize_choice,
    humanize_date,
    humanize_encounter_class,
    humanize_time,
    truncate,
)
from tests.utils import OverrideCache  # noqa: F401 # pyright: ignore


def _make_actor(user_type="staff", instance_id=1):
    return SimpleNamespace(user_type=user_type, instance=SimpleNamespace(id=instance_id))


def _make_session(patient_id="patient-1"):
    return SimpleNamespace(active_patient_external_id=patient_id)


class TruncateTests(SimpleTestCase):
    def test_short_text_returned_unchanged(self):
        self.assertEqual(truncate("short text"), "short text")

    def test_text_at_exact_limit_returned_unchanged(self):
        text = "A" * 4096
        self.assertEqual(truncate(text), text)

    def test_text_over_limit_is_truncated_with_suffix(self):
        text = "A" * 5000
        expected = "A" * (4096 - 20) + "\n... (truncated)"
        self.assertEqual(truncate(text), expected)


class HumanizeChoiceTests(SimpleTestCase):
    def test_none_returns_not_recorded(self):
        self.assertEqual(humanize_choice(None), "Not recorded")

    def test_empty_string_returns_not_recorded(self):
        self.assertEqual(humanize_choice(""), "Not recorded")

    def test_snake_case_value_is_titled(self):
        self.assertEqual(humanize_choice("in_progress"), "In Progress")

    def test_mixed_case_value_is_titled(self):
        self.assertEqual(humanize_choice("A_positive"), "A Positive")


class HumanizeEncounterClassTests(SimpleTestCase):
    def test_none_returns_not_recorded(self):
        self.assertEqual(humanize_encounter_class(None), "Not recorded")

    def test_imp_maps_to_inpatient(self):
        self.assertEqual(humanize_encounter_class("imp"), "Inpatient")

    def test_amb_maps_to_outperson(self):
        # NOTE: this is the exact current source mapping, including the "Outperson" typo.
        # Do not "fix" it here — the test documents current behavior, not intended behavior.
        self.assertEqual(humanize_encounter_class("amb"), "Outperson")

    def test_obsenc_maps_to_observation(self):
        self.assertEqual(humanize_encounter_class("obsenc"), "Observation")

    def test_emer_maps_to_emergency(self):
        self.assertEqual(humanize_encounter_class("emer"), "Emergency")

    def test_vr_maps_to_virtual(self):
        self.assertEqual(humanize_encounter_class("vr"), "Virtual")

    def test_hh_maps_to_home_health(self):
        self.assertEqual(humanize_encounter_class("hh"), "Home Health")

    def test_unknown_code_falls_back_to_titled_value(self):
        self.assertEqual(humanize_encounter_class("unknown_code"), "Unknown_Code")


@override_settings(TIME_ZONE="UTC")
class HumanizeDateTests(SimpleTestCase):
    def test_none_returns_not_recorded(self):
        self.assertEqual(humanize_date(None), "Not recorded")

    def test_string_input_returned_as_is(self):
        self.assertEqual(humanize_date("2024-01-01"), "2024-01-01")

    def test_aware_datetime_formatted_as_short_date(self):
        value = datetime(2024, 3, 5, 14, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(humanize_date(value), "05 Mar 2024")

    def test_naive_datetime_falls_back_to_str(self):
        value = datetime(2024, 3, 5, 14, 30)
        self.assertEqual(humanize_date(value), str(value))


@override_settings(TIME_ZONE="UTC")
class HumanizeTimeTests(SimpleTestCase):
    def test_none_returns_not_recorded(self):
        self.assertEqual(humanize_time(None), "Not recorded")

    def test_string_input_returned_as_is(self):
        self.assertEqual(humanize_time("14:30"), "14:30")

    def test_aware_datetime_formatted_as_short_time(self):
        value = datetime(2024, 3, 5, 14, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(humanize_time(value), "02:30 pm")

    def test_naive_datetime_falls_back_to_str(self):
        value = datetime(2024, 3, 5, 14, 30)
        self.assertEqual(humanize_time(value), str(value))


@OverrideCache
class CachedFetchTests(SimpleTestCase):
    def test_calls_underlying_function_once_and_returns_cached_result_on_second_call(self):
        call_count = []

        @cached_fetch(timeout_seconds=60)
        def fetch_fn(actor, session):
            call_count.append(1)
            return {"data": "value"}

        actor = _make_actor()
        session = _make_session()

        result1 = fetch_fn(actor, session)
        result2 = fetch_fn(actor, session)

        self.assertEqual(result1, {"data": "value"})
        self.assertEqual(result2, {"data": "value"})
        self.assertEqual(len(call_count), 1)

    def test_separate_cache_entry_per_active_patient(self):
        call_count = []

        @cached_fetch(timeout_seconds=60)
        def fetch_fn(actor, session):
            call_count.append(1)
            return "result"

        actor = _make_actor()
        session_a = _make_session(patient_id="patient-a")
        session_b = _make_session(patient_id="patient-b")

        fetch_fn(actor, session_a)
        fetch_fn(actor, session_b)

        self.assertEqual(len(call_count), 2)

    def test_separate_cache_entry_per_actor_instance_id(self):
        call_count = []

        @cached_fetch(timeout_seconds=60)
        def fetch_fn(actor, session):
            call_count.append(1)
            return "result"

        session = _make_session()
        actor_1 = _make_actor(instance_id=1)
        actor_2 = _make_actor(instance_id=2)

        fetch_fn(actor_1, session)
        fetch_fn(actor_2, session)

        self.assertEqual(len(call_count), 2)

    def test_missing_active_patient_still_caches_normally(self):
        call_count = []

        @cached_fetch(timeout_seconds=60)
        def fetch_fn(actor, session):
            call_count.append(1)
            return "result"

        actor = _make_actor()
        session = _make_session(patient_id=None)

        fetch_fn(actor, session)
        fetch_fn(actor, session)

        self.assertEqual(len(call_count), 1)
