"""Tests for the daily_note MCP tool."""

from datetime import date, timedelta

import pytest

from memopad.mcp.tools.daily_note import _build_default_template, _parse_date


class TestParseDate:
    def test_today_default_when_none(self):
        assert _parse_date(None) == date.today()

    def test_today_default_when_empty(self):
        assert _parse_date("") == date.today()

    def test_keyword_today(self):
        assert _parse_date("today") == date.today()
        assert _parse_date("TODAY") == date.today()

    def test_keyword_yesterday(self):
        assert _parse_date("yesterday") == date.today() - timedelta(days=1)

    def test_keyword_tomorrow(self):
        assert _parse_date("tomorrow") == date.today() + timedelta(days=1)

    def test_iso_date(self):
        assert _parse_date("2026-05-01") == date(2026, 5, 1)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_date("not-a-date")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_date("05/01/2026")


class TestDefaultTemplate:
    def test_includes_iso_date_in_prev_next_links(self):
        d = date(2026, 5, 1)
        body = _build_default_template(d)
        # Wikilinks are scoped under the daily/ namespace so an unrelated note
        # titled YYYY-MM-DD elsewhere in the vault doesn't resolve in its place.
        assert "[[daily/2026-04-30]]" in body
        assert "[[daily/2026-05-02]]" in body

    def test_includes_pretty_date(self):
        d = date(2026, 5, 1)
        body = _build_default_template(d)
        assert "Friday, May 01, 2026" in body

    def test_has_required_sections(self):
        body = _build_default_template(date(2026, 5, 1))
        assert "## Notes" in body
        assert "## Decisions" in body
        assert "## Tomorrow" in body

    def test_has_category_observation(self):
        body = _build_default_template(date(2026, 5, 1))
        assert "- [category] Daily journal entry for 2026-05-01" in body
