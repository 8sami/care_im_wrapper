import secrets
from datetime import timedelta
from secrets import choice

from care.emr.resources.encounter.constants import StatusChoices
from care.emr.resources.location.spec import (
    FacilityLocationFormChoices,
    FacilityLocationModeChoices,
)
from care.emr.resources.organization.spec import OrganizationTypeChoices
from care.fixtures.constants import (
    DEFAULT_AVAILABILITY,
    FACILITY_DEPARTMENTS,
    INVENTORY_ITEMS,
    LAB_TESTS,
    MANAGING_ORG_USERS,
    HealthcareServiceInternalType,
)
from care.fixtures.context import care_fixture_context
from django.utils import timezone


def log(message):
    print(message)  # noqa: T201


FIXED_PATIENTS = [
    {
        "name": "Yachana Desai",
        "date_of_birth": "1988-06-15",
        "phone_number": "+917012255109",
    },
    {
        "name": "Tanish Datta",
        "date_of_birth": "1949-03-22",
        "phone_number": "+917012255109",
    },
    {
        "name": "Sanya Srinivas",
        "date_of_birth": "1972-12-05",
        "phone_number": "+917736592618",
    },
    {
        "name": "Orinder Mane",
        "date_of_birth": "1984-01-26",
        "phone_number": "+917736592618",
    },
    {
        "name": "Gunbir Chacko",
        "date_of_birth": "1951-08-10",
        "phone_number": "+923263672475",
    },
    {
        "name": "Mahika Sundaram",
        "date_of_birth": "1998-11-30",
        "phone_number": "+923263672475",
    },
]

DIAGNOSTIC_CATEGORIES = [
    {
        "code": "LAB",
        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
        "display": "Laboratory",
    },
    {
        "code": "RAD",
        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
        "display": "Radiology",
    },
    {
        "code": "PAT",
        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
        "display": "Pathology",
    },
    {
        "code": "MB",
        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
        "display": "Microbiology",
    },
    {
        "code": "CT",
        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
        "display": "CAT Scan",
    },
]


def load_fixtures(base):  # noqa: PLR0915, PLR0912
    password = "Ohcn@123"

    geo_organization = base.create_organization(org_type=OrganizationTypeChoices.govt.value, name="Kerala")
    base.create_organization(
        org_type=OrganizationTypeChoices.govt.value,
        parent=geo_organization.id,
        name="Ernakulam",
    )
    suppliers = []
    for _ in range(3):
        suppliers.append(
            base.create_organization(
                org_type=OrganizationTypeChoices.product_supplier.value,
                name=f"Supplier {base.fake.company()}",
            )
        )
    role_org_names = [
        "Volunteer",
        "Doctor",
        "Staff",
        "Nurse",
        "Administrator",
        "Facility Admin",
    ]
    role_orgs = {}
    for name in role_org_names:
        role_orgs[name] = base.create_organization(org_type=OrganizationTypeChoices.role.value, name=name)
    log("Loading organizations completed")

    facility = base.create_facility(
        geo_organization.id,
        name="FACILITY WITH PATIENTS",
        facility_type="Private Hospital",
    )
    facility_id = facility.id
    log("Loading facility completed")

    existing = base.get_facility_organizations(facility_id)
    departments = {}
    admin_org = next((o for o in existing if o.name == "Administration"), None)
    if admin_org:
        departments["Administration"] = admin_org
    for name in FACILITY_DEPARTMENTS:
        departments[name] = base.create_facility_organization(facility_id, name=name)
    general_medicine = departments["General Medicine"]
    log("Loading departments completed")

    ward = base.create_location(
        facility_id,
        name="Ward A",
        form=FacilityLocationFormChoices.wa.value,
        mode=FacilityLocationModeChoices.kind.value,
        organizations=[general_medicine.id],
    )
    for idx in range(1, 6):
        base.create_location(
            facility_id,
            name=f"Bed {idx}",
            description=f"Bed {idx} in {ward.name}",
            parent=ward.id,
            form=FacilityLocationFormChoices.bd.value,
            mode=FacilityLocationModeChoices.instance.value,
            organizations=[general_medicine.id],
        )
    log("Loading locations completed")

    for i in range(1, 6):
        base.create_device(facility_id, registered_name=f"Device {i}")
    log("Loading devices completed")

    roles = base.get_roles()
    default_users = [
        ("Doctor", "care-doctor"),
        ("Staff", "care-staff"),
        ("Nurse", "care-nurse"),
        ("Administrator", "care-admin"),
        ("Volunteer", "care-volunteer"),
        ("Facility Admin", "care-fac-admin"),
    ]
    created_users = {}
    for role_name, username in default_users:
        if role_name not in roles or role_name not in role_orgs:
            continue
        user = base.create_user(
            geo_organization.id,
            role_orgs=[
                {
                    "organization": role_orgs[role_name].id,
                    "role": roles[role_name].id,
                }
            ],
            username=username,
            email=f"{username}@care.test",
            password=password,
        )
        created_users[role_name] = user
    log("Loading users completed")

    patients = []
    for p in FIXED_PATIENTS:
        patients.append(
            base.create_patient(
                geo_organization.id,
                name=p["name"],
                date_of_birth=p["date_of_birth"],
                phone_number=p["phone_number"],
            )
        )
    # 4 additional random patients (total 10)
    for _ in range(4):
        patients.append(base.create_patient(geo_organization.id))
    log("Loading patients completed")

    encounters = []
    for patient in patients:
        encounter = base.create_encounter(
            patient.id,
            facility_id,
            organizations=[general_medicine.id],
            status=StatusChoices.in_progress.value,
        )
        encounters.append(encounter)
    log("Loading encounters completed")

    admin_org = departments.get("Administration")
    if admin_org:
        for role_name in ("Facility Admin", "Nurse", "Staff"):
            user = created_users.get(role_name)
            role = roles.get(role_name)
            if user and role:
                base.add_user_to_facility_organization(facility_id, admin_org.id, user.id, role.id)
    log("Loading facility organization memberships completed")

    base.create_facility(
        geo_organization.id,
        name="SECONDARY FACILITY",
        facility_type="Private Hospital",
        is_public=True,
    )
    log("Loading secondary facility completed")

    base.load_questionnaires_from_file([geo_organization.id])
    log("Loading questionnaires completed")

    base.load_templates_from_file(facility=facility_id)
    log("Loading report templates completed")

    load_lab_definitions(base, facility_id, departments)
    log("Loading lab definitions completed")

    load_inventory(base, facility_id, departments, suppliers, ward)
    log("Loading inventory completed")

    load_scheduling(base, facility_id, created_users, patients, encounters, departments, roles)
    log("Loading scheduling completed")

    load_clinical_data(base, facility_id, patients, encounters, created_users)
    log("Loading clinical data (medications, service requests, diagnostic reports) completed")

    setup_managing_organization(base, role_orgs, geo_organization.id, password)
    log("Loading managing organization completed")

    log("\n" + "=" * 55)
    log(f"  {'Username':<25} {'Password':<15} {'Role'}")
    log("-" * 55)
    log(f"  {'admin':<25} {'admin':<15} {'Superuser'}")
    for role_name, username in default_users:
        log(f"  {username:<25} {password:<15} {role_name}")
    for user_def in MANAGING_ORG_USERS:
        if user_def["action"] == "create":
            log(f"  {user_def['username']:<25} {password:<15} {user_def['role']}")
    log("=" * 55 + "\n")


def load_lab_definitions(base, facility_id, departments):
    laboratory = departments["Laboratory"]
    administration = departments.get("Administration")

    lab_location = base.create_location(
        facility_id,
        name="Bio-Chemistry Lab",
        form=FacilityLocationFormChoices.ro.value,
        mode=FacilityLocationModeChoices.kind.value,
        organizations=[laboratory.id],
    )
    base.add_organization_to_location(facility_id, lab_location.id, laboratory.id)
    if administration:
        base.add_organization_to_location(facility_id, lab_location.id, administration.id)

    lab_charge_category = base.create_resource_category(facility_id, "Lab Tests", "charge_item_definition")
    lab_activity_category = base.create_resource_category(facility_id, "Lab Tests", "activity_definition")

    lab_service = base.create_healthcare_service(
        facility_id,
        name="Pathology Lab",
        internal_type=HealthcareServiceInternalType.lab.value,
        styling_metadata={"careIcon": "microscope"},
        locations=[lab_location.id],
    )

    for test in LAB_TESTS:
        base.create_lab_test(
            facility_id,
            test,
            service_id=lab_service.id,
            location_id=lab_location.id,
            charge_category_slug=lab_charge_category.slug,
            activity_category_slug=lab_activity_category.slug,
        )


def load_inventory(base, facility_id, departments, suppliers, transfer_destination):
    pharmacy = departments["Pharmacy"]

    pharmacy_location = base.create_location(
        facility_id,
        name="Pharmacy",
        form=FacilityLocationFormChoices.ro.value,
        mode=FacilityLocationModeChoices.kind.value,
        organizations=[pharmacy.id],
    )

    base.create_healthcare_service(
        facility_id,
        name="Main Pharmacy",
        internal_type=HealthcareServiceInternalType.pharmacy.value,
        styling_metadata={},
        locations=[pharmacy_location.id],
    )

    category_names = {item["category"] for item in INVENTORY_ITEMS}
    categories = {}
    for category_name in category_names:
        categories[category_name] = {
            "product_knowledge": base.create_resource_category(facility_id, category_name, "product_knowledge").slug,
            "charge_item_definition": base.create_resource_category(
                facility_id, category_name, "charge_item_definition"
            ).slug,
        }

    supplier_orders = {}
    for idx, supplier in enumerate(suppliers):
        request_order = base.create_request_order(
            facility_id,
            name=f"Initial Stock Request — {supplier.name}",
            destination=pharmacy_location.id,
            supplier=supplier.id,
        )
        delivery_order = base.create_delivery_order(
            facility_id,
            name=f"Initial Stock Delivery — {supplier.name}",
            destination=pharmacy_location.id,
            supplier=supplier.id,
        )
        supplier_orders[idx] = (request_order, delivery_order)

    transfer_seed = None
    for idx, item in enumerate(INVENTORY_ITEMS):
        request_order, delivery_order = supplier_orders[idx % len(suppliers)]

        product, product_knowledge = base.create_facility_product(
            facility_id,
            item,
            categories[item["category"]],
        )

        supply_request = base.create_supply_request(
            order=request_order.id,
            item=product_knowledge.id,
            quantity=item["stock_quantity"],
        )

        delivery = base.create_supply_delivery(
            order=delivery_order.id,
            supplied_item=product.id,
            supplied_item_quantity=item["stock_quantity"],
            supply_request=supply_request.id,
        )

        base.update_supply_delivery(delivery.id, status="completed", order=delivery_order.id)

        if transfer_seed is None:
            transfer_seed = (product, item["stock_quantity"])

    if transfer_seed and transfer_destination:
        product, stock_quantity = transfer_seed
        transfer_quantity = max(1, stock_quantity // 4)

        pharmacy_inventory_items = base.list_inventory_items(facility_id, pharmacy_location.id)
        pharmacy_item = next(
            (ii for ii in pharmacy_inventory_items if ii["product"]["id"] == product.id),
            None,
        )
        if pharmacy_item:
            transfer_request_order = base.create_request_order(
                facility_id,
                name="Ward Top-up Request",
                origin=pharmacy_location.id,
                destination=transfer_destination.id,
            )
            transfer_delivery_order = base.create_delivery_order(
                facility_id,
                name="Ward Top-up Delivery",
                origin=pharmacy_location.id,
                destination=transfer_destination.id,
            )
            transfer_supply_request = base.create_supply_request(
                order=transfer_request_order.id,
                item=product.product_knowledge["id"],
                quantity=transfer_quantity,
            )
            transfer_delivery = base.create_supply_delivery(
                order=transfer_delivery_order.id,
                supplied_inventory_item=pharmacy_item["id"],
                supplied_item_quantity=transfer_quantity,
                supply_request=transfer_supply_request.id,
            )
            base.update_supply_delivery(
                transfer_delivery.id,
                status="completed",
                order=transfer_delivery_order.id,
            )


def setup_managing_organization(base, role_orgs, geo_id, password):
    """Create a managing organization, link it to all role orgs, and assign users."""
    role_org_roles = base.get_role_org_roles()

    managing_org = base.create_organization(org_type=OrganizationTypeChoices.role.value, name="Health Department")
    managing_org_id = managing_org.id

    for _name, org in role_orgs.items():
        base.link_managing_org(org.id, managing_org_id)

    for user_def in MANAGING_ORG_USERS:
        role_id = role_org_roles[user_def["role"]].id

        if user_def["action"] == "create":
            user = base.create_user(
                geo_id,
                username=user_def["username"],
                email=f"{user_def['username']}@care.test",
                password=password,
            )
            base.assign_org_role(managing_org_id, user.id, role_id)

        elif user_def["action"] == "assign":
            user_data = base.get_user(user_def["username"])
            base.assign_org_role(managing_org_id, user_data.id, role_id)


def load_scheduling(base, facility_id, created_users, patients, encounters, departments, roles):
    """Create schedules, token slots, queues, and sample appointments for all patients."""
    now = timezone.now()
    valid_from = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    valid_to = (now + timedelta(days=8)).strftime("%Y-%m-%dT23:59:59")

    doctor = created_users.get("Doctor")
    if not doctor:
        return

    admin_org = departments.get("Administration")
    doctor_role = roles.get("Doctor")
    if admin_org and doctor_role:
        base.add_user_to_facility_organization(facility_id, admin_org.id, doctor.id, doctor_role.id)

    base.create_schedule(
        facility_id,
        resource_type="practitioner",
        resource_id=doctor.id,
        name="Doctor Consultation Schedule",
        valid_from=valid_from,
        valid_to=valid_to,
        availabilities=[DEFAULT_AVAILABILITY],
    )

    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    base.create_token_queue(
        facility_id,
        resource_type="practitioner",
        resource_id=doctor.id,
        date=tomorrow,
    )
    base.create_token_sub_queue(
        facility_id,
        resource_type="practitioner",
        resource_id=doctor.id,
        name="Consultation Room 1",
    )
    base.create_token_category(
        facility_id,
        resource_type="practitioner",
        name="General Consultation",
        shorthand="GEN",
    )

    slots_response = base.get_slots_for_day(
        facility_id,
        resource_type="practitioner",
        resource_id=doctor.id,
        day=tomorrow,
    )
    slots = slots_response.get("results", [])
    # Book every patient into their own slot; there are ~30 slots in the default
    # 09:30-18:30 / 18-min window so all 10 patients will always get one.
    for idx, patient in enumerate(patients):
        if idx >= len(slots):
            break
        base.create_appointment(
            facility_id,
            slot_id=slots[idx].id,
            patient_id=patient.id,
            note=f"Auto-booked fixture appointment {idx + 1}",
        )


def load_clinical_data(base, facility_id, patients, encounters, created_users):
    """
    Seed clinical data for every patient:
      - MedicationRequest  (1–3 active prescriptions, random)
      - MedicationStatement (self-reported / historical medication)
      - MedicationAdministration (dose given, linked to the first request above)
      - ServiceRequest via ActivityDefinition (lab order, one per patient cycling
        through the seeded lab tests)
      - DiagnosticReport linked to that service request (random category)

    ``patients`` and ``encounters`` are parallel lists produced in load_fixtures;
    index N of each list belongs to the same patient.
    """
    from care.fixtures.constants import (
        SNOMED_AMOXICILLIN,
        SNOMED_IBUPROFEN,
        SNOMED_PARACETAMOL,
    )

    doctor = created_users.get("Doctor")
    if not doctor:
        return

    sample_medications = [
        SNOMED_AMOXICILLIN,
        SNOMED_PARACETAMOL,
        SNOMED_IBUPROFEN,
    ]

    # Fetch the activity definition slugs that were created by load_lab_definitions.
    from django.urls import reverse

    ad_response = base.get(
        reverse("activity_definition-list", kwargs={"facility_external_id": facility_id}),
        params={"limit": 50},
    )
    lab_activity_slugs = [ad["slug"] for ad in ad_response.get("results", [])]

    for idx, (patient, encounter) in enumerate(zip(patients, encounters)):  # noqa: B905
        patient_id = patient.id
        encounter_id = encounter.id

        # MedicationRequest — 1 to 3 random prescriptions per patient
        med_count = secrets.randbelow(3) + 1  # 1, 2, or 3
        first_med_request = None
        for i in range(med_count):
            med = sample_medications[(idx + i) % len(sample_medications)]
            freq_text = "twice" if i % 2 == 0 else "once"
            days = 3 + i * 2
            req = base.create_medication_request(
                patient_id=patient_id,
                encounter_id=encounter_id,
                medication=med,
                dosage_text=f"1 tablet {freq_text} daily for {days} days",
            )
            if first_med_request is None:
                first_med_request = req

        # MedicationStatement — patient-reported historical medication
        base.create_medication_statement(
            patient_id=patient_id,
            encounter_id=encounter_id,
            medication=sample_medications[(idx + 1) % len(sample_medications)],
            dosage_text="1 tablet once daily (self-reported)",
        )

        # MedicationAdministration — dose given by staff, linked to first request
        base.create_medication_administration(
            patient_id=patient_id,
            encounter_id=encounter_id,
            medication=sample_medications[idx % len(sample_medications)],
            request_id=first_med_request.id,
            dosage_text="1 tablet administered orally",
            dose_value=1,
        )

        # ServiceRequest via ActivityDefinition (lab order)
        if lab_activity_slugs:
            activity_slug = lab_activity_slugs[idx % len(lab_activity_slugs)]
            service_request = base.apply_activity_definition_as_service_request(
                facility_id=facility_id,
                encounter_id=encounter_id,
                activity_definition_slug=activity_slug,
                requester_id=doctor.id,
            )

            # DiagnosticReport linked to that service request (random category)
            base.create_diagnostic_report(
                patient_id=patient_id,
                service_request_id=service_request.id,
                status="preliminary",
                conclusion="Awaiting review by attending physician.",
                category=choice(DIAGNOSTIC_CATEGORIES),
            )


if __name__ == "__main__":
    with care_fixture_context() as base:
        load_fixtures(base)
