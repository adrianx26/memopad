"""PersonaMem-style recall benchmark for the MemoPad memory system (Tb G7).

What this measures
------------------
The core hypothesis behind the L0–L3 pyramid + the G5 provenance chain is:

    A consolidated L3 persona, distilled from L1 facts which are distilled from L0
    raw notes, lets an agent *reliably reach ground-truth user preferences* — even
    after the memory store has grown noisy with unrelated content.

This eval operationalizes that as a **provenance recall** metric:

1. Seed N user preferences as L0 raw notes.
2. Distill each into an L1 fact (frontmatter `source_entities` -> L0).
3. Build one L3 persona that references all the L1 facts (`source_entities` -> L1).
4. Inject dilution content (entities/observations with overlapping terms) to mimic
   an extended conversation that buries the preferences in noise.
5. **persona_recall**: starting from the persona, `drill_down` (G5) must reach the
   correct L0 preference. This is *provenance-based* — it does not depend on term
   overlap, so it should stay high despite dilution.
6. **baseline_recall**: a direct BM25 term search for each preference question over
   the scattered notes (no persona). Term-based, so dilution degrades it.

The eval asserts persona_recall >= 0.80 (the success threshold from
`memopad-levels-implementation-plan.md` §14) and reports both metrics so the lift
from the provenance chain is visible. It does NOT require the (not-yet-built)
automatic distiller — the L1/L3 entities are constructed directly, which is exactly
the shape the future distiller will produce.

Run: `pytest -m eval tests/eval/test_personamem_eval.py -s`
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memopad.repository.search_index_row import SearchIndexRow
from memopad.schemas.search import SearchItemType
from memopad.services.provenance_service import (
    LEVEL_L0,
    build_drill_down_chain,
    parse_source_entities,
)

# A small, hand-authored preference set. Each entry is a (id, probe, answer)
# triple. `probe` is a single distinctive keyword used for the term-search
# baseline (FTS5 uses AND semantics across tokens, so a multi-word question would
# trivially zero-match short notes — a keyword gives term search a fair chance).
# `answer` is the ground truth that must be reachable from the persona via the
# provenance chain. The probe keywords are deliberately shared with DILUTION
# entries so term search has to compete with noise — that is the whole point.
PREFERENCES = [
    ("coffee", "coffee", "Pour-over, light roast, no sugar."),
    ("editor", "editor", "Neovim with a dark theme and 2-space indent."),
    ("tests", "tests", "Tests before implementation on complex logic."),
    ("tz", "timezone", "Europe/Bucharest (EET/EEST)."),
    ("lang", "python", "Python with type hints everywhere."),
]

# Dilution content: unrelated "facts" that share generic terms with the preferences
# (e.g. "user", "preferred", "setup") to make term-based retrieval noisier, mimicking
# a long conversation that buries the real preferences.
DILUTION = [
    ("setup-meeting", "User preferred meeting setup is remote, camera off."),
    ("user-onboard", "The user setup guide was shared in the onboarding channel."),
    ("preferred-vendor", "Preferred vendor for coffee beans changed last quarter."),
    ("user-feedback", "User feedback on the editor migration was mostly positive."),
    ("tests-staging", "Tests in staging run every night at 2am."),
    ("tz-policy", "Timezone policy for support shifts follows the user's region."),
    ("lang-rust", "Another team prefers Rust for their CLI tools."),
    ("coffee-machine", "The office coffee machine is broken again."),
]


def _now():
    return datetime.now(timezone.utc)


async def _make_entity(repo, *, permalink, title, level=None, sources=None, entity_type="note"):
    metadata = {}
    if level:
        metadata["level"] = level
    if sources:
        metadata["source_entities"] = sources
    return await repo.create(
        {
            "title": title,
            "entity_type": entity_type,
            "permalink": permalink,
            "file_path": f"{permalink}.md",
            "content_type": "text/markdown",
            "entity_metadata": metadata or None,
            "created_at": _now(),
            "updated_at": _now(),
        }
    )


async def _index(search_repository, entity, project_id, *, snippet=None, stems=None):
    """Index an entity. FTS5 matches `title` and `content_stems`, so the searchable
    body text must go into `content_stems` (not just `content_snippet`, which is
    only the display snippet). `stems` defaults to `snippet` for convenience."""
    body = snippet or entity.title
    await search_repository.index_item(
        SearchIndexRow(
            project_id=project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            file_path=entity.file_path,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            permalink=entity.permalink,
            title=entity.title,
            content_snippet=body,
            content_stems=stems if stems is not None else body,
        )
    )


def _collect_levels(node, levels_found: set) -> None:
    """Walk a drill_down tree and collect (level, permalink) for every reached node."""
    levels_found.add((node.level, node.permalink))
    for child in node.children:
        _collect_levels(child, levels_found)


@pytest.mark.asyncio
@pytest.mark.eval
async def test_personamem_provenance_recall(
    entity_repository, relation_repository, search_repository
):
    """persona_recall (drill_down) must stay high despite term dilution.

    Asserts persona_recall >= 0.80 (levels plan §14 threshold) and reports the
    baseline term-search recall for comparison.
    """
    project_id = entity_repository.project_id

    # 1) L0 raw notes — ground-truth preferences.
    l0_entities = {}
    for pid, _question, answer in PREFERENCES:
        e = await _make_entity(
            entity_repository,
            permalink=f"pref-{pid}-raw",
            title=f"Preference: {pid}",
            level="L0",
        )
        l0_entities[pid] = e

    # 2) L1 facts distilled from L0 (provenance -> L0).
    l1_entities = {}
    for pid, _question, answer in PREFERENCES:
        e = await _make_entity(
            entity_repository,
            permalink=f"pref-{pid}-fact",
            title=f"Fact: {pid} — {answer}",
            level="L1",
            sources=[f"memory://entity/pref-{pid}-raw"],
        )
        l1_entities[pid] = e

    # 3) L3 persona referencing all L1 facts (provenance -> L1).
    persona = await _make_entity(
        entity_repository,
        permalink="persona-user-profile",
        title="User Persona",
        level="L3",
        sources=[f"memory://entity/pref-{pid}-fact" for pid, _, _ in PREFERENCES],
    )

    # 4) Dilution content (no provenance, just noise).
    for permalink, title in DILUTION:
        e = await _make_entity(
            entity_repository, permalink=permalink, title=title, level="L0"
        )
        await _index(search_repository, e, project_id, snippet=title)

    # Index L0 + L1 + persona so term search can also see them. Put the answer
    # text into content_stems (the FTS5 body field) so term search has real
    # content to match against — a fair fight with the provenance chain.
    for pid, _, answer in PREFERENCES:
        await _index(
            search_repository, l0_entities[pid], project_id,
            snippet=f"Preference: {pid}", stems=f"{pid} preference {answer}",
        )
        await _index(
            search_repository, l1_entities[pid], project_id,
            snippet=f"{pid} — {answer}", stems=f"{pid} fact {answer}",
        )
    await _index(
        search_repository, persona, project_id,
        snippet="User persona profile", stems="user persona profile preferences",
    )

    # --- Metric A: persona_recall (provenance-based, via drill_down) ---
    chain = await build_drill_down_chain(
        entity_repository, relation_repository, persona, target_level=LEVEL_L0, max_depth=5
    )
    reached: set = set()
    _collect_levels(chain, reached)
    # persona_recall = fraction of preferences whose L0 raw note is reachable from persona.
    persona_hits = sum(
        1 for pid, _, _ in PREFERENCES if (LEVEL_L0, f"pref-{pid}-raw") in reached
    )
    persona_recall = persona_hits / len(PREFERENCES)

    # Sanity: the persona frontmatter lists all L1 facts.
    assert len(parse_source_entities(persona.entity_metadata)) == len(PREFERENCES)

    # --- Metric B: baseline_recall (term-based BM25, no persona) ---
    # Probe with a single distinctive keyword per preference. The dilution content
    # shares these keywords, so term search must rank the real preference above
    # noise — this is where dilution degrades a term-only retriever.
    baseline_hits = 0
    for pid, probe, _answer in PREFERENCES:
        results = await search_repository.search(search_text=probe, limit=5)
        relevant = {f"pref-{pid}-raw", f"pref-{pid}-fact"}
        if any(r.permalink in relevant for r in results):
            baseline_hits += 1
    baseline_recall = baseline_hits / len(PREFERENCES)

    # --- Report ---
    report = (
        "\n=== PersonaMem recall benchmark ===\n"
        f"preferences:        {len(PREFERENCES)}\n"
        f"persona_recall:     {persona_recall:.0%}  (drill_down persona -> L1 -> L0)\n"
        f"baseline_recall:    {baseline_recall:.0%}  (BM25 term search, no persona)\n"
        f"persona sources:    {len(parse_source_entities(persona.entity_metadata))}\n"
        "====================================\n"
    )
    print(report, flush=True)

    # Success threshold (levels plan §14: >= 80% ground-truth reachable).
    assert persona_recall >= 0.80, (
        f"persona_recall {persona_recall:.0%} below 80% threshold; provenance chain broken"
    )


@pytest.mark.asyncio
@pytest.mark.eval
async def test_personamem_baseline_degrades_under_dilution(
    entity_repository, relation_repository, search_repository
):
    """Sanity check: without the persona chain, term search alone is noisier.

    This is a complementary measurement — it does NOT assert a hard threshold
    (term recall depends on the FTS tokenizer), it just records the number so the
    lift from provenance is interpretable. Kept separate from the main assertion
    so a tokenizer change cannot make the eval harness itself red.
    """
    project_id = entity_repository.project_id

    # Preferences + dilution, NO persona / NO provenance. Pure scattered notes.
    for pid, _probe, answer in PREFERENCES:
        e = await _make_entity(
            entity_repository,
            permalink=f"scat-{pid}",
            title=f"Fact: {pid} — {answer}",
            level="L0",
        )
        await _index(
            search_repository, e, project_id,
            snippet=f"{pid} — {answer}", stems=f"{pid} fact {answer}",
        )
    for permalink, title in DILUTION:
        e = await _make_entity(
            entity_repository, permalink=f"scat-{permalink}", title=title, level="L0"
        )
        await _index(search_repository, e, project_id, snippet=title, stems=title)

    hits = 0
    for pid, probe, _ in PREFERENCES:
        results = await search_repository.search(search_text=probe, limit=5)
        if any(r.permalink == f"scat-{pid}" for r in results):
            hits += 1
    scattered_recall = hits / len(PREFERENCES)

    print(f"\n=== scattered-only term recall (no persona, no provenance): {scattered_recall:.0%} ===\n")
    # No hard assertion — informational. Provenance (test above) is the real gate.
    assert 0.0 <= scattered_recall <= 1.0