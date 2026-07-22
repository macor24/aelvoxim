from datetime import datetime

from windows_mcp.filesystem.views import File, Directory, format_size, MAX_READ_SIZE, MAX_RESULTS


class TestFormatSize:
    def test_bytes(self):
        assert format_size(0) == "0 B"
        assert format_size(512) == "512 B"
        assert format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.0 KB"
        assert format_size(1536) == "1.5 KB"
        assert format_size(10240) == "10.0 KB"

    def test_megabytes(self):
        assert format_size(1024 ** 2) == "1.0 MB"
        assert format_size(5 * 1024 ** 2) == "5.0 MB"

    def test_gigabytes(self):
        assert format_size(1024 ** 3) == "1.0 GB"
        assert format_size(2 * 1024 ** 3) == "2.0 GB"


class TestConstants:
    def test_max_read_size(self):
        assert MAX_READ_SIZE == 10 * 1024 * 1024

    def test_max_results(self):
        assert MAX_RESULTS == 500


class TestFile:
    def _make_file(self, **overrides):
        defaults = dict(
            path=r"C:\test\file.txt",
            type="File",
            size=2048,
            created=datetime(2025, 1, 15, 10, 30, 0),
            modified=datetime(2025, 6, 20, 14, 0, 0),
            accessed=datetime(2025, 6, 21, 9, 0, 0),
            read_only=False,
        )
        defaults.update(overrides)
        return File(**defaults)

    def test_to_string_basic(self):
        f = self._make_file()
        result = f.to_string()
        assert r"C:\test\file.txt" in result
        assert "Type: File" in result
        assert "2.0 KB" in result
        assert "2,048 bytes" in result
        assert "2025-01-15 10:30:00" in result
        assert "Read-only: False" in result

    def test_to_string_with_extension(self):
        f = self._make_file(extension=".txt")
        result = f.to_string()
        assert "Extension: .txt" in result

    def test_to_string_without_extension(self):
        f = self._make_file()
        assert "Extension" not in f.to_string()

    def test_to_string_with_contents(self):
        f = self._make_file(contents_files=10, contents_dirs=3)
        result = f.to_string()
        assert "Contents: 10 files, 3 directories" in result

    def test_to_string_without_contents(self):
        f = self._make_file()
        assert "Contents" not in f.to_string()

    def test_to_string_with_link_target(self):
        f = self._make_file(link_target=r"C:\actual\target.txt")
        result = f.to_string()
        assert r"Link target: C:\actual\target.txt" in result

    def test_to_string_read_only(self):
        f = self._make_file(read_only=True)
        assert "Read-only: True" in f.to_string()


class TestDirectory:
    def test_file_entry(self):
        d = Directory(name="readme.md", is_dir=False, size=4096)
        result = d.to_string()
        assert "[FILE]" in result
        assert "readme.md" in result
        assert "4.0 KB" in result

    def test_dir_entry(self):
        d = Directory(name="src", is_dir=True)
        result = d.to_string()
        assert "[DIR ]" in result
        assert "src" in result

    def test_dir_entry_no_size(self):
        d = Directory(name="build", is_dir=True, size=999)
        result = d.to_string()
        assert "999" not in result

    def test_relative_path_override(self):
        d = Directory(name="file.py", is_dir=False, size=100)
        result = d.to_string(relative_path="sub/file.py")
        assert "sub/file.py" in result
        assert result.count("file.py") == 1

    def test_default_size_zero(self):
        d = Directory(name="test", is_dir=False)
        assert d.size == 0
