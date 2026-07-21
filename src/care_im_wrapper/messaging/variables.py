from __future__ import annotations

from typing import Any

from care.emr.reports.renderer.template_engine import TemplateEngine  # pyright: ignore[reportMissingImports]

# Autoescape off: messaging params are plain text, so escaping corrupts values like
# "O'Brien & Sons". Safe -- values never hit markup and the Jinja env stays sandboxed.
_engine = TemplateEngine()
_engine.env.autoescape = False


def resolve_variable(expr: str, related_object: Any, context: dict[str, Any]) -> str:
    """Renders one `NotificationTemplate.variable_mapping` Jinja2 expression, e.g. "{{ object.patient.name }}"."""
    return _engine.render(expr, {"object": related_object, **context})
