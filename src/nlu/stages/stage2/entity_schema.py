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
SUPPORTED_ROLES = frozenset({"bookable_item", "staff", "booking_subject"})
UNIQUE_ROLES = frozenset({"bookable_item", "staff"})
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
    def mentions_schema(self) -> Dict[str, Any]:
        properties = {
            entity.name: {
                "type": "boolean",
                "description": (
                    f"True only when the current utterance mentions or refers to {entity.name}; "
                    "it may be true while the corresponding fact is null when no safe value "
                    "can be extracted."
                ),
            }
            for entity in self.fields
        }
        return {
            "type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False,
        }

    @property
    def entity_results_schema(self) -> Dict[str, Any]:
        properties = {
            entity.name: {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["NOT_MENTIONED"]},
                        },
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["MENTIONED_UNRESOLVED"]},
                        },
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["MENTIONED_VALUE"]},
                            "value": _mentioned_value_schema(entity),
                        },
                        "required": ["status", "value"],
                        "additionalProperties": False,
                    },
                ]
            }
            for entity in self.fields
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    @property
    def bookable_item_field(self) -> Optional[CompiledEntityField]:
        for f in self.fields:
            if f.type == "catalog" and resolved_id_key(f) == "service_id":
                return f
        return None


def _mentioned_value_schema(entity: CompiledEntityField) -> Dict[str, Any]:
    """Tool schema for MENTIONED_VALUE.value; enums are closed over allowed members."""
    if entity.type == "enum" and entity.values:
        return {"type": "string", "enum": list(entity.values)}
    return {"type": "string", "minLength": 1}


def planning_slot_key(entity: CompiledEntityField) -> str:
    """Core missing_slots key for a compiled entity (catalog ids vs field name)."""
    if entity.type == "catalog":
        return resolved_id_key(entity)
    return entity.name


def outstanding_entity_names(
    compiled: CompiledBusinessEntities,
    conversation_context: Optional[Mapping[str, Any]],
) -> Optional[frozenset]:
    """Return outstanding entity names from Core missing_slots, or None if unknown."""
    ctx = conversation_context if isinstance(conversation_context, Mapping) else {}
    missing = ctx.get("missing_slots")
    if not isinstance(missing, list):
        return None
    missing_keys = {str(item) for item in missing}
    return frozenset(
        entity.name
        for entity in compiled.fields
        if planning_slot_key(entity) in missing_keys
    )


def apply_exact_enum_utterance_ownership(
    text: str,
    evidence: Mapping[str, Any],
    compiled: CompiledBusinessEntities,
    conversation_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assign an exact enum-token utterance to the unique matching schema entity.

    Context (missing slots / preceding ask) may disambiguate which entity owns
    the value. Durable catalog selections are never copied into current-turn
    evidence. Shared enum tokens are not silently assigned.
    """
    from ...entity_resolution import EntityMentionEvidence, MentionState

    utterance = (text or "").strip()
    if not utterance or not evidence:
        return dict(evidence)

    enum_matches: List[Tuple[CompiledEntityField, str]] = []
    for entity in compiled.fields:
        if entity.type != "enum" or not entity.values:
            continue
        matched = _match_enum_value(utterance, entity.values)
        if matched is not None:
            enum_matches.append((entity, matched))
    if not enum_matches:
        return dict(evidence)

    outstanding = outstanding_entity_names(compiled, conversation_context)
    eligible = enum_matches
    if outstanding is not None:
        filtered = [
            (entity, value)
            for entity, value in enum_matches
            if entity.name in outstanding
        ]
        if filtered:
            eligible = filtered

    catalog_collisions = [
        entity
        for entity in compiled.fields
        if entity.type == "catalog" and utterance.lower() in entity.catalog
    ]
    if outstanding is not None:
        catalog_collisions = [
            entity for entity in catalog_collisions if entity.name in outstanding
        ]
    if catalog_collisions:
        return dict(evidence)

    if len(eligible) > 1:
        ask_hits = [
            item for item in eligible
            if _entity_cued_by_preceding_ask(item[0], conversation_context)
        ]
        if len(ask_hits) == 1:
            eligible = ask_hits
        else:
            return dict(evidence)
    if len(eligible) != 1:
        return dict(evidence)

    owner, canonical = eligible[0]
    updated = dict(evidence)
    updated[owner.name] = EntityMentionEvidence(
        entity_name=owner.name,
        state=MentionState.MENTIONED_VALUE,
        raw_value=canonical,
    )
    lowered = utterance.lower()
    for entity in compiled.fields:
        if entity.name == owner.name:
            continue
        current = updated.get(entity.name)
        if current is None:
            continue
        same_value = (
            current.state == MentionState.MENTIONED_VALUE
            and isinstance(current.raw_value, str)
            and current.raw_value.strip().lower() == lowered
        )
        unresolved_false_hit = current.state == MentionState.MENTIONED_UNRESOLVED
        if not same_value and not unresolved_false_hit:
            continue
        if entity.type == "catalog" and lowered in entity.catalog:
            continue
        if unresolved_false_hit and entity.type != "catalog":
            continue
        updated[entity.name] = EntityMentionEvidence(
            entity_name=entity.name,
            state=MentionState.NOT_MENTIONED,
        )
    return updated


def apply_unique_catalog_utterance_mention(
    text: str,
    evidence: Mapping[str, Any],
    compiled: CompiledBusinessEntities,
) -> Dict[str, Any]:
    """Recover a unique spoken catalog mention when Stage 2 left it NOT_MENTIONED.

    Uses compiled catalog phrases only. Does not read Core ``service_candidates``
    or durable ``resolved_service_id``. Ambiguous, negated, and interrogative
    utterances stay unassigned.
    """
    from ...catalog import spoken_unique_catalog_mention
    from ...entity_resolution import EntityMentionEvidence, MentionState

    updated = dict(evidence)
    bookable = compiled.bookable_item_field
    if bookable is None:
        return updated
    current = updated.get(bookable.name)
    if not isinstance(current, EntityMentionEvidence):
        return updated
    if current.state != MentionState.NOT_MENTIONED:
        return updated
    spoken = spoken_unique_catalog_mention(text, bookable.catalog_phrases or tuple(bookable.catalog))
    if not spoken:
        return updated
    updated[bookable.name] = EntityMentionEvidence(
        entity_name=bookable.name,
        state=MentionState.MENTIONED_VALUE,
        raw_value=spoken,
    )
    return updated


def apply_unique_catalog_mention_to_slm(
    text: str,
    slm: Dict[str, Any],
    compiled: Optional[CompiledBusinessEntities],
) -> Dict[str, Any]:
    """Recover a unique spoken catalog mention on the Stage 2 SLM payload.

    Intended to run after ungrounded schema values are stripped, so a false
    contextual label (for example ``haircut`` on utterance ``premium``) does
    not permanently suppress recovery.
    """
    from ...entity_resolution import MentionState

    if compiled is None or not isinstance(slm, dict):
        return slm
    mentions = slm.get("_entity_mentions")
    if not isinstance(mentions, dict):
        return slm
    updated_mentions = apply_unique_catalog_utterance_mention(text, mentions, compiled)
    bookable = compiled.bookable_item_field
    if bookable is None:
        return slm
    previous = mentions.get(bookable.name)
    current = updated_mentions.get(bookable.name)
    if (
        getattr(previous, "state", None) == getattr(current, "state", None)
        and getattr(previous, "raw_value", None) == getattr(current, "raw_value", None)
    ):
        return slm
    facts = dict(slm.get("facts") or {})
    raw_value = getattr(current, "raw_value", None)
    facts[bookable.name] = raw_value
    updated = {
        **slm,
        "facts": facts,
        "_entity_mentions": updated_mentions,
    }
    if (
        current is not None
        and getattr(current, "state", None) == MentionState.MENTIONED_VALUE
    ):
        phrase = bookable_item_phrase(facts, compiled)
        if phrase:
            updated["service_term"] = phrase
    return updated


def _entity_cued_by_preceding_ask(
    entity: CompiledEntityField,
    conversation_context: Optional[Mapping[str, Any]],
) -> bool:
    ctx = conversation_context if isinstance(conversation_context, Mapping) else {}
    haystacks: List[str] = []
    turns = ctx.get("turns") or []
    if isinstance(turns, list):
        for turn in reversed(turns):
            if isinstance(turn, Mapping) and turn.get("assistant"):
                haystacks.append(str(turn.get("assistant")))
                break
    messages = ctx.get("messages") or []
    if isinstance(messages, list):
        for message in reversed(messages):
            if (
                isinstance(message, Mapping)
                and message.get("role") == "assistant"
                and message.get("text")
            ):
                haystacks.append(str(message.get("text")))
                break
    if not haystacks:
        return False
    blob = " ".join(haystacks).lower()
    name_cue = entity.name.replace("_", " ").lower()
    if name_cue and name_cue in blob:
        return True
    description = (entity.description or "").strip().lower()
    if description and description in blob:
        return True
    return False


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
    seen_names = set()
    seen_unique_roles = set()
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
        "Set entity_mentions.<name> for every declared entity. Use true when the "
        "current utterance mentions or contextually refers to it, including when "
        "no safe facts.<name> value can be extracted; otherwise use false.",
        "For every entity, entity_mentions.<name> false REQUIRES facts.<name> null. "
        "Never copy a previously selected value from conversation context into facts; "
        "Core retains prior selections across turns.",
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
        if name in seen_names:
            raise EntitySchemaValidationError(f"duplicate entity field name: {name!r}")
        seen_names.add(name)
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
            if role in UNIQUE_ROLES:
                if role in seen_unique_roles:
                    raise EntitySchemaValidationError(
                        f"entity_schema role {role!r} may be declared only once"
                    )
                seen_unique_roles.add(role)

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
                if entity_id is None or not isinstance(entity_id, (str, int, float, bool)):
                    raise EntitySchemaValidationError(
                        f"entity_schema.fields[{index}] ({name!r}): catalog values "
                        "must be non-null JSON scalar canonical values"
                    )
                phrases.append(label)
                key = label.lower()
                catalog_resolve[key] = entity_id
                alias_map[key] = entity_id
            catalog_phrases = tuple(phrases)

            keys = ", ".join(f'"{p}"' for p in catalog_phrases) or "none provided"
            semantic_evidence: List[str] = []
            raw_items = raw_field.get("items")
            if isinstance(raw_items, list):
                for item in raw_items:
                    if not isinstance(item, Mapping):
                        continue
                    label = str(item.get("name") or "").strip()
                    if not label:
                        continue
                    details = [
                        str(item.get(key)).strip()
                        for key in ("description", "category")
                        if isinstance(item.get(key), str)
                        and str(item.get(key)).strip()
                    ]
                    if details:
                        semantic_evidence.append(f'{label}: {"; ".join(details)}')
            prompt_sections.extend(
                [
                    f"Entity: {name}",
                    f"Type: {field_type}",
                    f"Description: {description}",
                    f"Known catalog phrases (for reference only): {keys}",
                    *(
                        [
                            "Trusted semantic evidence (not identifiers): "
                            + " | ".join(semantic_evidence)
                        ]
                        if semantic_evidence
                        else []
                    ),
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
        "additionalProperties": False,
    }
    return CompiledBusinessEntities(
        prompt_rules="\n".join(prompt_sections).rstrip() + "\n",
        facts_schema=facts_schema,
        fields=tuple(compiled_fields),
        alias_map=alias_map,
        catalog_field_names=catalog_field_names,
    )


def atomic_entity_prompt_rules(compiled: CompiledBusinessEntities) -> str:
    """Prompt rules matching CREATE's atomic per-entity tool representation."""
    sections = [
        "── BUSINESS ENTITY RESULTS ─────────────────────────────────────────────────",
        "Return exactly one status branch for every declared entity in entity_results.",
        "NOT_MENTIONED means the current utterance does not mention or refer to the entity.",
        "MENTIONED_UNRESOLVED means it is mentioned but no safe value can be extracted.",
        "MENTIONED_VALUE requires the raw current-utterance phrase in value.",
        "If the current utterance is exactly one allowed enum value, assign that value "
        "to the matching outstanding enum entity. Do not place an enum token on a "
        "catalog entity unless the utterance also matches that catalog phrase.",
        "Never copy previously selected values from conversation context into entity_results.",
        "When Pending profile request is CUSTOMER_CONTACT_NAME, a linguistically plausible",
        "name supplied by the current user is MENTIONED_VALUE for customer_contact_name and",
        "continues the active booking intent. It is not CONFIRM_ACTION merely because prior",
        "assistant wording mentions confirmation. A genuine competing intent must not be",
        "swallowed. Unusable or placeholder answers are MENTIONED_UNRESOLVED, not invented",
        "names. Do not require Western multi-part name structure; single and international",
        "names may be valid.",
        "If the user declines or cannot provide the currently requested entity, mark it "
        "MENTIONED_UNRESOLVED, add it to declined_entities, and do not invent a value "
        '(e.g. "I don\'t have it" or "I don\'t know").',
        "",
    ]
    for entity in compiled.fields:
        sections.extend([
            f"Entity: {entity.name}",
            f"Type: {entity.type}",
            f"Description: {entity.description}",
        ])
        if entity.type == "enum" and entity.values:
            listed = ", ".join(f'"{v}"' for v in entity.values)
            sections.append(f"Allowed values: {listed}")
        elif entity.type == "catalog" and entity.catalog_phrases:
            keys = ", ".join(f'"{p}"' for p in entity.catalog_phrases)
            sections.append(f"Known catalog phrases (for reference only): {keys}")
        sections.append("")
    return "\n".join(sections).rstrip() + "\n"


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
