"""Unit tests for SchemaService."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from memopad.models.observation_schema import ObservationSchema
from memopad.services.schema_service import SchemaService, ConsolidationSuggestion


def _make_schema(id_: int, name: str, frequency: int, aliases: list[str] | None = None) -> ObservationSchema:
    schema = MagicMock(spec=ObservationSchema)
    schema.id = id_
    schema.name = name
    schema.frequency = frequency
    schema.aliases = aliases or []
    return schema


def _make_service() -> tuple[SchemaService, MagicMock]:
    repo = MagicMock()
    return SchemaService(repo), repo


@pytest.mark.asyncio
async def test_normalize_existing_canonical():
    service, repo = _make_service()
    schema = _make_schema(1, "status", 5)
    repo.find_by_name_or_alias = AsyncMock(return_value=schema)
    repo.upsert_schema = AsyncMock()

    result = await service.normalize_category("status")

    assert result == "status"
    repo.find_by_name_or_alias.assert_called_once_with("status")
    repo.upsert_schema.assert_called_once_with("status")


@pytest.mark.asyncio
async def test_normalize_existing_alias_casing():
    service, repo = _make_service()
    schema = _make_schema(1, "status", 5, ["Status"])
    repo.find_by_name_or_alias = AsyncMock(return_value=schema)
    repo.upsert_schema = AsyncMock()
    repo.add_alias = AsyncMock()

    result = await service.normalize_category("STATUS")

    assert result == "status"
    repo.find_by_name_or_alias.assert_called_once_with("STATUS")
    repo.upsert_schema.assert_called_once_with("status")
    repo.add_alias.assert_called_once_with(1, "STATUS")


@pytest.mark.asyncio
async def test_normalize_new_category():
    service, repo = _make_service()
    repo.find_by_name_or_alias = AsyncMock(return_value=None)
    new_schema = _make_schema(2, "priority", 1)
    repo.upsert_schema = AsyncMock(return_value=new_schema)
    repo.add_alias = AsyncMock()

    result = await service.normalize_category("Priority")

    assert result == "priority"
    repo.upsert_schema.assert_called_once_with("priority")
    repo.add_alias.assert_called_once_with(2, "Priority")


@pytest.mark.asyncio
async def test_normalize_new_category_lowercase():
    service, repo = _make_service()
    repo.find_by_name_or_alias = AsyncMock(return_value=None)
    new_schema = _make_schema(2, "priority", 1)
    repo.upsert_schema = AsyncMock(return_value=new_schema)
    repo.add_alias = AsyncMock()

    result = await service.normalize_category("priority")

    assert result == "priority"
    repo.upsert_schema.assert_called_once_with("priority")
    repo.add_alias.assert_not_called()


@pytest.mark.asyncio
async def test_get_schemas():
    service, repo = _make_service()
    schemas = [_make_schema(1, "status", 5)]
    repo.find_by_project = AsyncMock(return_value=schemas)

    result = await service.get_schemas()
    assert result == schemas
    repo.find_by_project.assert_called_once()


@pytest.mark.asyncio
async def test_suggest_consolidation():
    service, repo = _make_service()
    
    # "status" is high freq, "sttus" is low freq, "unknown" is low freq
    schemas = [
        _make_schema(1, "status", 10),
        _make_schema(2, "sttus", 1),
        _make_schema(3, "priority", 5),
        _make_schema(4, "unknown", 1),
    ]
    repo.find_by_project = AsyncMock(return_value=schemas)

    suggestions = await service.suggest_consolidation()

    assert len(suggestions) == 2
    
    # sttus should match status
    sttus_sug = next(s for s in suggestions if s.name == "sttus")
    assert sttus_sug.possible_duplicate_of == "status"
    
    # unknown shouldn't have a good match
    unknown_sug = next(s for s in suggestions if s.name == "unknown")
    assert unknown_sug.possible_duplicate_of is None

