"""ObservationSchema model — canonical category registry per project.

Implements MemGraphRAG's Ontology Layer concept: a stable, per-project registry of
known [category] names.  LLMs writing observations use free-form category strings;
SchemaService normalises them against this table to suppress noise (rare/mis-spelled
categories) and maintain a consistent vocabulary.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memopad.models.base import Base


class ObservationSchema(Base):
    """Canonical category schema for a project's observation vocabulary.

    This table is MemoPad's lightweight Schema Layer, inspired by MemGraphRAG's
    Ontology Layer. It records canonical `[category]` names, aliases, and usage
    frequency for a project.

    Important MemoPad constraint: this registry normalizes indexed observations
    only. It never rewrites the markdown source file, preserving the file-as-source-of-truth model.
    """

    __tablename__ = "observation_schema"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_obs_schema_project_name"),
        Index("ix_obs_schema_project", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    # Canonical lower-cased name, e.g. "status"
    name: Mapped[str] = mapped_column(String, nullable=False)
    # JSON list of alternate spellings, e.g. ["Status", "STATE", "state"]
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    # How many observations have used this category (or its aliases)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Project relationship (for eager loading convenience)
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates=None)  # type: ignore[name-defined]
