"""Choice enums with no Django app/model dependencies.

Lives outside models/ and settings.py specifically so both can import it without a
circular import: models/conversation_session.py already imports settings.plugin_settings,
so settings.py can't import ConversationSession back.
"""

from django.db import models


class Provider(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"  # pyright: ignore[reportAssignmentType]
