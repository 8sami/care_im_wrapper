"""Database-level behaviour of fetch_prescriptions."""

from types import SimpleNamespace

from care.utils.tests.base import CareAPITestBase
from django.db import connection
from django.test.utils import CaptureQueriesContext

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.medications import fetch_prescriptions
from care_im_wrapper.models import ConversationSession

TABLET = {"code": "{tbl}", "display": "tablets", "system": "http://unitsofmeasure.org"}


def _instruction(dose=1, days=5, man="1-0-1"):
    return {
        "text": man,
        "as_needed_boolean": False,
        "dose_and_rate": {"type": "ordered", "dose_quantity": {"value": dose, "unit": TABLET}},
        "timing": {
            "repeat": {"frequency": 2, "period": 1, "period_unit": "d", "bounds_duration": {"value": days, "unit": "d"}}
        },
        "route": {"code": "26643006", "system": "http://snomed.info/sct", "display": "Oral route"},
    }


class FetchPrescriptionsTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(first_name="Ada", last_name="Lovelace")
        self.patient = self.create_patient()
        self.facility = self.create_facility(user=self.user)
        self.organization = self.create_facility_organization(facility=self.facility)
        self.encounter = self.create_encounter(
            patient=self.patient, facility=self.facility, organization=self.organization
        )

    def _prescription(self, **kwargs):
        from care.emr.models.medication_request import MedicationRequestPrescription

        data = {
            "encounter": self.encounter,
            "patient": self.patient,
            "status": "active",
            "name": "Discharge medications",
            "prescribed_by": self.user,
        }
        data.update(kwargs)
        return MedicationRequestPrescription.objects.create(**data)

    def _medication(self, prescription, **kwargs):
        from care.emr.models.medication_request import MedicationRequest

        data = {
            "patient": self.patient,
            "encounter": self.encounter,
            "prescription": prescription,
            "do_not_perform": False,
            "status": "active",
            "intent": "order",
            "medication": {"display": "Test Medication"},
            "dosage_instruction": [_instruction()],
            "note": None,
        }
        data.update(kwargs)
        return MedicationRequest.objects.create(**data)

    def _session(self, active_patient_external_id=None):
        return SimpleNamespace(active_patient_external_id=active_patient_external_id, data_offsets=[], data_shown=0)

    def _patient_actor(self):
        return Actor(user_type=ConversationSession.UserType.PATIENT.value, instance=self.patient)

    def _fetch(self, actor=None, session=None):
        return fetch_prescriptions.__wrapped__(actor or self._patient_actor(), session or self._session())

    def test_no_prescriptions_raises_no_data_error(self):
        with self.assertRaises(NoDataError):
            self._fetch()

    def test_prescription_carries_its_prescriber_facility_and_medications(self):
        prescription = self._prescription(note="Complete the full course")
        self._medication(prescription, medication={"display": "Paracetamol 500mg"})

        page = self._fetch()

        self.assertEqual(len(page.records), 1)
        record = page.records[0]
        self.assertEqual(record.name, "Discharge medications")
        self.assertEqual(record.status, "Active")
        self.assertEqual(record.prescribed_by, "Ada Lovelace")
        self.assertEqual(record.facility, self.facility.name)
        self.assertEqual(record.note, "Complete the full course")
        self.assertEqual(len(record.medications), 1)
        self.assertEqual(record.medications[0].name, "Paracetamol 500mg")

    def test_medications_are_grouped_under_their_own_prescription(self):
        first = self._prescription(name="First")
        second = self._prescription(name="Second")
        self._medication(first, medication={"display": "Med A"})
        self._medication(second, medication={"display": "Med B"})
        self._medication(second, medication={"display": "Med C"})

        page = self._fetch()

        by_name = {p.name: p for p in page.records}
        self.assertEqual([m.name for m in by_name["First"].medications], ["Med A"])
        self.assertEqual([m.name for m in by_name["Second"].medications], ["Med B", "Med C"])

    def test_newest_group_comes_first(self):
        older = self._prescription(name="Older")
        self._medication(older, medication={"display": "Old med"})
        newer = self._prescription(name="Newer")
        self._medication(newer, medication={"display": "New med"})

        page = self._fetch()

        self.assertEqual([p.name for p in page.records], ["Newer", "Older"])

    def test_entered_in_error_prescription_hides_its_medications(self):
        """Filtering the medication's own status would not catch this."""
        real = self._prescription(name="Real")
        self._medication(real, medication={"display": "Kept"})
        mistake = self._prescription(name="Mistake", status="entered_in_error")
        self._medication(mistake, medication={"display": "Hidden"})

        page = self._fetch()

        self.assertEqual([p.name for p in page.records], ["Real"])
        self.assertEqual([m.name for m in page.records[0].medications], ["Kept"])

    def test_entered_in_error_medication_is_excluded(self):
        prescription = self._prescription()
        self._medication(prescription, medication={"display": "Kept"})
        self._medication(prescription, medication={"display": "Dropped"}, status="entered_in_error")

        page = self._fetch()

        self.assertEqual([m.name for m in page.records[0].medications], ["Kept"])

    def test_inactive_medication_is_listed_and_flagged(self):
        """care_fe keeps inactive medications visible but dims them, so the record carries."""
        prescription = self._prescription()
        self._medication(prescription, medication={"display": "Finished"}, status="completed")

        medication = self._fetch().records[0].medications[0]

        self.assertTrue(medication.is_inactive)
        self.assertEqual(medication.status, "Completed")

    def test_active_medication_is_not_flagged_inactive(self):
        prescription = self._prescription()
        self._medication(prescription, medication={"display": "Ongoing"}, status="active")

        self.assertFalse(self._fetch().records[0].medications[0].is_inactive)

    def test_prescription_with_no_medications_is_not_shown(self):
        """The query is driven off MedicationRequest, so an empty prescription has nothing."""
        self._prescription(name="Empty")

        with self.assertRaises(NoDataError):
            self._fetch()

    def test_medications_without_a_prescription_are_still_listed(self):
        """`prescription` is nullable with SET_NULL, and a questionnaire- or fixture-created."""
        self._medication(None, medication={"display": "Standalone"})

        page = self._fetch()

        self.assertEqual(len(page.records), 1)
        group = page.records[0]
        self.assertIsNone(group.name)
        self.assertEqual(group.status, "")
        self.assertEqual([m.name for m in group.medications], ["Standalone"])

    def test_prescribed_and_standalone_medications_form_separate_groups(self):
        prescription = self._prescription(name="On a prescription")
        self._medication(prescription, medication={"display": "Grouped"})
        self._medication(None, medication={"display": "Standalone"})

        page = self._fetch()

        self.assertEqual(len(page.records), 2)
        named = {p.name: p for p in page.records}
        self.assertEqual([m.name for m in named["On a prescription"].medications], ["Grouped"])
        self.assertEqual([m.name for m in named[None].medications], ["Standalone"])

    def test_standalone_group_falls_back_to_the_requester_as_prescriber(self):
        self._medication(None, medication={"display": "Standalone"}, requester=self.user)

        self.assertEqual(self._fetch().records[0].prescribed_by, "Ada Lovelace")

    def test_a_tapered_medication_keeps_one_line_per_instruction(self):
        prescription = self._prescription()
        self._medication(
            prescription,
            medication={"display": "Prednisolone"},
            dosage_instruction=[_instruction(dose=2, days=3), _instruction(dose=1, days=4)],
        )

        lines = self._fetch().records[0].medications[0].lines

        self.assertEqual(len(lines), 2)
        self.assertEqual((lines[0].dosage, lines[0].duration), ("2 tablets", "3 days"))
        self.assertEqual((lines[1].dosage, lines[1].duration), ("1 tablets", "4 days"))

    def test_another_patients_prescriptions_are_not_returned(self):
        other = self.create_patient()
        other_encounter = self.create_encounter(patient=other, facility=self.facility, organization=self.organization)
        mine = self._prescription(name="Mine")
        self._medication(mine, medication={"display": "Mine"})
        theirs = self._prescription(name="Theirs", patient=other, encounter=other_encounter)
        self._medication(theirs, patient=other, encounter=other_encounter, medication={"display": "Theirs"})

        page = self._fetch()

        self.assertEqual([p.name for p in page.records], ["Mine"])

    def test_grouping_medications_does_not_scale_with_prescription_count(self):
        """Grouping must not become an N+1."""
        for index in range(4):
            prescription = self._prescription(name=f"P{index}")
            self._medication(prescription)
            self._medication(prescription)

        with CaptureQueriesContext(connection) as ctx:
            page = self._fetch()
            self.assertEqual(len(page.records), 4)

        self.assertLessEqual(len(ctx.captured_queries), 3)
