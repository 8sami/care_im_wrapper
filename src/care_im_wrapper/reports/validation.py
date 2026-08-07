"""Server-side validation for a ``NotificationTemplate.variable_mapping`` draft.

Per expression: Jinja syntax, provider formatting (e.g. WhatsApp's no-newline
rule), then field existence against the template's linked context(s). Returns
a per-key error map so the FE can show each on its own placeholder field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from care.emr.reports.renderer.template_engine import TemplateEngine  # pyright: ignore[reportMissingImports]
from jinja2 import nodes  # pyright: ignore[reportMissingImports]

from care_im_wrapper.messaging.registry import get_declared_placeholders, validate_provider_expression
from care_im_wrapper.reports.schema import (
    _merge_field_trees,
    build_context_schema,
    resolve_template_context_slugs,
)

if TYPE_CHECKING:
    from care_im_wrapper.models.notification import NotificationTemplate

_engine = TemplateEngine()
_GLOBAL_NAMES = frozenset(_engine.env.globals.keys())


def _node_chain(node: nodes.Node) -> list[str] | None:
    """Reconstructs a dotted attribute chain (``object.patient.name``) from a Jinja
    AST node, or ``None`` if the node isn't a pure name/attribute/const-subscript chain."""
    if isinstance(node, nodes.Name):
        return [node.name]
    if isinstance(node, nodes.Getattr):
        base = _node_chain(node.node)
        return [*base, node.attr] if base is not None else None
    if isinstance(node, nodes.Getitem):
        base = _node_chain(node.node)
        if base is None:
            return None
        arg = node.arg
        if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
            return [*base, arg.value]
        return None
    return None


def _maximal_chains(expr: str) -> list[list[str]]:
    """Every maximal name/attribute chain referenced in ``expr`` (assumes valid syntax).
    ``object.patient.name`` yields ``[["object","patient","name"]]``, not its prefixes."""
    ast = _engine.env.parse(expr)
    chains: list[list[str]] = []
    for node in ast.find_all((nodes.Getattr, nodes.Getitem, nodes.Name)):
        chain = _node_chain(node)
        if chain:
            chains.append(chain)

    maximal: list[list[str]] = []
    for chain in chains:
        is_prefix_of_longer = any(len(other) > len(chain) and other[: len(chain)] == chain for other in chains)
        if not is_prefix_of_longer and chain not in maximal:
            maximal.append(chain)
    return maximal


def _path_exists(segments: list[str], field_tree: list[dict[str, Any]]) -> bool:
    if not segments:
        return True
    head, *rest = segments
    for field in field_tree:
        if field["key"] == head:
            return _path_exists(rest, field.get("fields", []))
    return False


def _validate_fields(expr: str, object_field_tree: list[dict[str, Any]], extra_keys: set[str]) -> list[str]:
    errors: list[str] = []
    for chain in _maximal_chains(expr):
        head = chain[0]
        if head == "object":
            rest = chain[1:]
            if rest and not _path_exists(rest, object_field_tree):
                errors.append(f"Unknown field: object.{'.'.join(rest)}")
        elif head in _GLOBAL_NAMES:
            continue
        elif head not in extra_keys:
            errors.append(f"Unknown variable: {head}")
    return errors


def _validate_expression(
    expr: Any,
    provider: str,
    object_field_tree: list[dict[str, Any]],
    extra_keys: set[str],
    *,
    has_context: bool,
) -> str | None:
    if not isinstance(expr, str):
        return "Expression must be a string."

    ok, message = _engine.validate_syntax(expr)
    if not ok:
        return message

    provider_errors = validate_provider_expression(provider, expr)
    if provider_errors:
        return provider_errors[0]

    if has_context:
        field_errors = _validate_fields(expr, object_field_tree, extra_keys)
        if field_errors:
            return field_errors[0]

    return None


def validate_variable_mapping(
    template: NotificationTemplate,
    variable_mapping: dict[str, Any],
) -> dict[str, str]:
    """Returns ``{placeholder_key: error message}`` for every invalid entry; an empty
    dict means the whole mapping is valid to save."""
    context_slugs = resolve_template_context_slugs(template)
    schemas = [s for s in (build_context_schema(slug) for slug in context_slugs) if s is not None]
    object_field_tree = _merge_field_trees([s["object_fields"] for s in schemas]) if schemas else []
    extra_keys = {row["key"] for s in schemas for row in s["extra_context_fields"]}

    errors: dict[str, str] = {}
    for key, expr in variable_mapping.items():
        message = _validate_expression(
            expr,
            str(template.provider),
            object_field_tree,
            extra_keys,
            has_context=bool(schemas),
        )
        if message:
            errors[key] = message

    # A mapping that fills only some placeholders sends fewer parameters than the approved
    # body declares, which the provider rejects outright -- so a half-filled mapping is
    # never savable. An empty one is: that is a template nobody has configured yet.
    if variable_mapping:
        for key in get_declared_placeholders(str(template.provider), template):
            if key not in variable_mapping:
                errors[key] = "A value is required; the template will not send without it."
    return errors
