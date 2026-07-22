"""Tests for windows_mcp.paths — Claude Desktop data directory resolution."""

from unittest.mock import patch

from windows_mcp.paths import (
    get_claude_config_path,
    get_claude_data_dir,
    is_msix_install,
)


class TestGetClaudeDataDir:
    def test_returns_msix_dir_when_present(self, tmp_path):
        """MSIX path takes priority over the standard path."""
        local_appdata = tmp_path / "LocalAppData"
        msix_claude = (
            local_appdata
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        msix_claude.mkdir(parents=True)

        # Also create the standard path to verify MSIX wins
        appdata = tmp_path / "AppData" / "Roaming"
        standard_claude = appdata / "Claude"
        standard_claude.mkdir(parents=True)

        env = {"LOCALAPPDATA": str(local_appdata), "APPDATA": str(appdata)}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_data_dir()

        assert result == msix_claude

    def test_returns_standard_dir_when_no_msix(self, tmp_path):
        """Falls back to %APPDATA%\\Claude when no MSIX package is found."""
        local_appdata = tmp_path / "LocalAppData"
        local_appdata.mkdir()

        appdata = tmp_path / "AppData" / "Roaming"
        standard_claude = appdata / "Claude"
        standard_claude.mkdir(parents=True)

        env = {"LOCALAPPDATA": str(local_appdata), "APPDATA": str(appdata)}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_data_dir()

        assert result == standard_claude

    def test_returns_none_when_neither_exists(self, tmp_path):
        """Returns None when Claude is not installed."""
        local_appdata = tmp_path / "LocalAppData"
        local_appdata.mkdir()
        appdata = tmp_path / "AppData" / "Roaming"
        appdata.mkdir(parents=True)

        env = {"LOCALAPPDATA": str(local_appdata), "APPDATA": str(appdata)}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_data_dir()

        assert result is None

    def test_handles_missing_env_vars(self):
        """Returns None gracefully when env vars are unset."""
        env = {"LOCALAPPDATA": "", "APPDATA": ""}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_data_dir()

        assert result is None

    def test_matches_any_publisher_id_suffix(self, tmp_path):
        """The glob matches Claude packages with any publisher-id hash."""
        local_appdata = tmp_path / "LocalAppData"
        msix_claude = (
            local_appdata / "Packages" / "Claude_abc123xyz" / "LocalCache" / "Roaming" / "Claude"
        )
        msix_claude.mkdir(parents=True)

        env = {"LOCALAPPDATA": str(local_appdata), "APPDATA": ""}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_data_dir()

        assert result == msix_claude


class TestGetClaudeConfigPath:
    def test_returns_config_path_when_file_exists(self, tmp_path):
        local_appdata = tmp_path / "LocalAppData"
        msix_claude = (
            local_appdata
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        msix_claude.mkdir(parents=True)
        config_file = msix_claude / "claude_desktop_config.json"
        config_file.write_text("{}")

        env = {"LOCALAPPDATA": str(local_appdata), "APPDATA": ""}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_config_path()

        assert result == config_file

    def test_returns_none_when_config_missing(self, tmp_path):
        local_appdata = tmp_path / "LocalAppData"
        msix_claude = (
            local_appdata
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        msix_claude.mkdir(parents=True)
        # No config file created

        env = {"LOCALAPPDATA": str(local_appdata), "APPDATA": ""}
        with patch.dict("os.environ", env, clear=False):
            result = get_claude_config_path()

        assert result is None


class TestIsMsixInstall:
    def test_true_when_msix_dir_exists(self, tmp_path):
        local_appdata = tmp_path / "LocalAppData"
        msix_claude = (
            local_appdata
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        msix_claude.mkdir(parents=True)

        env = {"LOCALAPPDATA": str(local_appdata)}
        with patch.dict("os.environ", env, clear=False):
            assert is_msix_install() is True

    def test_false_when_standard_only(self, tmp_path):
        local_appdata = tmp_path / "LocalAppData"
        local_appdata.mkdir()

        env = {"LOCALAPPDATA": str(local_appdata)}
        with patch.dict("os.environ", env, clear=False):
            assert is_msix_install() is False
