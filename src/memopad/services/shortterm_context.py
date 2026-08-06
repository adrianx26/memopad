"""In-task short-term context layering (Tb G6).

The L0–L3 levels plan is *long-term* memory. Tb adds a **separate**,
session-scoped context stack that lives alongside it — three layers, from raw to
condensed, that keep an agent's working context small as a session grows:

    refs/*.md   raw tool outputs         (ground truth, drill-downable)
    steps.jsonl one-line summaries        (one per tool call)
    canvas.mmd  condensed Mermaid graph   (top layer — what the agent sees)

Offload policy (the contribution over the manual plan): as the raw session
content grows toward the context-window budget, layers drop out of the *injected*
context but never off disk — so anything removed from the active context is still
reachable by `drill_down(node_id)`. The ratios come straight from Tb's
validated parameters:

    raw >= 0.50 * budget  →  mild:   inject steps only (refs offloaded to disk)
    raw >= 0.85 * budget  →  aggressive: regenerate canvas.mmd (capped at
                                       0.20 * budget) and inject it as the
                                       primary layer; steps become drill-downable.

Scope / non-breaking notes
--------------------------
- The whole feature is gated by ``shortterm_enabled`` (default off). The service
  itself is policy + local file I/O only; it touches no DB, no HTTP, no L0–L3
  flow. The MCP tool (``shortterm.py``) is the only caller and it checks the flag.
- Step summaries are **caller-supplied** (the agent/tool that performed the step
  passes the one-line summary). The plan calls for an LLM to produce these; that
  summarizer is a future seam, exactly like the G3 distiller callback — wiring it
  now would mean guessing at content, which AGENTS.md forbids. The deterministic
  policy is the part worth building today.
- Filesystem I/O is synchronous and on small local session files. The MCP tool is
  async but calls these brief sync ops directly, matching how ``canvas.py`` does
  sync JSON work inside its async tool.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

from memopad.config import MemoPadConfig

# --- Layer file names ------------------------------------------------------

REFS_DIR = "refs"
STEPS_FILE = "steps.jsonl"
CANVAS_FILE = "canvas.mmd"

# Offload levels (the layer that is *injected* shrinks as the level rises).
LEVEL_NONE = "none"
LEVEL_MILD = "mild"
LEVEL_AGGRESSIVE = "aggressive"

# Default ratios mirror Tb's validated parameters; all are configurable.
DEFAULT_MILD_OFFLOAD_RATIO = 0.5
DEFAULT_AGGRESSIVE_COMPRESS_RATIO = 0.85
DEFAULT_MMD_MAX_TOKEN_RATIO = 0.2


class ShortTermError(ValueError):
    """Fail-fast error for invalid session input (unsafe names, bad node ids)."""


# --- Token accounting ------------------------------------------------------

TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token, never zero for non-empty text).

    Deliberately crude: the offload *ratios* are what matter, not exact counts,
    and a precise tokenizer would make the policy non-deterministic across
    environments. Good enough to drive the 0.5/0.85 thresholds.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# --- Config projection -----------------------------------------------------


@dataclass
class ShortTermConfig:
    """Numeric policy projected from `MemoPadConfig` (Tb G6).

    `token_budget == 0` means "store layers but never compress" — the offload
    policy is a no-op, so the feature degrades to pure session bookkeeping.
    """

    token_budget: int
    mild_offload_ratio: float
    aggressive_compress_ratio: float
    mmd_max_token_ratio: float

    @classmethod
    def from_app_config(cls, cfg: MemoPadConfig) -> "ShortTermConfig":
        return cls(
            token_budget=cfg.shortterm_context_token_budget,
            mild_offload_ratio=cfg.shortterm_mild_offload_ratio,
            aggressive_compress_ratio=cfg.shortterm_aggressive_compress_ratio,
            mmd_max_token_ratio=cfg.shortterm_mmd_max_token_ratio,
        )


# --- Records ---------------------------------------------------------------


@dataclass
class StepRecord:
    """One summarized tool-call step. The Mermaid node id is ``s{index}``."""

    index: int
    tool: str
    summary: str
    ref_name: Optional[str]  # backing raw ref stem (no .md), if any
    token_estimate: int
    timestamp: str  # caller-supplied (keeps the engine off datetime.now)

    def node_id(self) -> str:
        return f"s{self.index}"

    def to_json(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: Dict[str, object]) -> "StepRecord":
        return cls(
            index=int(d["index"]),
            tool=str(d["tool"]),
            summary=str(d["summary"]),
            ref_name=d.get("ref_name"),  # type: ignore[arg-type]
            token_estimate=int(d["token_estimate"]),
            timestamp=str(d["timestamp"]),
        )


@dataclass
class RefRecord:
    """A raw tool-output ref stored under ``refs/{name}.md``."""

    name: str
    content: str
    token_estimate: int


# --- Pure policy functions -------------------------------------------------


def offload_level_for(raw_tokens: int, config: ShortTermConfig) -> str:
    """Decide which layers to inject, given the raw session size.

    `raw_tokens` is refs + steps. The aggressive threshold is checked first so a
    wide gap between the two ratios still selects the higher level. A zero budget
    disables compression entirely (store-only).
    """
    if config.token_budget <= 0:
        return LEVEL_NONE
    if raw_tokens >= config.aggressive_compress_ratio * config.token_budget:
        return LEVEL_AGGRESSIVE
    if raw_tokens >= config.mild_offload_ratio * config.token_budget:
        return LEVEL_MILD
    return LEVEL_NONE


# A ref/stem name must be a plain filename: no path separators, no dots-only,
# no control chars. Anything else is rejected up front (fail-fast) so a caller
# can never escape the session's refs/ directory.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def safe_ref_name(name: str) -> str:
    """Validate a ref name; return it unchanged or raise ShortTermError.

    Rejects empty, path-bearing, dotfile, and traversal-style names so the name
    composes safely into ``refs/{name}.md``.
    """
    if not name or not _SAFE_NAME.match(name):
        raise ShortTermError(
            f"unsafe ref name {name!r}: must be a plain filename "
            "(letters/digits/_-. only, no path separators or leading dot)"
        )
    return name


def _mmd_escape(text: str) -> str:
    """Escape characters that break Mermaid node labels."""
    return text.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_mermaid(steps: List[StepRecord], max_tokens: int) -> str:
    """Render steps as a sequential Mermaid flowchart, capped at `max_tokens`.

    Nodes are greedily included while the rendered output stays under the cap; if
    some steps are dropped, a final ``…N more`` node points the agent at
    ``drill_down``. ``max_tokens <= 0`` means unlimited (used in tests / when the
    cap is disabled). Each node's label is the step's one-line summary.
    """
    lines: List[str] = ["flowchart TD"]
    prev: Optional[str] = None
    included = 0

    for step in steps:
        nid = step.node_id()
        label = _mmd_escape(_truncate(f"{step.tool}: {step.summary}", 80))
        node_line = f'    {nid}["{label}"]'
        edge_line = f"    {prev} --> {nid}" if prev is not None else None

        candidate = lines + [node_line] + ([edge_line] if edge_line else [])
        if max_tokens > 0 and included > 0 and estimate_tokens("\n".join(candidate)) > max_tokens:
            break  # this step would blow the cap — stop before adding it
        lines.append(node_line)
        if edge_line is not None:
            lines.append(edge_line)
        prev = nid
        included += 1

    if included < len(steps):
        remaining = len(steps) - included
        lines.append(f'    sMore["… {remaining} more steps — use drill_down to expand"]')

    return "\n".join(lines)


# --- The service (file I/O + orchestration) --------------------------------


class ShortTermContext:
    """A file-backed, session-scoped 3-layer context stack.

    Layout under ``session_dir``::

        refs/*.md     raw tool outputs
        steps.jsonl   one StepRecord per line
        canvas.mmd    condensed Mermaid (regenerated on aggressive offload)

    All state lives on disk so a session survives process restarts; the service
    holds no mutable in-memory state beyond the config. Operations are
    idempotent reads of whatever is currently on disk.
    """

    def __init__(
        self,
        session_dir: Path,
        config: ShortTermConfig,
        *,
        token_counter: TokenCounter = estimate_tokens,
    ):
        self.session_dir = Path(session_dir)
        self.config = config
        self._tokens = token_counter
        self._refs_dir = self.session_dir / REFS_DIR
        self._steps_file = self.session_dir / STEPS_FILE
        self._canvas_file = self.session_dir / CANVAS_FILE
        self._refs_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    # --- paths ---

    @property
    def refs_dir(self) -> Path:
        return self._refs_dir

    @property
    def steps_file(self) -> Path:
        return self._steps_file

    @property
    def canvas_file(self) -> Path:
        return self._canvas_file

    def _ref_path(self, name: str) -> Path:
        return self._refs_dir / f"{safe_ref_name(name)}.md"

    # --- refs layer ---

    def add_ref(self, name: str, content: str) -> Path:
        """Write a raw tool output to ``refs/{name}.md``. Overwrites if present.

        Returns the path written. Refs are the ground-truth layer: they are never
        mutated by offload, only by an explicit re-add.
        """
        path = self._ref_path(name)
        path.write_text(content, encoding="utf-8")
        return path

    def read_ref(self, name: str) -> str:
        """Read a raw ref. Raises ShortTermError if it was never written."""
        path = self._ref_path(name)
        if not path.is_file():
            raise ShortTermError(f"ref not found: {name!r}")
        return path.read_text(encoding="utf-8")

    def refs(self) -> List[RefRecord]:
        """All refs currently on disk, ordered by name for deterministic output."""
        records: List[RefRecord] = []
        for path in sorted(self._refs_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            records.append(
                RefRecord(
                    name=path.stem,
                    content=content,
                    token_estimate=self._tokens(content),
                )
            )
        return records

    # --- steps layer ---

    def add_step(
        self,
        tool: str,
        summary: str,
        *,
        ref_name: Optional[str] = None,
        timestamp: str = "",
    ) -> StepRecord:
        """Append one summarized step. Returns the record (index = current count).

        ``ref_name`` optionally links the step to a raw ref for drill-down. The
        summary is caller-supplied (the future LLM summarizer seam will produce
        it); the service never invents content.
        """
        if ref_name is not None:
            safe_ref_name(ref_name)  # validate even if the file isn't written yet
        index = self.step_count()
        record = StepRecord(
            index=index,
            tool=tool,
            summary=summary,
            ref_name=ref_name,
            token_estimate=self._tokens(summary),
            timestamp=timestamp,
        )
        with self._steps_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        return record

    def steps(self) -> List[StepRecord]:
        """All steps, in insertion order (steps.jsonl is append-only)."""
        if not self._steps_file.is_file():
            return []
        records: List[StepRecord] = []
        with self._steps_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                records.append(StepRecord.from_json(json.loads(line)))
        return records

    def step_count(self) -> int:
        """Number of steps written (count of non-empty jsonl lines)."""
        if not self._steps_file.is_file():
            return 0
        count = 0
        with self._steps_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    # --- token accounting ---

    def refs_tokens(self) -> int:
        return sum(r.token_estimate for r in self.refs())

    def steps_tokens(self) -> int:
        return sum(s.token_estimate for s in self.steps())

    def raw_tokens(self) -> int:
        """Total raw session size (refs + steps) — the offload input."""
        return self.refs_tokens() + self.steps_tokens()

    # --- offload policy ---

    def offload_level(self) -> str:
        """Current offload level for the session, from the raw size vs budget."""
        return offload_level_for(self.raw_tokens(), self.config)

    def render_canvas(self) -> str:
        """Render the Mermaid canvas from the current steps, capped to the budget."""
        max_tokens = int(self.config.mmd_max_token_ratio * self.config.token_budget)
        return render_mermaid(self.steps(), max_tokens)

    def regenerate_canvas(self) -> str:
        """Render and persist canvas.mmd. Returns the rendered Mermaid."""
        mmd = self.render_canvas()
        self._canvas_file.write_text(mmd, encoding="utf-8")
        return mmd

    def canvas_text(self) -> str:
        """The persisted canvas, or empty string if none has been generated yet."""
        if not self._canvas_file.is_file():
            return ""
        return self._canvas_file.read_text(encoding="utf-8")

    def maybe_offload(self) -> str:
        """Evaluate the policy and, on aggressive, regenerate the canvas.

        Returns the resulting offload level. This is the hook to call after
        adding refs/steps. Mild needs no regeneration (injection simply drops the
        refs layer); aggressive regenerates the condensed canvas.
        """
        level = self.offload_level()
        if level == LEVEL_AGGRESSIVE:
            self.regenerate_canvas()
        return level

    # --- injection ---

    def build_injection(self) -> Dict[str, object]:
        """Return the layers to inject into the agent's context right now.

        Shape::

            {
              "offload_level": "none"|"mild"|"aggressive",
              "canvas": str,            # present when aggressive
              "steps": [StepRecord],    # present at none/mild
              "refs": [RefRecord],      # present only at none
            }

        Drill-down (`drill_down`) reaches the raw refs regardless of level — the
        levels only control what is *injected*, never what is *stored*.
        """
        level = self.offload_level()
        out: Dict[str, object] = {"offload_level": level}
        if level == LEVEL_AGGRESSIVE:
            out["canvas"] = self.canvas_text() or self.regenerate_canvas()
            return out
        out["steps"] = self.steps()
        if level == LEVEL_NONE:
            out["refs"] = self.refs()
        return out

    # --- drill-down ---

    def drill_down(self, node_id: str) -> Dict[str, object]:
        """Resolve a canvas node id to its source step and (if any) raw ref.

        ``node_id`` is ``s{index}`` (the Mermaid node label). Returns the step
        record plus the raw ref content it links to — the "top symbol → raw text"
        descent Tb specifies. Raises ShortTermError on a malformed id or a
        missing step.
        """
        if not isinstance(node_id, str) or not node_id.startswith("s") or not node_id[1:].isdigit():
            raise ShortTermError(
                f"bad node_id {node_id!r}: expected 's<index>' (e.g. 's3')"
            )
        index = int(node_id[1:])
        for step in self.steps():
            if step.index == index:
                result: Dict[str, object] = {"step": step}
                if step.ref_name is not None:
                    try:
                        result["ref_content"] = self.read_ref(step.ref_name)
                    except ShortTermError:
                        # Ref was never written — surface that honestly rather
                        # than silently dropping the key.
                        result["ref_content"] = None
                        result["ref_missing"] = step.ref_name
                return result
        raise ShortTermError(f"no step for node_id {node_id!r}")

    # --- session end ---

    def stable_steps(self) -> List[StepRecord]:
        """Steps eligible for promotion to L2 (scenario) at session end.

        The coupling point with the existing levels plan: a future ScenarioBuilder
        consumes these to mint an L2 scenario entity. Today this simply returns
        the persisted steps — the deterministic part — so the seam is explicit
        without depending on a not-yet-built distiller.
        """
        return self.steps()

    def clear(self) -> None:
        """Remove the session directory (cleanup at session end)."""
        if self.session_dir.is_dir():
            for path in self.session_dir.rglob("*"):
                if path.is_file():
                    path.unlink()
            # Remove now-empty dirs, deepest first.
            for path in sorted(self.session_dir.rglob("*"), reverse=True):
                if path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                self.session_dir.rmdir()
            except OSError:
                pass