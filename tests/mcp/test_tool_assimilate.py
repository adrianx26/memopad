"""Tests for the assimilate MCP tool."""

import pytest

from memopad.mcp.tools.assimilate import (
    LinkExtractor,
    HTMLToText,
    extract_links,
    html_to_text,
    categorize_links,
    detect_content_type,
    _build_overview_note,
    _build_github_links_note,
    _build_agent_profiles_note,
    _build_skills_rules_note,
    _build_concepts_note,
    _build_soul_files_note,
    _build_tools_functions_note,
    _build_algorithms_note,
    _build_decision_structure_note,
    _build_functional_diagram_note,
)


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


class TestLinkExtractor:
    def test_extracts_href_links(self):
        html = '<a href="https://example.com">Link</a> <a href="/about">About</a>'
        parser = LinkExtractor()
        parser.feed(html)
        assert parser.links == ["https://example.com", "/about"]

    def test_skips_empty_and_missing_href(self):
        html = '<a>No href</a> <a href="">Empty</a> <a href="ok">OK</a>'
        parser = LinkExtractor()
        parser.feed(html)
        # Empty string is falsy so it should be skipped
        assert parser.links == ["ok"]

    def test_handles_malformed_html(self):
        html = '<a href="link1"><div><a href="link2">'
        parser = LinkExtractor()
        parser.feed(html)
        assert "link1" in parser.links
        assert "link2" in parser.links


class TestHTMLToText:
    def test_strips_script_and_style(self):
        html = "<p>Hello</p><script>alert('x')</script><style>.x{}</style><p>World</p>"
        text = html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "alert" not in text
        assert ".x{}" not in text

    def test_converts_headings(self):
        html = "<h1>Title</h1><h2>Sub</h2><p>Text</p>"
        text = html_to_text(html)
        assert "# Title" in text
        assert "## Sub" in text
        assert "Text" in text

    def test_handles_empty_html(self):
        assert html_to_text("") == ""

    def test_collapses_newlines(self):
        html = "<p>A</p><p></p><p></p><p></p><p>B</p>"
        text = html_to_text(html)
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in text


class TestExtractLinks:
    def test_resolves_relative_links(self):
        html = '<a href="/page">Page</a>'
        links = extract_links(html, "https://example.com/")
        assert "https://example.com/page" in links

    def test_skips_anchors_and_javascript(self):
        html = '<a href="#section">Anchor</a><a href="javascript:void(0)">JS</a><a href="mailto:x@y.com">Email</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 0

    def test_strips_fragments(self):
        html = '<a href="/page#section">Link</a>'
        links = extract_links(html, "https://example.com/")
        assert links == ["https://example.com/page"]


class TestCategorizeLinks:
    def test_categorizes_correctly(self):
        links = [
            "https://example.com/page1",
            "https://example.com/page2",
            "https://github.com/org/repo",
            "https://raw.githubusercontent.com/org/repo/main/README.md",
            "https://other-site.com/page",
        ]
        result = categorize_links(links, "example.com")
        assert "https://example.com/page1" in result["internal"]
        assert "https://example.com/page2" in result["internal"]
        assert "https://github.com/org/repo" in result["github"]
        assert "https://raw.githubusercontent.com/org/repo/main/README.md" in result["github"]
        assert "https://other-site.com/page" in result["external"]

    def test_deduplicates(self):
        links = ["https://example.com/a", "https://example.com/a", "https://example.com/a"]
        result = categorize_links(links, "example.com")
        assert len(result["internal"]) == 1


# ---------------------------------------------------------------------------
# Content detection
# ---------------------------------------------------------------------------


class TestDetectContentType:
    def test_detects_agent_profile(self):
        types = detect_content_type("https://example.com/AGENTS.md", "Some content about agents")
        assert "agent_profile" in types

    def test_detects_skills(self):
        types = detect_content_type("https://example.com/skills/SKILL.md", "skill definition")
        assert "skills_rules" in types

    def test_detects_config(self):
        types = detect_content_type("https://example.com/README.md", "Project readme")
        assert "config_docs" in types

    def test_detects_concepts(self):
        text = "This project uses a plugin system with middleware pipeline for architecture design patterns"
        types = detect_content_type("https://example.com/docs", text)
        assert "concepts" in types

    def test_detects_soul_file(self):
        types = detect_content_type("https://example.com/soul.md", "Agent identity and personality")
        assert "soul_file" in types

    def test_detects_soul_file_by_content(self):
        types = detect_content_type("https://example.com/page", "This defines core_values and principles for the agent")
        assert "soul_file" in types

    def test_detects_tools_functions(self):
        types = detect_content_type("https://example.com/tools/search.py", "@mcp.tool decorator used here")
        assert "tools_functions" in types

    def test_detects_tools_functions_by_content(self):
        types = detect_content_type("https://example.com/page", "register_tool and api_endpoint definitions")
        assert "tools_functions" in types

    def test_detects_algorithms(self):
        types = detect_content_type("https://example.com/algo.py", "binary_search algorithm with O(log n) complexity")
        assert "algorithms" in types

    def test_detects_algorithms_by_content(self):
        types = detect_content_type("https://example.com/page", "dynamic_programming optimization with memoization")
        assert "algorithms" in types

    def test_detects_decision_structure(self):
        types = detect_content_type("https://example.com/fsm.py", "state_machine implementation with finite_state transitions")
        assert "decision_structure" in types

    def test_detects_decision_structure_by_content(self):
        types = detect_content_type("https://example.com/page", "rule_engine for routing_logic and decision_table handling")
        assert "decision_structure" in types

    def test_no_false_positives(self):
        types = detect_content_type("https://example.com/random", "Just a normal page with nothing special")
        assert types == []


# ---------------------------------------------------------------------------
# Note builders
# ---------------------------------------------------------------------------


class TestNoteBuilders:
    def _make_data(self, content_types=None):
        return {
            "pages": [
                {
                    "url": "https://example.com",
                    "text": "Main page content with details",
                    "content_types": content_types or [],
                    "links": {"internal": [], "github": [], "external": []},
                }
            ],
            "all_github_links": ["https://github.com/org/repo1", "https://github.com/org/repo2"],
            "all_external_links": ["https://other.com"],
            "errors": [],
        }

    def test_overview_note(self):
        data = self._make_data()
        note = _build_overview_note("https://example.com", data)
        assert "Assimilated: https://example.com" in note
        assert "Pages processed: 1" in note
        assert "GitHub links found: 2" in note

    def test_github_links_note(self):
        data = self._make_data()
        note = _build_github_links_note(data)
        assert note is not None
        assert "github.com/org/repo1" in note
        assert "github.com/org/repo2" in note

    def test_github_links_note_empty(self):
        data = self._make_data()
        data["all_github_links"] = []
        note = _build_github_links_note(data)
        assert note is None

    def test_agent_profiles_note(self):
        data = self._make_data(content_types=["agent_profile"])
        note = _build_agent_profiles_note(data)
        assert note is not None
        assert "Agent Profiles" in note

    def test_agent_profiles_note_empty(self):
        data = self._make_data(content_types=[])
        note = _build_agent_profiles_note(data)
        assert note is None

    def test_skills_rules_note(self):
        data = self._make_data(content_types=["skills_rules"])
        note = _build_skills_rules_note(data)
        assert note is not None
        assert "Skills, Rules" in note

    def test_concepts_note(self):
        data = self._make_data(content_types=["concepts"])
        note = _build_concepts_note(data)
        assert note is not None
        assert "Concepts" in note

    # --- New note builder tests ---

    def test_soul_files_note(self):
        data = self._make_data(content_types=["soul_file"])
        note = _build_soul_files_note(data)
        assert note is not None
        assert "Soul Files" in note
        assert "identity" in note.lower() or "soul" in note.lower()

    def test_soul_files_note_empty(self):
        data = self._make_data(content_types=[])
        note = _build_soul_files_note(data)
        assert note is None

    def test_tools_functions_note(self):
        data = self._make_data(content_types=["tools_functions"])
        note = _build_tools_functions_note(data)
        assert note is not None
        assert "Tools" in note
        assert "Functions" in note

    def test_tools_functions_note_empty(self):
        data = self._make_data(content_types=[])
        note = _build_tools_functions_note(data)
        assert note is None

    def test_algorithms_note(self):
        data = self._make_data(content_types=["algorithms"])
        note = _build_algorithms_note(data)
        assert note is not None
        assert "Algorithms" in note

    def test_algorithms_note_empty(self):
        data = self._make_data(content_types=[])
        note = _build_algorithms_note(data)
        assert note is None

    def test_decision_structure_note(self):
        data = self._make_data(content_types=["decision_structure"])
        note = _build_decision_structure_note(data)
        assert note is not None
        assert "Decision Structures" in note

    def test_decision_structure_note_empty(self):
        data = self._make_data(content_types=[])
        note = _build_decision_structure_note(data)
        assert note is None

    def test_functional_diagram_note(self):
        data = {
            "pages": [
                {
                    "url": "https://example.com/README.md",
                    "text": "Main readme content",
                    "content_types": ["config_docs"],
                    "links": {"internal": [], "github": [], "external": []},
                },
                {
                    "url": "https://example.com/src/main.py",
                    "text": "Main entry point",
                    "content_types": ["tools_functions"],
                    "links": {"internal": [], "github": [], "external": []},
                },
                {
                    "url": "https://example.com/src/utils.py",
                    "text": "Utility functions",
                    "content_types": ["algorithms"],
                    "links": {"internal": [], "github": [], "external": []},
                },
            ],
            "all_github_links": [],
            "all_external_links": [],
            "errors": [],
        }
        note = _build_functional_diagram_note(data)
        assert note is not None
        assert "Functional Diagram" in note
        assert "```mermaid" in note
        assert "graph TD" in note
        assert "README.md" in note
        assert "main.py" in note
        assert "utils.py" in note

    def test_functional_diagram_note_empty(self):
        data = {"pages": [], "all_github_links": [], "all_external_links": [], "errors": []}
        note = _build_functional_diagram_note(data)
        assert note is None


# ---------------------------------------------------------------------------
# Integration: assimilate tool (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assimilate_invalid_url(app, test_project):
    """Test that assimilate rejects invalid URLs."""
    from memopad.mcp.tools.assimilate import assimilate

    result = await assimilate.fn(url="not-a-url", project=test_project.name)
    assert "Error" in result
    assert "Invalid URL" in result


@pytest.mark.asyncio
async def test_assimilate_stores_notes(app, test_project, monkeypatch):
    """Test that assimilate crawls and stores notes correctly."""
    import sys
    from memopad.mcp.tools.assimilate import assimilate

    # Get the actual module object (not shadowed by FunctionTool)
    assimilate_mod = sys.modules["memopad.mcp.tools.assimilate"]

    # Mock the crawl function to avoid real HTTP
    mock_data = {
        "pages": [
            {
                "url": "https://test-site.example.com",
                "text": "# Test Repo\n\nA great project with plugin system and architecture design patterns",
                "content_types": ["config_docs", "concepts"],
                "links": {"internal": [], "github": ["https://github.com/org/test"], "external": []},
            },
        ],
        "all_github_links": ["https://github.com/org/test"],
        "all_external_links": [],
        "errors": [],
    }

    async def mock_crawl(url, max_depth=10, max_pages=0):
        return mock_data

    monkeypatch.setattr(assimilate_mod, "crawl", mock_crawl)

    result = await assimilate.fn(url="https://test-site.example.com", project=test_project.name)

    assert "Assimilation Complete" in result
    assert f"project: {test_project.name}" in result
    assert "items_processed: 1" in result
    assert "notes_stored:" in result
    assert "Overview" in result
    assert f"[Session: Using project '{test_project.name}']" in result
