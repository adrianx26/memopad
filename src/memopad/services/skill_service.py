"""Skill asset service (Tb G1).

A **Skill** is an orthogonal asset type — not a fifth memory level — modelling
*reusable, versioned expertise*: "what the agent has learned to do well", as
opposed to facts ("what it knows"). It is modelled without any DB migration by
reusing the existing Entity/Observation machinery:

- `entity_type = "skill"`  (free string column; no enum/registry to extend)
- versioning + lifecycle live in `entity_metadata`:
  - `skill_version: int`        (>= 1)
  - `skill_status: "draft" | "validated" | "deprecated"`
- the structured trigger / steps / validation / guard-rail content lives in
  **observations** with canonical categories `[trigger]`, `[step]`,
  `[validation]`, `[when]`, `[do]`, `[don't]`. These categories auto-register via
  `SchemaService.normalize_category` on first use — no hardcoded list needed.

Provenance is reused from G5: a new skill version points its `source_entities`
at the previous version (and at any incident/learnings), so `drill_down` can
trace a validated skill back to the evidence it was distilled from.

This module is **pure domain logic** — no file I/O, no HTTP, no SQLAlchemy. It
takes already-fetched entity/observation objects and reasons about them. The
router endpoints and MCP tools compose this with the existing entity CRUD for
persistence (files remain the source of truth). Gated behind `skills_enabled`
(default off) at the integration boundary so existing flows are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# --- Canonical constants ---------------------------------------------------
#
# These are the only "schema" a Skill carries. Nothing here is persisted as a
# new column — `entity_type` is a free string and the rest are JSON keys in
# `entity_metadata`, both of which already exist on Entity.

SKILL_ENTITY_TYPE: str = "skill"

# entity_metadata keys
SKILL_STATUS_KEY: str = "skill_status"
SKILL_VERSION_KEY: str = "skill_version"

# Lifecycle statuses
STATUS_DRAFT: str = "draft"
STATUS_VALIDATED: str = "validated"
STATUS_DEPRECATED: str = "deprecated"
VALID_STATUSES: frozenset[str] = frozenset({STATUS_DRAFT, STATUS_VALIDATED, STATUS_DEPRECATED})

# Skill observation categories (auto-registered on first use by SchemaService).
# `trigger` / `step` / `validation` are the Tb triple; `when` / `do` /
# `don't` are the optional guard-rail categories from the implementation plan.
CATEGORY_TRIGGER: str = "trigger"
CATEGORY_STEP: str = "step"
CATEGORY_VALIDATION: str = "validation"
CATEGORY_WHEN: str = "when"
CATEGORY_DO: str = "do"
CATEGORY_DONT: str = "don't"

# A skill is only "validated" once it has at least one observation in each of
# these required categories — the structural gate `validate_skill` enforces.
REQUIRED_CATEGORIES: tuple[str, ...] = (CATEGORY_TRIGGER, CATEGORY_STEP, CATEGORY_VALIDATION)
OPTIONAL_CATEGORIES: tuple[str, ...] = (CATEGORY_WHEN, CATEGORY_DO, CATEGORY_DONT)

# Ranking: a validated skill is boosted as if it were a high-confidence L2
# scenario. Applied in ContextService only when `skills_enabled`.
SKILL_BOOST_MULTIPLIER: float = 2.0


class SkillError(ValueError):
    """Fail-fast error for malformed skill payloads (AGENTS.md: no fallback)."""


@dataclass
class SkillDetail:
    """Structured view of a skill entity for rendering."""

    external_id: str
    title: str
    permalink: Optional[str]
    file_path: str
    skill_version: int
    skill_status: str
    source_entities: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    validations: List[str] = field(default_factory=list)
    when: List[str] = field(default_factory=list)
    do: List[str] = field(default_factory=list)
    dont: List[str] = field(default_factory=list)
    other_observations: List[tuple[str, str]] = field(default_factory=list)


# --- Predicates / accessors ------------------------------------------------


def is_skill_entity(entity: Any) -> bool:
    """True if the given ORM/Pydantic entity has `entity_type == 'skill'`.

    Defensive against missing attribute — callers may pass ORM models, Pydantic
    response schemas, or dicts; only the entity_type string matters.
    """
    et = getattr(entity, "entity_type", None)
    if et is None and isinstance(entity, Mapping):
        et = entity.get("entity_type")
    return et == SKILL_ENTITY_TYPE


def skill_status(entity_metadata: Optional[Mapping[str, Any]]) -> str:
    """Return the skill status, defaulting to STATUS_DRAFT when absent.

    A skill with no recorded status is conceptually a draft — `validate_skill`
    is the only path that sets `validated`, so absence ⇒ not-yet-validated.
    """
    if not entity_metadata:
        return STATUS_DRAFT
    return entity_metadata.get(SKILL_STATUS_KEY, STATUS_DRAFT)


def skill_version(entity_metadata: Optional[Mapping[str, Any]]) -> int:
    """Return the skill version, defaulting to 1 when absent."""
    if not entity_metadata:
        return 1
    return int(entity_metadata.get(SKILL_VERSION_KEY, 1))


def is_validated_skill(entity: Any) -> bool:
    """True if the entity is a skill whose status is `validated`."""
    return is_skill_entity(entity) and skill_status(getattr(entity, "entity_metadata", None)) == STATUS_VALIDATED


# --- Payload validation (fail-fast) ----------------------------------------


def validate_skill_payload(
    entity_metadata: Optional[Mapping[str, Any]],
    *,
    skills_enabled: bool,
) -> None:
    """Fail-fast invariant on a skill's metadata, mirroring G5's provenance guard.

    No-op unless `skills_enabled` is on — so turning the flag off restores the
    pre-skill behaviour exactly (a `type: skill` note is just a note).

    Raises:
        SkillError: if `skill_status` is not a known status or `skill_version`
            is not a positive integer.
    """
    if not skills_enabled:
        return
    if not entity_metadata:
        return  # a skill with no metadata is a draft v1 — allowed.

    status = entity_metadata.get(SKILL_STATUS_KEY)
    if status is not None and status not in VALID_STATUSES:
        raise SkillError(
            f"skill_status must be one of {sorted(VALID_STATUSES)}, got '{status}'"
        )

    version = entity_metadata.get(SKILL_VERSION_KEY)
    if version is not None:
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise SkillError(f"skill_version must be a positive integer, got {version!r}")


# --- Structural validation (the `validate_skill` gate) ---------------------


def group_skill_observations(observations: Sequence[Any]) -> Dict[str, List[str]]:
    """Group a skill's observations by canonical category.

    Returns a dict keyed by every canonical category (always present, possibly
    empty) plus an `other` bucket of (category, content) pairs for anything
    outside the canonical set. Observations are matched by normalized category
    (lowercased, stripped) so `[Step]` / `[STEP]` collapse onto `step`.
    """
    grouped: Dict[str, List[str]] = {c: [] for c in REQUIRED_CATEGORIES + OPTIONAL_CATEGORIES}
    grouped["other"] = []  # type: ignore[assignment]  # list of (category, content) tuples

    for obs in observations:
        category = (getattr(obs, "category", None) or "").strip().lower()
        content = getattr(obs, "content", None) or ""
        if category in grouped and category != "other":
            grouped[category].append(content)
        else:
            grouped["other"].append((category, content))  # type: ignore[arg-type]
    return grouped


import re as _re

_TOKEN_RE = _re.compile(r"[A-Za-z0-9]+")


def _significant_tokens(text: str) -> set:
    """Lowercased alphanumeric word tokens of length >= 3 from `text`.

    Short tokens (e.g. "db", "id", path prefixes like "v2") are dropped to keep
    the trigger match signal-to-noise high. Returns a set for direct
    intersection. Used only by `match_trigger`.
    """
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "") if len(tok) >= 3}


def match_trigger(triggers: Sequence[str], text: str) -> bool:
    """Return True iff any significant token (>= 3 chars) of `text` is also a
    significant token of any trigger string.

    Tb G1: ContextService uses this to decide whether a validated skill's
    ``[trigger]`` observation matches the topic of a `build_context` call, so a
    skill that did *not* surface in primary search can still be injected when its
    trigger is relevant. Pure + deterministic (token-set intersection,
    case-insensitive) so it is unit-testable without a DB.

    Token-boundary matching (not bare substring): a 3-char topic token like
    ``test`` only matches a trigger that contains the *word* ``test``, not one
    that merely contains "test" as a substring of "latest". Both sides are
    tokenized on >= 3-char words, so a focused topic like "reset-db" matches a
    broad trigger like "user asks to reset DB in prod" via the shared word
    "reset", while noise like "test" ⊂ "latest" is excluded. `None`/empty on
    either side is a clean False (no tokenization of None).
    """
    if not text or not triggers:
        return False
    topic_tokens = _significant_tokens(text)
    if not topic_tokens:
        return False
    for trig in triggers:
        if not trig:
            continue
        if topic_tokens & _significant_tokens(trig):
            return True
    return False


@dataclass
class ValidationResult:
    """Outcome of a structural validation check."""

    ok: bool
    missing: List[str] = field(default_factory=list)
    present: List[str] = field(default_factory=list)


def structural_validation(grouped: Dict[str, List[str]]) -> ValidationResult:
    """Check the Tb triple is present: >= 1 trigger, 1 step, 1 validation.

    This is the *structural* gate. The implementation plan's LLM-based
    verification (do the steps actually cover the trigger? does the validation
    rule hold?) is deferred — recorded as a follow-up. Structural completeness
    is the deterministic precondition for any LLM check.
    """
    missing: List[str] = []
    present: List[str] = []
    for cat in REQUIRED_CATEGORIES:
        if grouped.get(cat):
            present.append(cat)
        else:
            missing.append(cat)
    return ValidationResult(ok=not missing, missing=missing, present=present)


# --- Rendering -------------------------------------------------------------


def build_skill_body(
    *,
    trigger: str,
    steps: Sequence[str],
    validation: str,
    when: Optional[str] = None,
    do: Optional[str] = None,
    dont: Optional[str] = None,
) -> str:
    """Build the markdown **body** for a skill entity (observation lines only).

    The frontmatter (type/skill_version/skill_status/source_entities/tags) is
    produced separately by `schema_to_markdown` from `entity_metadata`, so this
    function emits only the `- [category] content` observation lines plus any
    relations the caller may append. Steps are numbered for readability.
    """
    lines: List[str] = []
    lines.append(f"- [{CATEGORY_TRIGGER}] {trigger.strip()}")
    for i, step in enumerate(steps, start=1):
        step = step.strip()
        # If the caller already prefixed a number, don't double-number.
        prefix = "" if step[:2].rstrip(".").isdigit() else f"{i}. "
        lines.append(f"- [{CATEGORY_STEP}] {prefix}{step}")
    lines.append(f"- [{CATEGORY_VALIDATION}] {validation.strip()}")
    if when:
        lines.append(f"- [{CATEGORY_WHEN}] {when.strip()}")
    if do:
        lines.append(f"- [{CATEGORY_DO}] {do.strip()}")
    if dont:
        lines.append(f"- [{CATEGORY_DONT}] {dont.strip()}")
    return "\n".join(lines)


def build_skill_detail(entity: Any, observations: Sequence[Any]) -> SkillDetail:
    """Assemble a SkillDetail from an entity + its observations."""
    md = getattr(entity, "entity_metadata", None) or {}
    grouped = group_skill_observations(observations)
    return SkillDetail(
        external_id=getattr(entity, "external_id", ""),
        title=getattr(entity, "title", ""),
        permalink=getattr(entity, "permalink", None),
        file_path=getattr(entity, "file_path", ""),
        skill_version=skill_version(md),
        skill_status=skill_status(md),
        source_entities=list(md.get("source_entities", []) or []),
        triggers=grouped[CATEGORY_TRIGGER],
        steps=grouped[CATEGORY_STEP],
        validations=grouped[CATEGORY_VALIDATION],
        when=grouped[CATEGORY_WHEN],
        do=grouped[CATEGORY_DO],
        dont=grouped[CATEGORY_DONT],
        other_observations=[(c, t) for c, t in grouped["other"]],  # type: ignore[misc]
    )


def render_skill_detail(detail: SkillDetail) -> str:
    """Render a SkillDetail as a Markdown document for MCP tool output."""
    status_line = f"status: `{detail.skill_status}`"
    version_line = f"version: {detail.skill_version}"
    header = [
        f"# Skill: {detail.title}",
        f"permalink: `{detail.permalink}`" if detail.permalink else "permalink: _(none)_",
        f"file: `{detail.file_path}`",
        f"{version_line}  |  {status_line}",
    ]
    if detail.source_entities:
        header.append(f"sources: {len(detail.source_entities)}  (drill_down to trace)")

    sections: List[str] = []
    if detail.triggers:
        sections.append("## Trigger\n" + "\n".join(f"- {t}" for t in detail.triggers))
    if detail.steps:
        sections.append("## Steps\n" + "\n".join(f"- {s}" for s in detail.steps))
    if detail.validations:
        sections.append("## Validation\n" + "\n".join(f"- {v}" for v in detail.validations))
    if detail.when:
        sections.append("## When\n" + "\n".join(f"- {w}" for w in detail.when))
    if detail.do:
        sections.append("## Do\n" + "\n".join(f"- {d}" for d in detail.do))
    if detail.dont:
        sections.append("## Don't\n" + "\n".join(f"- {d}" for d in detail.dont))
    if detail.other_observations:
        sections.append(
            "## Other observations\n"
            + "\n".join(f"- [{c}] {t}" for c, t in detail.other_observations)
        )

    return "\n".join(header) + ("\n\n" + "\n\n".join(sections) if sections else "")


def render_validation_result(detail: SkillDetail, result: ValidationResult) -> str:
    """Render the outcome of `validate_skill`."""
    lines = [
        f"# Validation: {detail.title}",
        f"version: {detail.skill_version}  |  status: `{detail.skill_status}`",
    ]
    if result.ok:
        lines.append(
            "✅ **Structurally valid** — has >= 1 trigger, >= 1 step, >= 1 validation. "
            "Status set to `validated`."
        )
    else:
        lines.append(
            f"❌ **Incomplete** — missing required categories: "
            f"{', '.join(f'[{m}]' for m in result.missing)}. "
            f"Present: {', '.join(f'[{p}]' for p in result.present) or 'none'}."
        )
        lines.append(
            "_LLM verification (do the steps cover the trigger? does the validation "
            "rule hold?) is deferred — structural completeness is the precondition._"
        )
    return "\n".join(lines)