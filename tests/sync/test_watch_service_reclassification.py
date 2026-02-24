import pytest
from watchfiles.main import Change
from memopad.sync.watch_service import WatchService


@pytest.mark.asyncio
async def test_handle_changes_reclassifies_added_existing_files(
    app_config,
    project_repository,
    sync_service,
    test_project,
    project_config,
):
    """Verify that file add events for existing entities are correctly reclassified as modification events.

    This covers the logic in WatchService.handle_changes that handles atomic writes
    where an 'added' event is received for a file that already exists in the database.
    """

    # Use a sync service factory that returns our test sync_service fixture
    async def sync_service_factory(_project):
        return sync_service

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        quiet=True,
        sync_service_factory=sync_service_factory,
    )

    # 1. Create a file and sync it so it exists in the database
    test_file = project_config.home / "reclassification-test.md"
    test_file.write_text("# Test File\n", encoding="utf-8")

    # Perform initial sync to ensure the entity exists in the DB
    await sync_service.sync(project_config.home, project_name=test_project.name)

    # Verify it's in the DB
    entity = await sync_service.entity_repository.get_by_file_path("reclassification-test.md")
    assert entity is not None

    # 2. Simulate an 'added' event for this same file
    # This simulates a situation where an atomic write (delete then add)
    # results in an 'added' event for a file that is already known to the system.
    changes = {
        (Change.added, str(test_file)),
    }

    # 3. Handle the changes
    await watch_service.handle_changes(test_project, changes)

    # 4. Verify reclassification
    # The event should be processed as 'modified' (because the entity already exists), not 'new'.
    # We check the recent_events in the watch service state.
    events = watch_service.state.recent_events

    # Filter events for our specific file
    file_events = [e for e in events if e.path == "reclassification-test.md"]

    # We expect at least one 'modified' event from the handle_changes call
    # (The initial sync might have added its own events depending on implementation,
    # but we care about what handle_changes did).
    actions = [e.action for e in file_events]

    assert "modified" in actions, f"Expected 'modified' in actions, got {actions}"
    assert "new" not in [e.action for e in file_events if e.timestamp > watch_service.state.start_time], \
        "Should not have 'new' event for existing file during handle_changes"
