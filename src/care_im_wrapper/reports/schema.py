"""Builds a JSON field schema (for the FE picker) and preview instances from a
registered context class. ``_extract_fields_from_context`` is adapted from
``care.emr.reports.context_builder.data_points.utils``, minus core's queryset/
filter/registry coupling.
"""

from __future__ import annotations

from typing import Any

from care.emr.reports.context_builder.data_points.base import (  # pyright: ignore[reportMissingImports]
    Field,
    SingleObjectContextBuilder,
)

from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY


def _field_schema(attr_name: str, field: Field, *, visited: set[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "key": attr_name,
        "display": field.display or attr_name.replace("_", " ").title(),
        "description": field.description,
        "type": field.type,
    }

    preview = _preview_value(field)
    if preview is not None:
        schema["preview_value"] = preview

    if field.target_context is not None:
        schema["is_nested_context"] = True
        nested = _extract_fields_from_context(field.target_context, visited.copy())
        if nested:
            schema["fields"] = nested

    return schema


def _preview_value(field: Field) -> Any:
    if field.preview_value is not None:
        if isinstance(field.preview_value, (list, dict)):
            return field.preview_value
        return str(field.preview_value)
    if field.preview_fn:
        try:
            return str(field.preview_fn())
        except Exception:  # a broken preview fn must not break schema generation
            return ""
    return None


def _extract_fields_from_context(context_class: type, visited: set[str] | None = None) -> list[dict[str, Any]]:
    """Reflection walker: returns one schema entry per ``Field`` class attribute,
    recursing into nested ``target_context`` classes. ``visited`` guards against
    cyclic context references."""
    if visited is None:
        visited = set()

    class_name = context_class.__name__
    if class_name in visited:
        return []
    visited.add(class_name)

    fields: list[dict[str, Any]] = []
    for attr_name in dir(context_class):
        if attr_name.startswith("_"):
            continue
        try:
            attr = getattr(context_class, attr_name)
        except Exception:  # descriptors/properties can raise on access during reflection
            continue
        if isinstance(attr, Field):
            fields.append(_field_schema(attr_name, attr, visited=visited))

    return sorted(fields, key=lambda f: f["display"])


def _extra_context_schema(context_class: type) -> list[dict[str, Any]]:
    extra: dict[str, Field] = getattr(context_class, "extra_context_fields", {}) or {}
    entries: list[dict[str, Any]] = []
    for key, field in extra.items():
        entry: dict[str, Any] = {
            "key": key,
            "display": field.display or key.replace("_", " ").title(),
            "description": field.description,
            "type": field.type,
        }
        preview = _preview_value(field)
        if preview is not None:
            entry["preview_value"] = preview
        entries.append(entry)
    return sorted(entries, key=lambda f: f["display"])


def build_context_schema(context_slug: str) -> dict[str, Any] | None:
    """Schema for a single context slug, or ``None`` if the slug is unregistered."""
    context_class = NOTIFICATION_CONTEXT_REGISTRY.get(context_slug)
    if context_class is None:
        return None
    return {
        "slug": context_slug,
        "display_name": getattr(context_class, "__display_name__", "") or context_slug.replace("_", " ").title(),
        "description": getattr(context_class, "__description__", ""),
        "object_fields": _extract_fields_from_context(context_class),
        "extra_context_fields": _extra_context_schema(context_class),
    }


def _merge_field_trees(trees: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Deep-unions field trees by ``key`` (recursing into nested ``fields``) so
    triggers with different ``context_slug``s on the same template get one schema."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for tree in trees:
        for field in tree:
            key = field["key"]
            if key not in merged:
                merged[key] = {**field}
                if "fields" in field:
                    merged[key]["fields"] = list(field["fields"])
                order.append(key)
            elif "fields" in field:
                existing = merged[key]
                existing["fields"] = _merge_field_trees([existing.get("fields", []), field["fields"]])
    return [merged[key] for key in order]


def _merge_flat(rows: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row_group in rows:
        for row in row_group:
            merged.setdefault(row["key"], row)
    return sorted(merged.values(), key=lambda f: f["display"])


def build_notification_schema(context_slugs: list[str]) -> dict[str, Any]:
    """Union of every given context's schema: object fields (``object.<path>``)
    and extra-context fields (bare ``<key>``). Unknown slugs are skipped; no
    slugs yields empty groups, for an unconfigured template."""
    contexts = [build_context_schema(slug) for slug in context_slugs]
    contexts = [c for c in contexts if c is not None]
    return {
        "contexts": [
            {"slug": c["slug"], "display_name": c["display_name"], "description": c["description"]} for c in contexts
        ],
        "object_fields": _merge_field_trees([c["object_fields"] for c in contexts]),
        "extra_context_fields": _merge_flat([c["extra_context_fields"] for c in contexts]),
    }


def resolve_template_context_slugs(template: Any) -> list[str]:
    """Distinct, non-empty ``context_slug``s of every trigger that renders this
    template. A template with no linked trigger yet yields ``[]``."""
    from care_im_wrapper.models.notification import NotificationTrigger

    return list(
        NotificationTrigger.objects.filter(template_slug=template.slug)
        .exclude(context_slug="")
        .values_list("context_slug", flat=True)
        .distinct()
    )


def build_preview(context_slug: str) -> tuple[SingleObjectContextBuilder, dict[str, Any]] | None:
    """A preview ``related_object`` (fields short-circuited to ``preview_value``)
    plus the extra-context dict, or ``None`` for an unregistered slug. Fed into
    ``resolve_variable`` -- the same function real sends use."""
    context_class = NOTIFICATION_CONTEXT_REGISTRY.get(context_slug)
    if context_class is None:
        return None
    preview_object = context_class(is_preview=True)
    extra: dict[str, Any] = {}
    for key, field in (getattr(context_class, "extra_context_fields", {}) or {}).items():
        value = _preview_value(field)
        extra[key] = value if value is not None else ""
    return preview_object, extra
