"""Tests for SyncReport failure propagation and response mapping."""

from datetime import datetime, timezone

from memopad.schemas.sync_report import SyncReportResponse
from memopad.sync.sync_service import SyncFailure, SyncReport


def test_sync_report_failed_round_trip():
    """A failing sync task must be recorded on SyncReport.failed (not silently dropped)."""
    report = SyncReport()
    report.new = {"notes/a.md"}
    report.failed.append(
        SyncFailure(
            path="notes/a.md",
            error_class="EntityCreationError",
            message="boom",
        )
    )

    assert len(report.failed) == 1
    assert report.failed[0].path == "notes/a.md"
    assert report.failed[0].error_class == "EntityCreationError"
    assert report.failed[0].message == "boom"


def test_from_sync_report_maps_failed():
    """from_sync_report must surface failed entries on the API response."""
    report = SyncReport()
    report.new = {"notes/b.md"}
    report.failed.append(
        SyncFailure(path="notes/b.md", error_class="ValueError", message="bad input")
    )

    resp = SyncReportResponse.from_sync_report(report)
    assert len(resp.failed) == 1
    assert resp.failed[0].path == "notes/b.md"
    assert resp.failed[0].error_class == "ValueError"
    assert resp.failed[0].message == "bad input"
    # And the existing fields still serialize
    assert resp.new == {"notes/b.md"}
    assert resp.total == 1


def test_empty_failed_by_default():
    """A clean sync leaves failed empty."""
    report = SyncReport()
    resp = SyncReportResponse.from_sync_report(report)
    assert resp.failed == []
    assert resp.skipped_files == []