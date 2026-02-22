"""Content type detection for assimilated pages."""

import re

# Pre-compiled patterns for content detection
AGENT_PROFILE_PATTERNS = re.compile(
    r"(AGENTS\.md|CLAUDE\.md|SYSTEM_PROMPT|system[_-]?prompt|agent[_-]?profile|"
    r"\.cursorrules|cursor[_-]?rules|copilot[_-]?instructions|\.github/copilot)",
    re.IGNORECASE,
)

SKILLS_PATTERNS = re.compile(
    r"(SKILL\.md|skills/|\.agent/|workflows/|\.gemini/|"
    r"rules[_-]?file|RULES\.md|\.clinerules|\.windsurfrules)",
    re.IGNORECASE,
)

CONFIG_PATTERNS = re.compile(
    r"(README\.md|readme\.md|pyproject\.toml|package\.json|Cargo\.toml|"
    r"setup\.py|setup\.cfg|Makefile|justfile|docker-compose|Dockerfile)",
    re.IGNORECASE,
)

SOUL_PATTERNS = re.compile(
    r"(soul\.md|SOUL\.md|identity\.md|persona|personality|core[_-]?values|"
    r"purpose\.md|mission|manifesto|beliefs|principles|ethos|"
    r"character\.md|worldview|philosophy\.md|creed)",
    re.IGNORECASE,
)

TOOLS_FUNCTIONS_PATTERNS = re.compile(
    r"(@tool|@mcp\.tool|def\s+tool_|function[_-]?calling|api[_-]?endpoint|"
    r"register[_-]?tool|tool[_-]?schema|openapi|swagger|handler|"
    r"@app\.(get|post|put|delete|patch)|@router\.|def\s+handle_|"
    r"tools/|functions/|endpoints/|api/)",
    re.IGNORECASE,
)

ALGORITHMS_PATTERNS = re.compile(
    r"(algorithm|sorting|binary[_-]?search|traversal|dynamic[_-]?programming|"
    r"recursion|backtracking|greedy|dijkstra|breadth[_-]?first|depth[_-]?first|"
    r"hashing|optimization|time[_-]?complexity|space[_-]?complexity|"
    r"O\(n|O\(log|divide[_-]?and[_-]?conquer|memoization)",
    re.IGNORECASE,
)

DECISION_PATTERNS = re.compile(
    r"(decision[_-]?tree|state[_-]?machine|fsm|finite[_-]?state|"
    r"workflow[_-]?engine|branching[_-]?logic|conditional[_-]?flow|"
    r"routing[_-]?logic|dispatch|strategy[_-]?pattern|policy[_-]?engine|"
    r"rule[_-]?engine|control[_-]?flow|switch[_-]?case|decision[_-]?table)",
    re.IGNORECASE,
)

# Optimized concept detection with compiled regex
CONCEPT_PATTERN = re.compile(
    r"architecture|design pattern|framework|plugin system|extension|middleware|"
    r"pipeline|workflow engine|knowledge graph|semantic|embedding|vector|"
    r"\brag\b|retrieval|agent|tool use|function calling",
    re.IGNORECASE,
)


def detect_content_type(url: str, text: str) -> list[str]:
    """Identify what kind of useful content a page contains."""
    types: list[str] = []
    combined = url + "\n" + text[:3000]

    if AGENT_PROFILE_PATTERNS.search(combined):
        types.append("agent_profile")
    if SKILLS_PATTERNS.search(combined):
        types.append("skills_rules")
    if CONFIG_PATTERNS.search(combined):
        types.append("config_docs")
    if SOUL_PATTERNS.search(combined):
        types.append("soul_file")
    if TOOLS_FUNCTIONS_PATTERNS.search(combined):
        types.append("tools_functions")
    if ALGORITHMS_PATTERNS.search(combined):
        types.append("algorithms")
    if DECISION_PATTERNS.search(combined):
        types.append("decision_structure")

    # Detect conceptual content using regex (more efficient than list iteration)
    text_lower = text[:5000].lower()
    matches = len(CONCEPT_PATTERN.findall(text_lower))
    if matches >= 2:
        types.append("concepts")

    return types
