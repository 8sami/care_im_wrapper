from typing import ClassVar


class ConversationState:
    NEW: ClassVar[str] = "new"
    AWAITING_YOB: ClassVar[str] = "awaiting_yob"
    AMBIGUOUS: ClassVar[str] = "ambiguous"
    AUTHENTICATED: ClassVar[str] = "authenticated"
    COOLDOWN: ClassVar[str] = "cooldown"
    AWAITING_PATIENT_SEARCH: ClassVar[str] = "awaiting_patient_search"
    SELECTING_PATIENT: ClassVar[str] = "selecting_patient"
