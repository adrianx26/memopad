"""EntityAlias model — alternate names for entity resolution."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from memopad.models.base import Base


class EntityAlias(Base):
    """Alternate name for an entity.

    Aliases are explicit names from markdown frontmatter (`aliases:`) and are used
    as a fallback in LinkResolver when permalink, title, and path resolution fail.

    This is a conservative MemGraphRAG-inspired entity resolution aid: MemoPad
    stores explicit user-authored aliases, but does not invent aliases or perform
    fuzzy entity merging automatically.
    """

    __tablename__ = "entity_alias"
    __table_args__ = (
        Index("ix_entity_alias_entity", "entity_id"),
        Index("ix_entity_alias_project_alias", "project_id", "alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entity.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="frontmatter")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now().astimezone()
    )

    entity: Mapped[Optional["Entity"]] = relationship(
        "Entity", back_populates="aliases"
    )  # type: ignore[name-defined]
