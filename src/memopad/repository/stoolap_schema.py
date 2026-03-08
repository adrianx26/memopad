"""DDL schema for Stoolap backend.

All statements use CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
so this module is safe to apply at startup on every run — it is idempotent.

The schema mirrors the Alembic-managed SQLite/Postgres schema but is expressed
in plain SQL compatible with Stoolap's dialect (a superset of SQLite SQL).
"""

from typing import List

# ---------------------------------------------------------------------------
# DDL statements executed in order on Stoolap database startup
# ---------------------------------------------------------------------------
STOOLAP_DDL: List[str] = [
    # ------------------------------------------------------------------
    # project
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS project (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL UNIQUE,
        path        TEXT    NOT NULL,
        permalink   TEXT,
        is_active   INTEGER NOT NULL DEFAULT 1,
        is_default  INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_project_name ON project(name)",
    "CREATE INDEX IF NOT EXISTS ix_project_permalink ON project(permalink)",

    # ------------------------------------------------------------------
    # entity
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS entity (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id     TEXT    NOT NULL UNIQUE,
        title           TEXT    NOT NULL,
        entity_type     TEXT    NOT NULL DEFAULT 'note',
        entity_metadata TEXT,
        content_type    TEXT    NOT NULL DEFAULT 'text/markdown',
        project_id      INTEGER NOT NULL,
        permalink       TEXT,
        file_path       TEXT    NOT NULL,
        checksum        TEXT,
        mtime           REAL,
        size            INTEGER,
        created_at      TEXT    DEFAULT (datetime('now')),
        updated_at      TEXT    DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uix_entity_file_path_project ON entity(file_path, project_id)",
    "CREATE INDEX IF NOT EXISTS ix_entity_permalink ON entity(permalink)",
    "CREATE INDEX IF NOT EXISTS ix_entity_project_id ON entity(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_entity_external_id ON entity(external_id)",
    "CREATE INDEX IF NOT EXISTS ix_entity_entity_type ON entity(entity_type)",

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS observation (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id  INTEGER NOT NULL,
        entity_id   INTEGER NOT NULL,
        content     TEXT    NOT NULL,
        category    TEXT    NOT NULL DEFAULT 'note',
        context     TEXT,
        tags        TEXT,
        FOREIGN KEY (entity_id)  REFERENCES entity(id)  ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_observation_entity_id ON observation(entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_observation_project_id ON observation(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_observation_category ON observation(category)",

    # ------------------------------------------------------------------
    # relation
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS relation (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id    INTEGER NOT NULL,
        from_id       INTEGER NOT NULL,
        to_id         INTEGER,
        to_name       TEXT    NOT NULL,
        relation_type TEXT    NOT NULL,
        context       TEXT,
        FOREIGN KEY (from_id)    REFERENCES entity(id)  ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_relation_from_id ON relation(from_id)",
    "CREATE INDEX IF NOT EXISTS ix_relation_to_id ON relation(to_id)",
    "CREATE INDEX IF NOT EXISTS ix_relation_project_id ON relation(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_relation_type ON relation(relation_type)",

    # ------------------------------------------------------------------
    # search_index  (replaces FTS5 virtual table used in SQLite backend)
    # Full-text matching is done via LIKE in the search repository.
    # In a future phase HNSW indexes can be added here for vector search.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS search_index (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id        INTEGER,
        project_id       INTEGER NOT NULL,
        title            TEXT,
        permalink        TEXT,
        file_path        TEXT,
        type             TEXT,
        content_stems    TEXT,
        content_snippet  TEXT,
        metadata         TEXT,
        category         TEXT,
        from_id          INTEGER,
        to_id            INTEGER,
        relation_type    TEXT,
        created_at       TEXT,
        updated_at       TEXT,
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_search_project ON search_index(project_id)",
    "CREATE INDEX IF NOT EXISTS ix_search_type ON search_index(type)",
    "CREATE INDEX IF NOT EXISTS ix_search_entity_id ON search_index(entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_search_permalink ON search_index(permalink)",
]
