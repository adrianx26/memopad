"""Unified project resolution across MCP, API, and CLI.

This module provides a single canonical implementation of project resolution
logic, eliminating duplicated decision trees across the codebase.

The resolution follows a three-tier hierarchy:
1. Constrained mode: MEMOPAD_MCP_PROJECT env var (highest priority)
2. Explicit parameter: Project passed directly to operation
3. Default project: Used when default_project_mode=true (lowest priority)

In cloud mode, project is required unless discovery mode is explicitly allowed.
"""

import os
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from loguru import logger


class ResolutionMode(Enum):
    """How the project was resolved."""

    ENV_CONSTRAINT = auto()  # MEMOPAD_MCP_PROJECT env var
    EXPLICIT = auto()  # Explicit project parameter
    DEFAULT = auto()  # default_project with default_project_mode=true
    NONE = auto()  # No resolution possible


@dataclass(frozen=True)
class ResolvedProject:
    """Result of project resolution.

    Attributes:
        project: The resolved project name, or None if not resolved
        mode: How the project was resolved
        reason: Human-readable explanation of resolution
    """

    project: Optional[str]
    mode: ResolutionMode
    reason: str

    @property
    def is_resolved(self) -> bool:
        """Whether a project was successfully resolved."""
        return self.project is not None

    @property
    def is_discovery_mode(self) -> bool:
        """Whether we're in discovery mode (no specific project)."""
        return self.mode == ResolutionMode.NONE and self.project is None


@dataclass
class ProjectResolver:
    """Unified project resolution logic.

    Resolves the effective project given requested project, environment
    constraints, and configuration settings.

    This is the single canonical implementation of project resolution,
    used by MCP tools, API routes, and CLI commands.

    Args:
        default_project_mode: Whether to use default project when not specified
        default_project: The default project name
        constrained_project: Optional env-constrained project override
            (typically from memopad_MCP_PROJECT)
    """

    default_project_mode: bool = False
    default_project: Optional[str] = None
    constrained_project: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        default_project_mode: bool = False,
        default_project: Optional[str] = None,
    ) -> "ProjectResolver":
        """Create resolver with constrained_project from environment.

        Args:
            default_project_mode: Whether to use default project when not specified
            default_project: The default project name

        Returns:
            ProjectResolver configured with current environment
        """
        constrained = os.environ.get("MEMOPAD_MCP_PROJECT")
        return cls(
            default_project_mode=default_project_mode,
            default_project=default_project,
            constrained_project=constrained,
        )

    def resolve(
        self,
        project: Optional[str] = None,
    ) -> ResolvedProject:
        """Resolve project using the hierarchy.

        Resolution order:
        1. Constrained project from env var (highest priority in local mode)
        2. Explicit project parameter
        3. Default project if default_project_mode=true

        Args:
            project: Optional explicit project parameter

        Returns:
            ResolvedProject with project name, resolution mode, and reason
        """

        # Priority 1: CLI constraint overrides everything
        if self.constrained_project:
            logger.debug(f"Using CLI constrained project: {self.constrained_project}")
            return ResolvedProject(
                project=self.constrained_project,
                mode=ResolutionMode.ENV_CONSTRAINT,
                reason=f"Environment constraint: MEMOPAD_MCP_PROJECT={self.constrained_project}",
            )

        # Priority 2: Explicit project parameter
        if project:
            logger.debug(f"Using explicit project parameter: {project}")
            return ResolvedProject(
                project=project,
                mode=ResolutionMode.EXPLICIT,
                reason=f"Explicit parameter: {project}",
            )

        # Priority 3: Default project mode
        if self.default_project_mode and self.default_project:
            logger.debug(f"Using default project from config: {self.default_project}")
            return ResolvedProject(
                project=self.default_project,
                mode=ResolutionMode.DEFAULT,
                reason=f"Default project mode: {self.default_project}",
            )

        # No resolution possible
        logger.debug("No project resolution possible")
        return ResolvedProject(
            project=None,
            mode=ResolutionMode.NONE,
            reason="No project specified and default_project_mode is disabled",
        )

    def require_project(
        self,
        project: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> ResolvedProject:
        """Resolve project, raising an error if not resolved.

        Convenience method for operations that require a project.

        Args:
            project: Optional explicit project parameter
            error_message: Custom error message if project not resolved

        Returns:
            ResolvedProject (always with a non-None project)

        Raises:
            ValueError: If project could not be resolved
        """
        result = self.resolve(project, allow_discovery=False)
        if not result.is_resolved:
            msg = error_message or (
                "No project specified. Either set 'default_project_mode=true' in config, "
                "or provide a 'project' argument."
            )
            raise ValueError(msg)
        return result


__all__ = [
    "ProjectResolver",
    "ResolvedProject",
    "ResolutionMode",
]
