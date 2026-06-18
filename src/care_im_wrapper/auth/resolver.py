import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]
    from care.users.models import User  # pyright: ignore[reportMissingImports]
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
    identities: list[ResolvedIdentity]


def resolve_phone_number(phone_number: str) -> ResolutionResult:
    if not Patient or not User:
        return ResolutionResult(found=False, identities=[])

    # Normalization happens in tasks._extract_phone, so we assume E.164 here.
    identities = []

    try:
        # Check Patients
        patients = Patient.objects.filter(phone_number=phone_number).order_by("id")
        for p in patients:
            yob = p.year_of_birth
            if not yob:
                logger.warning("Patient %s has no year_of_birth, skipping", p.id)
                continue
            identities.append(
                ResolvedIdentity(
                    user_type="patient",
                    user_id=p.id,
                    year_of_birth=yob,
                    full_name=p.name,
                    phone_number=p.phone_number,
                )
            )
    except Exception:
        raise

    try:
        # Check Users (Staff)
        users = User.objects.filter(phone_number=phone_number, is_active=True).order_by("id")
        for u in users:
            dob = getattr(u, "date_of_birth", None)
            if not dob:
                logger.warning("User %s has no date_of_birth, skipping", u.id)
                continue
            identities.append(
                ResolvedIdentity(
                    user_type="staff",
                    user_id=u.id,
                    year_of_birth=dob.year,
                    full_name=u.get_full_name(),
                    phone_number=u.phone_number,
                )
            )
    except Exception:
        raise

    return ResolutionResult(found=bool(identities), identities=identities)
