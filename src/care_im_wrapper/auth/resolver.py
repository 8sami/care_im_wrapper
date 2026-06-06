import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from care.emr.models.patient import Patient
    from care.users.models import User
except ImportError:
    # Fallback for when running in isolation (e.g., tests)
    Patient = None
    User = None


@dataclass
class ResolvedIdentity:
    user_type: str  # "patient" | "staff"
    user_id: int
    year_of_birth: int
    full_name: str
    phone_number: str


@dataclass
class ResolutionResult:
    found: bool
    ambiguous: bool
    identities: list[ResolvedIdentity]


def resolve_phone_number(phone_number: str) -> ResolutionResult:
    if not Patient or not User:
        return ResolutionResult(found=False, ambiguous=False, identities=[])

    identities = []

    try:
        # Check Patients
        patients = Patient.objects.filter(phone_number=phone_number).order_by("id")[:2]
        for p in patients:
            identities.append(
                ResolvedIdentity(
                    user_type="patient",
                    user_id=p.id,
                    year_of_birth=int(p.dob.year)
                    if hasattr(p, "dob") and p.dob
                    else 0,  # Note: implementation detail depends on CARE
                    full_name=f"{p.first_name} {p.last_name}".strip(),
                    phone_number=p.phone_number,
                )
            )
    except Exception as e:
        logger.error("Error querying patients for phone %s: %s", phone_number, e)

    try:
        # Check Users (Staff)
        users = User.objects.filter(phone_number=phone_number, is_active=True).order_by("id")[:2]
        for u in users:
            identities.append(
                ResolvedIdentity(
                    user_type="staff",
                    user_id=u.id,
                    year_of_birth=0,  # Staff YOB not easily available from User model without extra fields
                    full_name=u.get_full_name(),
                    phone_number=u.phone_number,
                )
            )
    except Exception as e:
        logger.error("Error querying users for phone %s: %s", phone_number, e)

    found = len(identities) > 0
    ambiguous = len(identities) > 1

    return ResolutionResult(found=found, ambiguous=ambiguous, identities=identities)
