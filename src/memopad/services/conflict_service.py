"""Service for detecting and managing observation conflicts.

Inspired by MemGraphRAG's Conflict Detector + Conflict Handler agent pattern.
Instead of autonomous LLM agents, MemoPad uses lightweight rule-based detection and
surfaces conflicts to the LLM via tool output for human-in-the-loop resolution.

Current detection is intentionally simple and should be treated as a first-pass
quality signal, not a final truth source. It flags observations in the same entity
and category when their content diverges beyond a threshold. Future hardening should
prefer explicit conflict markers or a dedicated observation_conflict join table.

Detection logic:
- Two observations on the same entity in the same category with different content
  are flagged as potentially conflicting. The score is the divergence
  (1 - similarity): 1.0 = completely different content, lower for similar.
- Similarity is a character-multiset (Sørensen–Dice) overlap over case-folded,
  whitespace-collapsed content — see `memopad.text_similarity.character_overlap`.
  No embeddings are used today. Wiring the embedding service (Phase 3) for a cosine
  score is deferred: it needs a labeled sample to recalibrate `_CONFLICT_THRESHOLD`
  against, and the character metric is sufficient as a first-pass signal.
- Known limitation: the character-multiset metric ignores word boundaries, so long
  texts that merely share many characters (e.g. two long notes with common filler)
  can false-positive. Replacing it with token-Jaccard or a sequence ratio is tracked
  as future work for the same reason (needs a labeled sample to recalibrate).
- Conflicts are stored bidirectionally: both observations point at each other.
- MemoPad does not auto-resolve conflicts; it records and surfaces them.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

from loguru import logger
from sqlalchemy import case, select, update

from memopad.models.knowledge import Observation
from memopad.repository.observation_repository import ObservationRepository
from memopad.text_similarity import character_overlap
from memopad import db


# --- Threshold ---
# Trigger: content differs by more than this ratio (0–1, lower = more similar)
# Why: avoid flagging minor whitespace edits or capitalisation differences
# Outcome: only meaningfully different content in the same category is flagged
# Tuning note: calibrated against the character-multiset metric; if the metric is
# changed (token-Jaccard / sequence ratio — see module docstring), this threshold
# MUST be recalibrated against a labeled sample, not carried over verbatim.
_CONFLICT_THRESHOLD = 0.15  # flag when divergence (1 - similarity) > threshold


@dataclass
class ConflictResult:
    """A pair of observations that conflict with each other."""

    obs_a_id: int
    obs_b_id: int
    score: float  # 0.0–1.0; 1.0 = completely different content


def _similarity_ratio(a: str, b: str) -> float:
    """Compute a normalised similarity ratio between two strings.

    Case-insensitive and whitespace-collapsed (so "Foo  bar" == "foo bar"), then
    character-multiset overlap via the shared `character_overlap` metric
    (see `memopad.text_similarity`). Identical-after-normalization strings short
    -circuit to 1.0.

    Returns:
        Float in [0, 1] where 1.0 = identical, 0.0 = no common characters.
    """
    a_norm = " ".join(a.lower().split())
    b_norm = " ".join(b.lower().split())

    if a_norm == b_norm:
        return 1.0

    return character_overlap(a_norm, b_norm)


class ConflictService:
    """Detects and records conflicts between observations on the same entity.

    Usage:
        conflict_service = ConflictService(observation_repository)
        conflicts = await conflict_service.detect_and_mark(entity_id, new_observations)
    """

    def __init__(self, observation_repository: ObservationRepository) -> None:
        self.observation_repository = observation_repository

    async def detect_and_mark(
        self,
        entity_id: int,
        new_observations: Sequence[Observation],
        provenance_path: Optional[str] = None,
    ) -> list[ConflictResult]:
        """Detect conflicts between newly written observations and all existing ones.

        Called after observations are persisted so that IDs are available.

        Args:
            entity_id:         The entity whose observations were just written.
            new_observations:  The freshly inserted Observation objects (with IDs).
            provenance_path:   Source file path for grounding; stored on the obs.

        Returns:
            List of ConflictResult pairs that were detected and written to DB.
        """
        if not new_observations:
            return []

        # Fetch all observations currently in DB for this entity, including the
        # newly inserted ones (they are already committed by the caller).
        all_obs = await self.observation_repository.find_by_entity(entity_id)

        # Build a lookup: category → list of observations in that category
        by_category: dict[str, list[Observation]] = {}
        for obs in all_obs:
            by_category.setdefault(obs.category, []).append(obs)

        conflicts: list[ConflictResult] = []

        # Only examine categories that contain more than one observation
        for category, siblings in by_category.items():
            if len(siblings) < 2:
                continue

            # Compare every pair within the same category
            for i, obs_a in enumerate(siblings):
                for obs_b in siblings[i + 1 :]:
                    # Skip pairs that are already resolved
                    if obs_a.conflict_resolved or obs_b.conflict_resolved:
                        continue

                    # Skip if already flagged as each other's conflict
                    if obs_a.conflicting_obs_id == obs_b.id:
                        continue

                    similarity = _similarity_ratio(obs_a.content, obs_b.content)
                    divergence = 1.0 - similarity

                    if divergence > _CONFLICT_THRESHOLD:
                        score = round(divergence, 4)
                        logger.info(
                            f"Conflict detected: obs_id={obs_a.id} vs obs_id={obs_b.id} "
                            f"category='{category}' score={score}"
                        )
                        conflicts.append(
                            ConflictResult(obs_a_id=obs_a.id, obs_b_id=obs_b.id, score=score)
                        )

        if conflicts:
            await self._write_conflicts(conflicts, provenance_path)

        return conflicts

    async def _write_conflicts(
        self,
        conflicts: list[ConflictResult],
        provenance_path: Optional[str],
    ) -> None:
        """Persist conflict flags bidirectionally for all detected pairs.

        Batched into a single UPDATE with CASE expressions rather than 2N round
        trips (one per side per pair). An observation appearing in several
        conflict pairs keeps the last partner/score — same last-write-wins
        semantics as the previous per-row loop. All rows share one
        scoped_session, so the whole flag-write is one transaction/commit.
        """
        if not conflicts:
            return

        # Per-row partner + score maps (bidirectional: a→b and b→a).
        partner: dict[int, int] = {}
        score: dict[int, float] = {}
        for result in conflicts:
            partner[result.obs_a_id] = result.obs_b_id
            partner[result.obs_b_id] = result.obs_a_id
            score[result.obs_a_id] = result.score
            score[result.obs_b_id] = result.score

        async with db.scoped_session(self.observation_repository.session_maker) as session:
            await session.execute(
                update(Observation)
                .where(Observation.id.in_(partner))
                .values(
                    conflict_score=case(
                        *[(Observation.id == k, v) for k, v in score.items()]
                    ),
                    conflicting_obs_id=case(
                        *[(Observation.id == k, v) for k, v in partner.items()]
                    ),
                    conflict_resolved=False,
                    provenance_path=provenance_path,
                )
            )

    async def resolve_conflict(self, observation_id: int) -> None:
        """Mark an observation's conflict as resolved.

        Clears the conflict flag on both sides of the pair so they no longer
        appear in tool output with a warning marker.

        Args:
            observation_id: ID of either observation in a conflicting pair.
        """
        async with db.scoped_session(self.observation_repository.session_maker) as session:
            # Load the observation to find its partner
            result = await session.execute(
                select(Observation).where(Observation.id == observation_id)
            )
            obs = result.scalars().one_or_none()
            if not obs:
                logger.warning(f"resolve_conflict: observation {observation_id} not found")
                return

            partner_id = obs.conflicting_obs_id

            # Clear this observation's conflict
            await session.execute(
                update(Observation)
                .where(Observation.id == observation_id)
                .values(conflict_score=None, conflicting_obs_id=None, conflict_resolved=True)
            )

            # Clear partner's conflict if it still points back
            if partner_id:
                await session.execute(
                    update(Observation)
                    .where(Observation.id == partner_id)
                    .values(conflict_score=None, conflicting_obs_id=None, conflict_resolved=True)
                )

            logger.info(
                f"Conflict resolved: obs_id={observation_id} partner_id={partner_id}"
            )
