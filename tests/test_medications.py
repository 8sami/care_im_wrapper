"""Dosage formatters, checked against care_fe's own behaviour."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from care_im_wrapper.data.medications import (
    _additional_instructions,
    _format_dosage,
    _format_duration,
    _format_frequency,
    _format_sig,
    _is_non_unit_dose,
    build_dosage_lines,
    display_medication_name,
    format_user_name,
)

TABLET = {"code": "{tbl}", "display": "tablets", "system": "http://unitsofmeasure.org"}
MG = {"code": "mg", "display": "milligram"}


def quantity(value, unit=TABLET):
    return {"value": value, "unit": unit}


class DisplayMedicationNameTests(SimpleTestCase):
    """care_fe displayMedicationName: medication.display, else requested_product.name."""

    def test_prefers_medication_display(self):
        med = SimpleNamespace(medication={"display": "Paracetamol 500mg"}, requested_product=None)
        self.assertEqual(display_medication_name(med), "Paracetamol 500mg")

    def test_display_wins_over_requested_product(self):
        med = SimpleNamespace(
            medication={"display": "Display Name"},
            requested_product=SimpleNamespace(name="Product Name"),
        )
        self.assertEqual(display_medication_name(med), "Display Name")

    def test_falls_back_to_requested_product_name(self):
        med = SimpleNamespace(medication={}, requested_product=SimpleNamespace(name="Amoxicillin"))
        self.assertEqual(display_medication_name(med), "Amoxicillin")

    def test_requested_product_used_when_medication_is_none(self):
        med = SimpleNamespace(medication=None, requested_product=SimpleNamespace(name="Metformin"))
        self.assertEqual(display_medication_name(med), "Metformin")

    def test_coding_text_is_the_last_labelled_source(self):
        med = SimpleNamespace(medication={"text": "Cetirizine"}, requested_product=None)
        self.assertEqual(display_medication_name(med), "Cetirizine")

    def test_nothing_nameable_falls_back_to_a_label_not_a_blank(self):
        """care_fe returns "" here; a chat bullet needs something to name."""
        med = SimpleNamespace(medication=None, requested_product=None)
        self.assertEqual(display_medication_name(med), "Unknown medication")


class FormatDosageTests(SimpleTestCase):
    """care_fe formatDosage."""

    def test_dose_quantity_with_unit(self):
        self.assertEqual(_format_dosage({"dose_and_rate": {"dose_quantity": quantity(500, MG)}}), "500 milligram")

    def test_trailing_zeros_are_trimmed(self):
        self.assertEqual(_format_dosage({"dose_and_rate": {"dose_quantity": quantity("500.00", MG)}}), "500 milligram")

    def test_dose_range_renders_both_ends(self):
        inst = {"dose_and_rate": {"dose_range": {"low": quantity(2), "high": quantity(1)}}}
        self.assertEqual(_format_dosage(inst), "2 tablets -> 1 tablets")

    def test_no_dose_and_rate_is_empty(self):
        self.assertIsNone(_format_dosage({}))

    def test_dose_quantity_without_value_is_empty(self):
        self.assertIsNone(_format_dosage({"dose_and_rate": {"dose_quantity": {"unit": TABLET}}}))


class IsNonUnitDoseTests(SimpleTestCase):
    """care_fe isNonUnitDose -- what it highlights."""

    def test_dose_range_is_always_non_unit(self):
        inst = {"dose_and_rate": {"dose_range": {"low": quantity(1), "high": quantity(2)}}}
        self.assertTrue(_is_non_unit_dose(inst))

    def test_single_unit_is_not_highlighted(self):
        self.assertFalse(_is_non_unit_dose({"dose_and_rate": {"dose_quantity": quantity(1)}}))

    def test_two_units_is_highlighted(self):
        self.assertTrue(_is_non_unit_dose({"dose_and_rate": {"dose_quantity": quantity(2)}}))

    def test_half_a_unit_is_highlighted(self):
        self.assertTrue(_is_non_unit_dose({"dose_and_rate": {"dose_quantity": quantity("0.5")}}))

    def test_missing_dose_is_not_highlighted(self):
        self.assertFalse(_is_non_unit_dose({}))


class FormatFrequencyTests(SimpleTestCase):
    """care_fe formatFrequency -> getFrequencyDisplayLabel."""

    def test_prn_reads_as_sos(self):
        self.assertEqual(_format_frequency({"as_needed_boolean": True}), "SOS")

    def test_prn_with_reason(self):
        inst = {"as_needed_boolean": True, "as_needed_for": {"display": "Pain"}}
        self.assertEqual(_format_frequency(inst), "SOS (Pain)")

    def test_preset_man_patterns_are_labelled(self):
        for man, label in (
            ("1-0-1", "Twice a day"),
            ("1-1-1", "Thrice a day"),
            ("1-0-0", "Morning only"),
            ("0-0-1", "Night only"),
            ("0-1-0", "Noon only"),
            ("1-1-0", "Morning & Noon"),
            ("0-1-1", "Noon & Night"),
            ("1-1-1-1", "Four times a day"),
        ):
            with self.subTest(man=man):
                self.assertEqual(_format_frequency({"text": man}), f"{man} ({label})")

    def test_non_preset_dash_pattern_is_verbatim(self):
        """care_fe only labels its eight presets. Counting non-zero slots would call this."""
        self.assertEqual(_format_frequency({"text": "2-2-2"}), "2-2-2")

    def test_all_zero_pattern_is_verbatim(self):
        self.assertEqual(_format_frequency({"text": "0-0-0"}), "0-0-0")

    def test_free_text_is_verbatim(self):
        self.assertEqual(_format_frequency({"text": "Weekly"}), "Weekly")

    def test_timing_code_resolving_to_a_man_preset(self):
        for code, expected in (
            ("BID", "1-0-1 (Twice a day)"),
            ("TID", "1-1-1 (Thrice a day)"),
            ("QID", "1-1-1-1 (Four times a day)"),
            ("AM", "1-0-0 (Morning only)"),
            ("PM", "0-0-1 (Night only)"),
            ("NOON", "0-1-0 (Noon only)"),
        ):
            with self.subTest(code=code):
                self.assertEqual(_format_frequency({"timing": {"code": {"code": code}}}), expected)

    def test_timing_code_without_a_man_preset_uses_its_display(self):
        for code, expected in (
            ("QD", "QD (Once a day)"),
            ("QOD", "QOD (Alternate days)"),
            ("HS", "HS (At bedtime)"),
            ("AC", "AC (Before meals)"),
            ("PC", "PC (After meals)"),
            ("STAT", "STAT (Immediately)"),
            ("WK", "WK (Weekly)"),
            ("MO", "MO (Monthly)"),
            ("Q1H", "Q1H (Every 1 hour)"),
            ("Q2H", "Q2H (Every 2 hours)"),
            ("Q3H", "Q3H (Every 3 hours)"),
            ("Q4H", "Q4H (Every 4 hours)"),
            ("Q6H", "Q6H (Every 6 hours)"),
            ("Q8H", "Q8H (Every 8 hours)"),
            ("Q12H", "Q12H (Every 12 hours)"),
        ):
            with self.subTest(code=code):
                self.assertEqual(_format_frequency({"timing": {"code": {"code": code}}}), expected)

    def test_unknown_timing_code_is_returned_bare(self):
        self.assertEqual(_format_frequency({"timing": {"code": {"code": "XYZ"}}}), "XYZ")

    def test_text_takes_priority_over_timing_code(self):
        inst = {"text": "1-0-1", "timing": {"code": {"code": "STAT"}}}
        self.assertEqual(_format_frequency(inst), "1-0-1 (Twice a day)")

    def test_prn_takes_priority_over_text(self):
        """care_fe's formatFrequency short-circuits on as_needed_boolean before consulting."""
        self.assertEqual(_format_frequency({"as_needed_boolean": True, "text": "1-0-1"}), "SOS")

    def test_nothing_recorded_is_empty(self):
        self.assertIsNone(_format_frequency({"as_needed_boolean": False}))


class FormatDurationTests(SimpleTestCase):
    """care_fe getTimingBounds + formatTimingBounds + formatDurationLabel."""

    def _repeat(self, **repeat):
        return {"timing": {"repeat": repeat}}

    def test_duration_unit_is_humanised(self):
        self.assertEqual(_format_duration(self._repeat(bounds_duration={"value": "5", "unit": "d"})), "5 days")

    def test_singular_duration(self):
        self.assertEqual(_format_duration(self._repeat(bounds_duration={"value": "1", "unit": "d"})), "1 day")

    def test_every_ucum_unit_has_a_label(self):
        for unit, expected in (
            ("d", "2 days"),
            ("h", "2 hours"),
            ("wk", "2 weeks"),
            ("mo", "2 months"),
            ("a", "2 years"),
        ):
            with self.subTest(unit=unit):
                self.assertEqual(_format_duration(self._repeat(bounds_duration={"value": "2", "unit": unit})), expected)

    def test_zero_duration_counts_as_none(self):
        self.assertIsNone(_format_duration(self._repeat(bounds_duration={"value": "0", "unit": "d"})))

    def test_unknown_unit_falls_back_to_the_raw_code(self):
        self.assertEqual(_format_duration(self._repeat(bounds_duration={"value": "3", "unit": "zz"})), "3 zz")

    def test_bounds_range(self):
        bounds = {"low": {"value": "5", "unit": "d"}, "high": {"value": "7", "unit": "d"}}
        self.assertEqual(_format_duration(self._repeat(bounds_range=bounds)), "5–7 days")

    def test_bounds_period_uses_readable_dates(self):
        bounds = {"start": "2026-06-01", "end": "2026-06-08"}
        self.assertEqual(_format_duration(self._repeat(bounds_period=bounds)), "01 Jun 2026 → 08 Jun 2026")

    def test_range_is_preferred_over_duration(self):
        """care_fe getTimingBounds checks range, then period, then duration."""
        inst = self._repeat(
            bounds_range={"low": {"value": "5", "unit": "d"}, "high": {"value": "7", "unit": "d"}},
            bounds_duration={"value": "3", "unit": "d"},
        )
        self.assertEqual(_format_duration(inst), "5–7 days")

    def test_no_bounds_is_empty(self):
        self.assertIsNone(_format_duration({"timing": {"repeat": {}}}))
        self.assertIsNone(_format_duration({}))


class FormatSigTests(SimpleTestCase):
    """care_fe formatSig: route, method, site."""

    def test_route_only(self):
        self.assertEqual(_format_sig({"route": {"display": "Oral route"}}), "Via Oral route")

    def test_route_method_and_site(self):
        inst = {
            "route": {"display": "Oral route"},
            "method": {"display": "Swallow"},
            "site": {"display": "left deltoid"},
        }
        self.assertEqual(_format_sig(inst), "Via Oral route by Swallow to left deltoid")

    def test_method_without_route(self):
        self.assertEqual(_format_sig({"method": {"display": "Injection"}}), "by Injection")

    def test_nothing_recorded_is_empty(self):
        self.assertIsNone(_format_sig({}))


class AdditionalInstructionsTests(SimpleTestCase):
    def test_patient_instruction_comes_first(self):
        inst = {"patient_instruction": "Take with food", "additional_instruction": [{"display": "Avoid alcohol"}]}
        self.assertEqual(_additional_instructions(inst), ("Take with food", "Avoid alcohol"))

    def test_multiple_additional_instructions_are_kept_separate(self):
        inst = {"additional_instruction": [{"display": "A"}, {"display": "B"}]}
        self.assertEqual(_additional_instructions(inst), ("A", "B"))

    def test_entries_without_a_display_are_skipped(self):
        self.assertEqual(_additional_instructions({"additional_instruction": [{}, {"display": "A"}]}), ("A",))

    def test_none_recorded(self):
        self.assertEqual(_additional_instructions({}), ())


class BuildDosageLinesTests(SimpleTestCase):
    """One line per dosage_instruction -- never collapsed."""

    def test_a_tapered_course_keeps_each_dose_with_its_own_duration(self):
        med = SimpleNamespace(
            dosage_instruction=[
                {
                    "text": "1-0-1",
                    "dose_and_rate": {"dose_quantity": quantity(2)},
                    "timing": {"repeat": {"bounds_duration": {"value": "3", "unit": "d"}}},
                },
                {
                    "text": "1-0-1",
                    "dose_and_rate": {"dose_quantity": quantity(1)},
                    "timing": {"repeat": {"bounds_duration": {"value": "4", "unit": "d"}}},
                },
            ]
        )

        lines = build_dosage_lines(med)

        self.assertEqual(len(lines), 2)
        self.assertEqual((lines[0].dosage, lines[0].duration), ("2 tablets", "3 days"))
        self.assertEqual((lines[1].dosage, lines[1].duration), ("1 tablets", "4 days"))

    def test_non_dict_entries_are_skipped(self):
        med = SimpleNamespace(dosage_instruction=["nope", {"text": "1-0-1"}])

        lines = build_dosage_lines(med)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].frequency, "1-0-1 (Twice a day)")

    def test_legacy_string_becomes_a_free_text_sig_rather_than_being_dropped(self):
        med = SimpleNamespace(dosage_instruction="As directed by physician")

        lines = build_dosage_lines(med)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].sig, "As directed by physician")
        self.assertEqual(lines[0].dosage, "")

    def test_no_instructions_yields_no_lines(self):
        self.assertEqual(build_dosage_lines(SimpleNamespace(dosage_instruction=None)), ())
        self.assertEqual(build_dosage_lines(SimpleNamespace(dosage_instruction=[])), ())


class FormatUserNameTests(SimpleTestCase):
    """care_fe formatName."""

    def test_prefix_first_last_suffix(self):
        user = SimpleNamespace(prefix="Dr.", first_name="Ada", last_name="Lovelace", suffix="MD", username="ada")
        self.assertEqual(format_user_name(user), "Dr. Ada Lovelace MD")

    def test_first_and_last_only(self):
        user = SimpleNamespace(prefix=None, first_name="Ada", last_name="Lovelace", suffix=None, username="ada")
        self.assertEqual(format_user_name(user), "Ada Lovelace")

    def test_falls_back_to_username(self):
        user = SimpleNamespace(prefix=None, first_name="", last_name="", suffix=None, username="ada")
        self.assertEqual(format_user_name(user), "ada")

    def test_no_user(self):
        self.assertIsNone(format_user_name(None))
