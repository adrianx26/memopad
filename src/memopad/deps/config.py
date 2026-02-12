"""Configuration dependency injection for memopad.

This module provides configuration-related dependencies.
Note: Long-term goal is to minimize direct ConfigManager access
and inject config from composition roots instead.
"""

from typing import Annotated

from fastapi import Depends

from memopad.config import MemoPadConfig, ConfigManager


def get_app_config() -> MemoPadConfig:  # pragma: no cover
    """Get the application configuration.

    Note: This is a transitional dependency. The goal is for composition roots
    to read ConfigManager and inject config explicitly. During migration,
    this provides the same behavior as before.
    """
    app_config = ConfigManager().config
    return app_config


AppConfigDep = Annotated[MemoPadConfig, Depends(get_app_config)]
