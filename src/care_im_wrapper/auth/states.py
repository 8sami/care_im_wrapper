from typing import ClassVar


class ConversationState:
    NEW: ClassVar[str] = "new"
    AWAITING_YOB: ClassVar[str] = "awaiting_yob"
    AMBIGUOUS: ClassVar[str] = "ambiguous"
    AUTHENTICATED: ClassVar[str] = "authenticated"
    COOLDOWN: ClassVar[str] = "cooldown"
