"""Local, bounded relation and tag suggestions from the existing search index."""

import re
from collections import Counter, defaultdict

from memopad.repository.entity_alias_repository import EntityAliasRepository
from memopad.schemas.search import SearchItemType, SearchQuery
from memopad.services.local_decision_provider import (
    DecisionBatch,
    DecisionProvider,
    DecisionQuestion,
)
from memopad.services.search_service import SearchService

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
_QUERY_STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "both",
    "but",
    "can",
    "could",
    "does",
    "each",
    "for",
    "from",
    "have",
    "into",
    "just",
    "more",
    "most",
    "note",
    "not",
    "only",
    "other",
    "our",
    "out",
    "over",
    "should",
    "some",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "very",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "you",
    "your",
    "the",
    "and",
    "or",
}
_MAX_QUERY_TERMS = 10
_MAX_CANDIDATES = 30
_MAX_RELATIONS = 15
_MAX_TAGS = 5
_MAX_DECISION_CANDIDATES = 29
_MENTION_CONTEXT_CHARS = 240


def _query_terms(content: str) -> list[str]:
    counts = Counter(
        token.lower()
        for token in _TOKEN_RE.findall(content)
        if token.lower() not in _QUERY_STOP_WORDS
    )
    return [
        term
        for term, _ in sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[
            :_MAX_QUERY_TERMS
        ]
    ]


def _mention_count(content: str, phrase: str) -> int:
    if len(phrase.strip()) < 3:
        return 0
    pattern = rf"(?<!\w){re.escape(phrase.strip())}(?!\w)"
    return len(re.findall(pattern, content, flags=re.IGNORECASE))


def _tags(metadata: dict | None) -> list[str]:
    if not metadata:
        return []
    values = metadata.get("tags", [])
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values.strip("[] "))
    if not isinstance(values, (list, tuple, set)):
        return []
    return [str(value).strip().lower() for value in values if str(value).strip()]


class LocalExtractionService:
    """Use FTS candidates and explicit aliases to return actionable suggestions."""

    def __init__(
        self,
        search_service: SearchService,
        alias_repository: EntityAliasRepository,
        decision_provider: DecisionProvider | None = None,
    ) -> None:
        self.search_service = search_service
        self.alias_repository = alias_repository
        self.decision_provider = decision_provider

    async def suggest(self, content: str, *, source_entity_id: int) -> dict:
        """Find mentioned entities and tags common to indexed, related notes."""
        terms = _query_terms(content)
        if not terms:
            return {"relations": [], "tags": []}

        rows = await self.search_service.search(
            SearchQuery(
                text=" OR ".join(f'"{term}"' for term in terms),
                entity_types=[SearchItemType.ENTITY],
            ),
            limit=_MAX_CANDIDATES,
        )
        candidates = [
            row
            for row in rows
            if row.type == SearchItemType.ENTITY.value
            and row.id != source_entity_id
            and row.permalink is not None
        ]
        aliases = await self.alias_repository.find_by_entities([row.id for row in candidates])
        aliases_by_entity: dict[int, list[str]] = defaultdict(list)
        for alias in aliases:
            aliases_by_entity[alias.entity_id].append(alias.alias)

        relations = []
        tag_support: Counter[str] = Counter()
        entity_count = 0
        for row in candidates:
            entity_count += 1
            for tag in set(_tags(row.metadata)):
                tag_support[tag] += 1

            mentions = []
            if row.title:
                mentions.append(row.title)
            mentions.extend(aliases_by_entity.get(row.id, []))
            matched = [(phrase, _mention_count(content, phrase)) for phrase in mentions]
            matched = [(phrase, count) for phrase, count in matched if count]
            if matched:
                phrase, count = max(matched, key=lambda item: (item[1], len(item[0])))
                relations.append(
                    {
                        "title": row.title,
                        "permalink": row.permalink,
                        "matched_text": phrase,
                        "confidence": 0.9 if count > 1 else 0.75,
                    }
                )

        relations.sort(key=lambda item: (-item["confidence"], item["title"] or ""))
        tags = (
            [
                {
                    "tag": tag,
                    "supporting_entities": count,
                    "confidence": round(count / max(entity_count, 2), 2),
                }
                for tag, count in tag_support.most_common(_MAX_TAGS)
            ]
            if entity_count
            else []
        )
        return {"relations": relations[:_MAX_RELATIONS], "tags": tags}

    async def _decide_relation(
        self,
        content: str,
        *,
        mention: str,
        candidates: list[dict],
    ) -> dict | None:
        """Choose among locally retrieved relation candidates, with explicit abstention.

        This is a read-only decision seam. Candidate discovery remains local and
        deterministic; the provider cannot supply a new entity or write a relation.
        Returned probabilities are model scores conditional on this option set,
        not calibrated correctness estimates.
        """
        if self.decision_provider is None:
            raise RuntimeError("A local decision provider is required for relation decisions")
        if not mention.strip() or mention.casefold() not in content.casefold():
            raise ValueError("mention must be a non-empty phrase present in the source content")
        if not candidates:
            return None
        if len(candidates) > _MAX_DECISION_CANDIDATES:
            raise ValueError(
                f"relation decisions are limited to {_MAX_DECISION_CANDIDATES} candidates"
            )

        choices: dict[str, str | None] = {}
        candidate_by_choice: dict[str, dict] = {}
        for index, candidate in enumerate(candidates):
            title = candidate.get("title")
            permalink = candidate.get("permalink")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("every relation candidate must have a non-empty title")
            if not isinstance(permalink, str) or not permalink.strip():
                raise ValueError("every relation candidate must have a non-empty permalink")
            choice_id = f"candidate_{index}"
            choices[choice_id] = f"{title.strip()} (permalink: {permalink.strip()})"
            candidate_by_choice[choice_id] = candidate
        choices["none"] = "None of these entities is the intended relation target."

        question = DecisionQuestion(
            id="relation_target",
            type="choice",
            instructions=(
                "Select the existing entity referred to by the mention in this note excerpt. "
                "Choose none if the excerpt does not identify one of the listed entities."
            ),
            criteria=choices,
        )
        batch = DecisionBatch(
            state=_mention_context(content, mention),
            questions=[question],
        )
        result = await self.decision_provider.decide(batch)
        answer = result.answers.get(question.id)
        if (
            answer is None
            or answer.question_id != question.id
            or answer.type != "choice"
            or not isinstance(answer.value, str)
        ):
            raise ValueError("decision provider returned no valid relation choice")
        if answer.value == "none":
            return None
        selected = candidate_by_choice.get(answer.value)
        if selected is None:
            raise ValueError("decision provider selected an unknown relation candidate")

        probabilities = answer.probabilities
        selected_probability = (
            probabilities.get(answer.value) if isinstance(probabilities, dict) else None
        )
        return {
            "candidate": selected,
            "model": result.model,
            "selected_probability": selected_probability,
        }

    async def suggest_relation_target(
        self,
        content: str,
        *,
        mention: str,
        source_entity_id: int,
    ) -> dict | None:
        """Retrieve local mention matches, then let the local provider choose or abstain."""
        suggestions = await self.suggest(content, source_entity_id=source_entity_id)
        candidates = [
            candidate
            for candidate in suggestions["relations"]
            if candidate["matched_text"].casefold() == mention.strip().casefold()
        ]
        if not candidates:
            return None
        return await self._decide_relation(content, mention=mention, candidates=candidates)


def _mention_context(content: str, mention: str) -> str:
    """Return a bounded excerpt centered on the first case-insensitive mention."""
    start = content.casefold().find(mention.casefold())
    if start < 0:
        raise ValueError("mention must be present in content")
    half_window = _MENTION_CONTEXT_CHARS // 2
    excerpt_start = max(0, start - half_window)
    excerpt_end = min(len(content), start + len(mention) + half_window)
    excerpt = content[excerpt_start:excerpt_end]
    if excerpt_start:
        excerpt = f"...{excerpt}"
    if excerpt_end < len(content):
        excerpt = f"{excerpt}..."
    return f"Source note excerpt: {excerpt}\nMention: {mention.strip()}"
