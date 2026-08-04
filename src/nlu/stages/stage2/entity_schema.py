"""
Compile optional entity_schema into CREATE prompt rules, tool facts schema, and aliases.

Single interpretation point for schema-driven business entity extraction.

Alias precedence (when entity_schema is present)
------------------------------------------------
For identical *normalized* lookup keys (case-insensitive; stored lowercased):

    entity_schema field catalog  >  tenant_context.aliases

Schema catalog entries override tenant aliases on key clash. Non-overlapping
tenant aliases are retained. Original catalog phrase labels (as supplied in the
schema) are preserved in the generated prompt; only the resolve alias map is
normalized to lowercase.

Multi-entity path
-----------------
Each declared field is preserved on facts. Catalog fields resolve independently
against that field's own catalog (never a merged catalog). bookable_item still
emits platform ``service_id`` / ``service_candidates`` for Core compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SUPPORTED_ENTITY_TYPES = frozenset({"catalog", "enum", "text"})
SUPPORTED_ROLES = frozenset({"bookable_item", "staff"})
_BOOKABLE_NAMES = frozenset({"service", "room_type"})
_STAFF_NAMES = frozenset({"staff", "technician"})


class EntitySchemaValidationError(ValueError):
    """Raised when entity_schema is present but invalid or unsupported."""


@dataclass(frozen=True)
class CompiledEntityField:
    """One validated entity_schema field."""

    name: str
    type: str
    description: str
    role: Optional[str] = None
    # Lowercased phrase → id for catalog resolve (catalog type only).
    catalog: Dict[str, Any] = field(default_factory=dict)
    # Original phrase labels for prompts (catalog type only).
    catalog_phrases: Tuple[str, ...] = ()
    # Enum member strings (enum type only).
    values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledBusinessEntities:
    """Outputs CREATE needs from a validated entity_schema."""

    prompt_rules: str
    facts_schema: Dict[str, Any]
    fields: Tuple[CompiledEntityField, ...] = ()
    # Flat merge of all catalog phrases — legacy effective_tenant_context only.
    alias_map: Dict[str, Any] = field(default_factory=dict)
    catalog_field_names: List[str] = field(default_factory=list)

    @property
    def bookable_item_field(self) -> Optional[CompiledEntityField]:
        for f in self.fields:
            if f.type == "catalog" and resolved_id_key(f) == "service_id":
                return f
        return None


def resolved_id_key(entity: CompiledEntityField) -> str:
    """Platform fact key for a resolved catalog entity."""
    role = entity.role
    if role is None:
        if entity.name in _BOOKABLE_NAMES:
            role = "bookable_item"
        elif entity.name in _STAFF_NAMES:
            role = "staff"
    if role == "bookable_item":
        return "service_id"
    if role == "staff":
        return "staff_id"
    return f"{entity.name}_id"


def compile_business_entities(entity_schema: Any) -> CompiledBusinessEntities:
    """Validate entity_schema and compile prompt rules, tool facts, and field metadata.

    Raises:
        EntitySchemaValidationError: invalid shape or unsupported field type/version.
    """
    if not isinstance(entity_schema, dict):
        raise EntitySchemaValidationError(
            "entity_schema must be an object"
        )

    # Missing version defaults to 1; only explicit unsupported versions fail.
    if "version" in entity_schema and entity_schema["version"] not in (1, "1"):
        raise EntitySchemaValidationError(
            f"unsupported entity_schema version: {entity_schema['version']!r} (supported: 1)"
        )

    fields = entity_schema.get("fields")
    if fields is None:
        raise EntitySchemaValidationError(
            "entity_schema.fields is required"
        )
    if not isinstance(fields, list):
        raise EntitySchemaValidationError(
            "entity_schema.fields must be an array"
        )

    properties: Dict[str, Any] = {}
    alias_map: Dict[str, Any] = {}
    catalog_field_names: List[str] = []
    compiled_fields: List[CompiledEntityField] = []
    prompt_sections: List[str] = [
        "── BUSINESS ENTITY RULES ───────────────────────────────────────────────────",
        "Extract only the business entities declared below.",
        "Do NOT invent values the user did not mention.",
        "",
        "If the user explicitly declines to choose a value for one of the declared "
        "business entities (for example \"no preference\", \"any\", \"either\", "
        "\"I don't mind\", or similar expressions), include that entity's field name "
        "in declined_entities and leave the corresponding facts.<entity> null.",
        "Do not invent catalog values for a declined entity.",
        "Null on a fact means not mentioned / not selected — not declined.",
        "",
    ]

    for index, raw_field in enumerate(fields):
        if not isinstance(raw_field, dict):
            raise EntitySchemaValidationError(
                f"entity_schema.fields[{index}] must be an object"
            )
        name = raw_field.get("name")
        if not name or not isinstance(name, str):
            raise EntitySchemaValidationError(
                f"entity_schema.fields[{index}].name is required"
            )
        field_type = raw_field.get("type")
        if not field_type or not isinstance(field_type, str):
            raise EntitySchemaValidationError(
                f"entity_schema.fields[{index}] ({name!r}): type is required"
            )
        if field_type not in SUPPORTED_ENTITY_TYPES:
            raise EntitySchemaValidationError(
                f"unsupported entity type {field_type!r} for field {name!r}; "
                f"supported types: {', '.join(sorted(SUPPORTED_ENTITY_TYPES))}"
            )

        description = raw_field.get("description") or f"The {name} mentioned by the user."
        if not isinstance(description, str):
            raise EntitySchemaValidationError(
                f"entity_schema.fields[{index}] ({name!r}): description must be a string"
            )

        role = raw_field.get("role")
        if role is not None:
            if not isinstance(role, str) or role not in SUPPORTED_ROLES:
                raise EntitySchemaValidationError(
                    f"entity_schema.fields[{index}] ({name!r}): unsupported role "
                    f"{role!r}; supported roles: {', '.join(sorted(SUPPORTED_ROLES))}"
                )

        catalog_resolve: Dict[str, Any] = {}
        catalog_phrases: Tuple[str, ...] = ()
        enum_values: Tuple[str, ...] = ()

        if field_type == "catalog":
            catalog = raw_field.get("catalog")
            if not isinstance(catalog, Mapping):
                raise EntitySchemaValidationError(
                    f"entity_schema.fields[{index}] ({name!r}): catalog type requires "
                    "a catalog object mapping phrases to ids"
                )
            if not catalog:
                raise EntitySchemaValidationError(
                    f"entity_schema.fields[{index}] ({name!r}): catalog must not be empty"
                )

            catalog_field_names.append(name)
            phrases: List[str] = []
            for phrase, entity_id in catalog.items():
                label = str(phrase).strip()
                if not label:
                    continue
                phrases.append(label)
                key = label.lower()
                catalog_resolve[key] = entity_id
                alias_map[key] = entity_id
            catalog_phrases = tuple(phrases)

            keys = ", ".join(f'"{p}"' for p in catalog_phrases) or "none provided"
            prompt_sections.extend(
                [
                    f"Entity: {name}",
                    f"Type: {field_type}",
                    f"Description: {description}",
                    f"Known catalog phrases (for reference only): {keys}",
                    f"Extract the user's phrase EXACTLY as spoken into facts.{name}.",
                    "Do NOT resolve, correct, or match it against the catalog — code handles that.",
                    f"facts.{name} must be null when the user does not mention this entity.",
                    "",
                ]
            )
            properties[name] = {
                "type": ["string", "null"],
                "description": (
                    f"{description} "
                    "Raw phrase as spoken (typos preserved). "
                    "Null if not mentioned. Do NOT resolve against the catalog."
                ),
            }

        elif field_type == "enum":
            values = raw_field.get("values")
            if not isinstance(values, list) or not values:
                raise EntitySchemaValidationError(
                    f"entity_schema.fields[{index}] ({name!r}): enum type requires "
                    "a non-empty values array"
                )
            normalized: List[str] = []
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise EntitySchemaValidationError(
                        f"entity_schema.fields[{index}] ({name!r}): enum values "
                        "must be non-empty strings"
                    )
                normalized.append(value.strip())
            enum_values = tuple(normalized)
            listed = ", ".join(f'"{v}"' for v in enum_values)
            prompt_sections.extend(
                [
                    f"Entity: {name}",
                    f"Type: {field_type}",
                    f"Description: {description}",
                    f"Allowed values: {listed}",
                    f"Extract one allowed value into facts.{name}, or null if not mentioned.",
                    "Do NOT invent values outside the allowed list.",
                    "",
                ]
            )
            properties[name] = {
                "type": ["string", "null"],
                "description": (
                    f"{description} One of: {listed}. Null if not mentioned."
                ),
            }

        else:  # text
            prompt_sections.extend(
                [
                    f"Entity: {name}",
                    f"Type: {field_type}",
                    f"Description: {description}",
                    f"Extract the user's text EXACTLY as spoken into facts.{name}.",
                    f"facts.{name} must be null when the user does not mention this entity.",
                    "",
                ]
            )
            properties[name] = {
                "type": ["string", "null"],
                "description": (
                    f"{description} Raw text as spoken. Null if not mentioned."
                ),
            }

        compiled_fields.append(
            CompiledEntityField(
                name=name,
                type=field_type,
                description=description,
                role=role if isinstance(role, str) else None,
                catalog=catalog_resolve,
                catalog_phrases=catalog_phrases,
                values=enum_values,
            )
        )

    if not properties:
        raise EntitySchemaValidationError(
            "entity_schema.fields must contain at least one field"
        )

    facts_schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
    }
    return CompiledBusinessEntities(
        prompt_rules="\n".join(prompt_sections).rstrip() + "\n",
        facts_schema=facts_schema,
        fields=tuple(compiled_fields),
        alias_map=alias_map,
        catalog_field_names=catalog_field_names,
    )


def effective_tenant_context(
    tenant_context: Optional[Dict[str, Any]],
    compiled: Optional[CompiledBusinessEntities],
) -> Dict[str, Any]:
    """Return a new tenant_context with schema aliases merged; never mutates input.

    Precedence for the same normalized (lowercased) key:

        entity_schema.catalog  >  tenant_context.aliases

    Lookup via ``resolve_service`` is case-insensitive because alias keys are
    stored lowercased. Prompt text still lists original catalog labels from the
    schema (see ``compile_business_entities``).

    Note: multi-entity catalog resolution uses per-field catalogs, not this flat
    merge. The flat merge remains for legacy single-channel resolve compatibility.
    """
    base = dict(tenant_context or {})
    if compiled is None or not compiled.alias_map:
        return base
    existing = dict(base.get("aliases") or {})
    # Schema catalog overrides tenant aliases on normalized key clash.
    base["aliases"] = {**existing, **compiled.alias_map}
    return base


def extract_declared_facts(
    raw_facts: Mapping[str, Any],
    compiled: CompiledBusinessEntities,
) -> Dict[str, Any]:
    """Copy declared entity fields from tool output into a facts fragment."""
    out: Dict[str, Any] = {}
    for entity in compiled.fields:
        value = raw_facts.get(entity.name)
        if value is None:
            out[entity.name] = None
            continue
        text = str(value).strip()
        if not text:
            out[entity.name] = None
            continue
        if entity.type == "enum" and entity.values:
            matched = _match_enum_value(text, entity.values)
            out[entity.name] = matched if matched is not None else text
        else:
            out[entity.name] = text
    return out


def _match_enum_value(text: str, values: Sequence[str]) -> Optional[str]:
    lowered = text.strip().lower()
    for value in values:
        if value.lower() == lowered:
            return value
    return None


def bookable_item_phrase(
    facts: Mapping[str, Any],
    compiled: Optional[CompiledBusinessEntities],
) -> Any:
    """Raw phrase for the bookable catalog entity (legacy service_term channel)."""
    if compiled is None:
        return None
    bookable = compiled.bookable_item_field
    if bookable is None:
        return service_term_from_facts(facts, compiled.catalog_field_names)
    value = facts.get(bookable.name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def service_term_from_facts(
    facts: Mapping[str, Any],
    catalog_field_names: Optional[List[str]] = None,
) -> Any:
    """Compatibility: map schema catalog fields onto legacy service_term."""
    if not isinstance(facts, Mapping):
        return None
    if facts.get("service_term") is not None:
        return facts.get("service_term")
    names = list(catalog_field_names or [])
    if "service" not in names:
        names = ["service", *names]
    for name in names:
        if name == "service_term":
            continue
        if facts.get(name) is not None:
            return facts.get(name)
    return None
