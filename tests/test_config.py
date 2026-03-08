"""Test configuration management."""

import tempfile
import pytest
from datetime import datetime

from memopad.config import MemoPadConfig, ConfigManager
from pathlib import Path


class TestMemoPadConfig:
    """Test MemoPadConfig behavior with MEMOPAD_HOME environment variable."""

    def test_default_behavior_without_MEMOPAD_home(self, config_home, monkeypatch):
        """Test that config uses default path when MEMOPAD_HOME is not set."""
        # Ensure MEMOPAD_HOME is not set
        monkeypatch.delenv("MEMOPAD_HOME", raising=False)

        config = MemoPadConfig()

        # Should use the default path (home/memopad)
        expected_path = config_home / "memopad"
        assert Path(config.projects["main"]) == expected_path

    def test_respects_MEMOPAD_home_environment_variable(self, config_home, monkeypatch):
        """Test that config respects MEMOPAD_HOME environment variable."""
        custom_path = config_home / "app" / "data"
        monkeypatch.setenv("MEMOPAD_HOME", str(custom_path))

        config = MemoPadConfig()

        # Should use the custom path from environment variable
        assert Path(config.projects["main"]) == custom_path

    def test_model_post_init_respects_MEMOPAD_home_creates_main(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init creates main project with MEMOPAD_HOME when missing and no other projects."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("MEMOPAD_HOME", str(custom_path))

        # Create config without main project
        config = MemoPadConfig()

        # model_post_init should have added main project with MEMOPAD_HOME
        assert "main" in config.projects
        assert Path(config.projects["main"]) == custom_path

    def test_model_post_init_respects_MEMOPAD_home_sets_non_main_default(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init does not create main project with MEMOPAD_HOME when another project exists."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("MEMOPAD_HOME", str(custom_path))

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = MemoPadConfig(projects={"other": str(other_path)})

        # model_post_init should not add main project with MEMOPAD_HOME
        assert "main" not in config.projects
        assert Path(config.projects["other"]) == other_path

    def test_model_post_init_fallback_without_MEMOPAD_home(self, config_home, monkeypatch):
        """Test that model_post_init can set a non-main default when MEMOPAD_HOME is not set."""
        # Ensure MEMOPAD_HOME is not set
        monkeypatch.delenv("MEMOPAD_HOME", raising=False)

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = MemoPadConfig(projects={"other": str(other_path)})

        # model_post_init should not add main project, but "other" should now be the default
        assert "main" not in config.projects
        assert Path(config.projects["other"]) == other_path

    def test_MEMOPAD_home_with_relative_path(self, config_home, monkeypatch):
        """Test that MEMOPAD_HOME works with relative paths."""
        relative_path = "relative/memory/path"
        monkeypatch.setenv("MEMOPAD_HOME", relative_path)

        config = MemoPadConfig()

        # Should normalize to platform-native path format
        assert Path(config.projects["main"]) == Path(relative_path)

    def test_MEMOPAD_home_overrides_existing_main_project(self, config_home, monkeypatch):
        """Test that MEMOPAD_HOME is not used when a map is passed in the constructor."""
        custom_path = str(config_home / "override" / "memory" / "path")
        monkeypatch.setenv("MEMOPAD_HOME", custom_path)

        # Try to create config with a different main project path
        original_path = str(config_home / "original" / "path")
        config = MemoPadConfig(projects={"main": original_path})

        # The default_factory should override with MEMOPAD_HOME value
        # Note: This tests the current behavior where default_factory takes precedence
        assert config.projects["main"] == original_path


class TestConfigManager:
    """Test ConfigManager functionality."""

    @pytest.fixture
    def temp_config_manager(self):
        """Create a ConfigManager with temporary config file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create a test ConfigManager instance
            config_manager = ConfigManager()
            # Override config paths to use temp directory
            config_manager.config_dir = temp_path / "memopad"
            config_manager.config_file = config_manager.config_dir / "config.yaml"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create initial config with test projects
            test_config = MemoPadConfig(
                default_project="main",
                projects={
                    "main": str(temp_path / "main"),
                    "test-project": str(temp_path / "test"),
                    "special-chars": str(
                        temp_path / "special"
                    ),  # This will be the config key for "Special/Chars"
                },
            )
            config_manager.save_config(test_config)

            yield config_manager

    def test_set_default_project_with_exact_name_match(self, temp_config_manager):
        """Test set_default_project when project name matches config key exactly."""
        config_manager = temp_config_manager

        # Set default to a project that exists with exact name match
        config_manager.set_default_project("test-project")

        # Verify the config was updated
        config = config_manager.load_config()
        assert config.default_project == "test-project"

    def test_set_default_project_with_permalink_lookup(self, temp_config_manager):
        """Test set_default_project when input needs permalink normalization."""
        config_manager = temp_config_manager

        # Simulate a project that was created with special characters
        # The config key would be the permalink, but user might type the original name

        # First add a project with original name that gets normalized
        config = config_manager.load_config()
        config.projects["special-chars-project"] = str(Path("/tmp/special"))
        config_manager.save_config(config)

        # Now test setting default using a name that will normalize to the config key
        config_manager.set_default_project(
            "Special Chars Project"
        )  # This should normalize to "special-chars-project"

        # Verify the config was updated with the correct config key
        updated_config = config_manager.load_config()
        assert updated_config.default_project == "special-chars-project"

    def test_set_default_project_uses_canonical_name(self, temp_config_manager):
        """Test that set_default_project uses the canonical config key, not user input."""
        config_manager = temp_config_manager

        # Add a project with a config key that differs from user input
        config = config_manager.load_config()
        config.projects["my-test-project"] = str(Path("/tmp/mytest"))
        config_manager.save_config(config)

        # Set default using input that will match but is different from config key
        config_manager.set_default_project("My Test Project")  # Should find "my-test-project"

        # Verify that the canonical config key is used, not the user input
        updated_config = config_manager.load_config()
        assert updated_config.default_project == "my-test-project"
        # Should NOT be the user input
        assert updated_config.default_project != "My Test Project"

    def test_set_default_project_nonexistent_project(self, temp_config_manager):
        """Test set_default_project raises ValueError for nonexistent project."""
        config_manager = temp_config_manager

        with pytest.raises(ValueError, match="Project 'nonexistent' not found"):
            config_manager.set_default_project("nonexistent")

    def test_disable_permalinks_flag_default(self):
        """Test that disable_permalinks flag defaults to False."""
        config = MemoPadConfig()
        assert config.disable_permalinks is False

    def test_disable_permalinks_flag_can_be_enabled(self):
        """Test that disable_permalinks flag can be set to True."""
        config = MemoPadConfig(disable_permalinks=True)
        assert config.disable_permalinks is True

    def test_config_manager_respects_custom_config_dir(self, monkeypatch):
        """Test that ConfigManager respects MEMOPAD_CONFIG_DIR environment variable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_config_dir = Path(temp_dir) / "custom" / "config"
            monkeypatch.setenv("MEMOPAD_CONFIG_DIR", str(custom_config_dir))

            config_manager = ConfigManager()

            # Verify config_dir is set to the custom path
            assert config_manager.config_dir == custom_config_dir
            # Verify config_file is in the custom directory
            assert config_manager.config_file == custom_config_dir / "config.json"
            # Verify the directory was created
            assert config_manager.config_dir.exists()

    def test_config_manager_default_without_custom_config_dir(self, config_home, monkeypatch):
        """Test that ConfigManager uses default location when MEMOPAD_CONFIG_DIR is not set."""
        monkeypatch.delenv("MEMOPAD_CONFIG_DIR", raising=False)

        config_manager = ConfigManager()

        # Should use default location (new visible directory, not hidden .memopad)
        assert config_manager.config_dir == config_home / "memopad"
        assert config_manager.config_file == config_home / "memopad" / "config.json"

    def test_remove_project_with_exact_name_match(self, temp_config_manager):
        """Test remove_project when project name matches config key exactly."""
        config_manager = temp_config_manager

        # Verify project exists
        config = config_manager.load_config()
        assert "test-project" in config.projects

        # Remove the project with exact name match
        config_manager.remove_project("test-project")

        # Verify the project was removed
        config = config_manager.load_config()
        assert "test-project" not in config.projects

    def test_remove_project_with_permalink_lookup(self, temp_config_manager):
        """Test remove_project when input needs permalink normalization."""
        config_manager = temp_config_manager

        # Add a project with normalized key
        config = config_manager.load_config()
        config.projects["special-chars-project"] = str(Path("/tmp/special"))
        config_manager.save_config(config)

        # Remove using a name that will normalize to the config key
        config_manager.remove_project(
            "Special Chars Project"
        )  # This should normalize to "special-chars-project"

        # Verify the project was removed using the correct config key
        updated_config = config_manager.load_config()
        assert "special-chars-project" not in updated_config.projects

    def test_remove_project_uses_canonical_name(self, temp_config_manager):
        """Test that remove_project uses the canonical config key, not user input."""
        config_manager = temp_config_manager

        # Add a project with a config key that differs from user input
        config = config_manager.load_config()
        config.projects["my-test-project"] = str(Path("/tmp/mytest"))
        config_manager.save_config(config)

        # Remove using input that will match but is different from config key
        config_manager.remove_project("My Test Project")  # Should find "my-test-project"

        # Verify that the canonical config key was removed
        updated_config = config_manager.load_config()
        assert "my-test-project" not in updated_config.projects

    def test_remove_project_nonexistent_project(self, temp_config_manager):
        """Test remove_project raises ValueError for nonexistent project."""
        config_manager = temp_config_manager

        with pytest.raises(ValueError, match="Project 'nonexistent' not found"):
            config_manager.remove_project("nonexistent")

    def test_remove_project_cannot_remove_default(self, temp_config_manager):
        """Test remove_project raises ValueError when trying to remove default project."""
        config_manager = temp_config_manager

        # Try to remove the default project
        with pytest.raises(ValueError, match="Cannot remove the default project"):
            config_manager.remove_project("main")




class TestPlatformNativePathSeparators:
    """Test that config uses platform-native path separators."""

    def test_project_paths_use_platform_native_separators_in_config(self, monkeypatch):
        """Test that project paths use platform-native separators when created."""
        import platform

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Set up ConfigManager with temp directory
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "memopad"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create a project path
            project_path = temp_path / "my" / "project"
            project_path.mkdir(parents=True, exist_ok=True)

            # Add project via ConfigManager
            config = MemoPadConfig(projects={})
            config.projects["test-project"] = str(project_path)
            config_manager.save_config(config)

            # Read the raw JSON file
            import json

            config_data = json.loads(config_manager.config_file.read_text())

            # Verify path uses platform-native separators
            saved_path = config_data["projects"]["test-project"]

            # On Windows, should have backslashes; on Unix, forward slashes
            if platform.system() == "Windows":
                # Windows paths should contain backslashes
                assert "\\" in saved_path or ":" in saved_path  # C:\\ or \\UNC
                assert "/" not in saved_path.replace(":/", "")  # Exclude drive letter
            else:
                # Unix paths should use forward slashes
                assert "/" in saved_path
                # Should not force POSIX on non-Windows
                assert saved_path == str(project_path)

    def test_add_project_uses_platform_native_separators(self, monkeypatch):
        """Test that ConfigManager.add_project() uses platform-native separators."""
        import platform

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Set up ConfigManager
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "memopad"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Initialize with empty projects
            initial_config = MemoPadConfig(projects={})
            config_manager.save_config(initial_config)

            # Add project
            project_path = temp_path / "new" / "project"
            config_manager.add_project("new-project", str(project_path))

            # Load and verify
            config = config_manager.load_config()
            saved_path = config.projects["new-project"]

            # Verify platform-native separators
            if platform.system() == "Windows":
                assert "\\" in saved_path or ":" in saved_path
            else:
                assert "/" in saved_path
                assert saved_path == str(project_path)

    def test_model_post_init_uses_platform_native_separators(self, config_home, monkeypatch):
        """Test that model_post_init uses platform-native separators."""
        import platform

        monkeypatch.delenv("MEMOPAD_HOME", raising=False)

        # Create config without projects (triggers model_post_init to add main)
        config = MemoPadConfig(projects={})

        # Verify main project path uses platform-native separators
        main_path = config.projects["main"]

        if platform.system() == "Windows":
            # Windows: should have backslashes or drive letter
            assert "\\" in main_path or ":" in main_path
        else:
            # Unix: should have forward slashes
            assert "/" in main_path


class TestFormattingConfig:
    """Test file formatting configuration options."""

    def test_format_on_save_defaults_to_false(self):
        """Test that format_on_save is disabled by default."""
        config = MemoPadConfig()
        assert config.format_on_save is False

    def test_format_on_save_can_be_enabled(self):
        """Test that format_on_save can be set to True."""
        config = MemoPadConfig(format_on_save=True)
        assert config.format_on_save is True

    def test_formatter_command_defaults_to_none(self):
        """Test that formatter_command defaults to None (uses built-in mdformat)."""
        config = MemoPadConfig()
        assert config.formatter_command is None

    def test_formatter_command_can_be_set(self):
        """Test that formatter_command can be configured."""
        config = MemoPadConfig(formatter_command="prettier --write {file}")
        assert config.formatter_command == "prettier --write {file}"

    def test_formatters_defaults_to_empty_dict(self):
        """Test that formatters defaults to empty dict."""
        config = MemoPadConfig()
        assert config.formatters == {}

    def test_formatters_can_be_configured(self):
        """Test that per-extension formatters can be configured."""
        config = MemoPadConfig(
            formatters={
                "md": "prettier --write {file}",
                "json": "jq . {file} > {file}.tmp && mv {file}.tmp {file}",
            }
        )
        assert config.formatters["md"] == "prettier --write {file}"
        assert "json" in config.formatters

    def test_formatter_timeout_defaults_to_5_seconds(self):
        """Test that formatter_timeout defaults to 5.0 seconds."""
        config = MemoPadConfig()
        assert config.formatter_timeout == 5.0

    def test_formatter_timeout_can_be_customized(self):
        """Test that formatter_timeout can be set to a different value."""
        config = MemoPadConfig(formatter_timeout=10.0)
        assert config.formatter_timeout == 10.0

    def test_formatter_timeout_must_be_positive(self):
        """Test that formatter_timeout validation rejects non-positive values."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            MemoPadConfig(formatter_timeout=0)

        with pytest.raises(pydantic.ValidationError):
            MemoPadConfig(formatter_timeout=-1)

    def test_formatting_env_vars(self, monkeypatch):
        """Test that formatting config can be set via environment variables."""
        monkeypatch.setenv("MEMOPAD_FORMAT_ON_SAVE", "true")
        monkeypatch.setenv("MEMOPAD_FORMATTER_COMMAND", "prettier --write {file}")
        monkeypatch.setenv("MEMOPAD_FORMATTER_TIMEOUT", "10.0")

        config = MemoPadConfig()

        assert config.format_on_save is True
        assert config.formatter_command == "prettier --write {file}"
        assert config.formatter_timeout == 10.0

    def test_formatters_env_var_json(self, monkeypatch):
        """Test that formatters dict can be set via JSON environment variable."""
        import json

        formatters_json = json.dumps({"md": "prettier --write {file}", "json": "jq . {file}"})
        monkeypatch.setenv("MEMOPAD_FORMATTERS", formatters_json)

        config = MemoPadConfig()

        assert config.formatters == {"md": "prettier --write {file}", "json": "jq . {file}"}

    def test_save_and_load_formatting_config(self):
        """Test that formatting config survives save/load cycle."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "memopad"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create config with formatting settings
            test_config = MemoPadConfig(
                projects={"main": str(temp_path / "main")},
                format_on_save=True,
                formatter_command="prettier --write {file}",
                formatters={"md": "prettier --write {file}", "json": "prettier --write {file}"},
                formatter_timeout=10.0,
            )
            config_manager.save_config(test_config)

            # Load and verify
            loaded_config = config_manager.load_config()
            assert loaded_config.format_on_save is True
            assert loaded_config.formatter_command == "prettier --write {file}"
            assert loaded_config.formatters == {
                "md": "prettier --write {file}",
                "json": "prettier --write {file}",
            }
            assert loaded_config.formatter_timeout == 10.0
