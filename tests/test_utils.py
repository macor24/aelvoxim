"""Tests for aelvoxim.utils — shared utility functions.

Covers:
- JSON read/write
- Date/time parsing
- Directory utilities
- Internationalization (i18n)
"""
import json
import os
import tempfile
from pathlib import Path
import pytest
from aelvoxim.utils import (
    ensure_dir,
    now_str,
    parse_dt,
    hours_ago,
    read_json,
    write_json,
    get_data_dir,
)


class TestEnsureDir:
    """Directory creation utility."""

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_dir = Path(tmp) / "subdir" / "nested"
            assert not new_dir.exists()
            result = ensure_dir(new_dir)
            assert result == new_dir
            assert new_dir.exists()
            assert new_dir.is_dir()

    def test_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            result = ensure_dir(path)
            assert result == path


class TestNowStr:
    """Current time string formatting."""

    def test_returns_string(self):
        result = now_str()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_default_format(self):
        result = now_str()
        # Default format: YYYY-MM-DD HH:MM:SS
        assert len(result) >= 16

    def test_custom_format(self):
        result = now_str("%Y-%m-%d")
        assert len(result) == 10


class TestParseDt:
    """Date/time string parsing."""

    def test_valid_datetime(self):
        result = parse_dt("2026-07-13 12:30:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 7
        assert result.day == 13

    def test_invalid_string(self):
        result = parse_dt("not a date")
        assert result is None

    def test_empty_string(self):
        result = parse_dt("")
        assert result is None


class TestHoursAgo:
    """Time difference calculation."""

    def test_recent_time(self):
        from datetime import datetime, timedelta
        recent = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        result = hours_ago(recent)
        assert result is not None
        assert 1.0 <= result <= 3.0

    def test_old_time(self):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        result = hours_ago(old)
        assert result is not None
        assert result >= 100.0

    def test_invalid_string(self):
        result = hours_ago("invalid")
        assert result is None


class TestReadWriteJson:
    """JSON file operations."""

    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            data = {"name": "aelvoxim", "version": 1}
            success = write_json(path, data)
            assert success is True
            assert path.exists()

            loaded = read_json(path)
            assert loaded is not None
            assert loaded["name"] == "aelvoxim"
            assert loaded["version"] == 1

    def test_write_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            assert write_json(path, {}) is True

    def test_read_nonexistent(self):
        result = read_json(Path("/tmp/nonexistent_file_xyz.json"))
        assert result is None

    def test_read_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{invalid json")
            result = read_json(path)
            assert result is None


class TestGetDataDir:
    """Data directory resolution."""

    def test_returns_path(self):
        result = get_data_dir()
        assert isinstance(result, Path)
        assert result.exists()
