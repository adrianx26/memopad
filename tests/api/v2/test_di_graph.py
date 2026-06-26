"""DI-graph regression guard for the v2-external factory set (deps/services.py).

Background: commit `c487b5e` ("Add missing imports and service dependencies in
entity_service and deps modules") shipped a broken DI graph that no test
caught, because a conftest SyntaxError was blocking *all* test collection at
the time. The v2 router tests now exercise the external graph transitively,
but this test resolves each top-level v2-external service factory *explicitly*
and in one request, so a wiring break (missing import, signature mismatch,
dropped dep) in any of them is caught regardless of which routes are tested.

Note: the non-external `_v2` (integer project_id) factories are dead code — no
router uses them (only `_v2_external` is mounted). They are the triplication
that Phase 5.4 consolidates, so they are intentionally not probed here.
"""

import pytest

from memopad.deps.services import (
    ContextServiceV2ExternalDep,
    DirectoryServiceV2ExternalDep,
    EntityServiceV2ExternalDep,
    FileServiceV2ExternalDep,
    SearchServiceV2ExternalDep,
    SyncServiceV2ExternalDep,
)


# One probe route that depends on every top-level v2-external service at once.
# Resolving this single request walks the entire external DI graph (config,
# engine/session_maker, project lookup by external_id, repositories, and every
# service constructor + its sub-dependencies).
_PROBE_PATH = "/v2/projects/{project_id}/_di_probe_all"


@pytest.mark.asyncio
async def test_v2_external_di_graph_resolves_all_services(app, client, test_project):
    """Every v2-external service factory must resolve without ImportError / missing-dep."""

    async def probe(
        entity: EntityServiceV2ExternalDep,
        search: SearchServiceV2ExternalDep,
        file: FileServiceV2ExternalDep,
        directory: DirectoryServiceV2ExternalDep,
        context: ContextServiceV2ExternalDep,
        sync: SyncServiceV2ExternalDep,
    ):
        return {
            "types": [
                type(entity).__name__,
                type(search).__name__,
                type(file).__name__,
                type(directory).__name__,
                type(context).__name__,
                type(sync).__name__,
            ]
        }

    app.add_api_route(_PROBE_PATH, probe, methods=["GET"])

    resp = await client.get(f"/v2/projects/{test_project.external_id}/_di_probe_all")
    assert resp.status_code == 200, resp.text

    types = resp.json()["types"]
    expected = [
        "EntityService",
        "SearchService",
        "FileService",
        "DirectoryService",
        "ContextService",
        "SyncService",
    ]
    assert types == expected, (
        f"DI graph resolved unexpected types: {types} — a factory is miswired. "
        "This is exactly the c487b5e-class regression this test guards against."
    )