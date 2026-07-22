from windows_mcp.desktop.views import Browser, Status, Size, DesktopState


class TestBrowser:
    def test_has_process_chrome(self):
        assert Browser.has_process("chrome.exe") is True

    def test_has_process_edge(self):
        assert Browser.has_process("msedge.exe") is True

    def test_has_process_firefox(self):
        assert Browser.has_process("firefox.exe") is True

    def test_has_process_case_insensitive(self):
        assert Browser.has_process("Chrome.EXE") is True
        assert Browser.has_process("MSEDGE.EXE") is True

    def test_has_process_unknown(self):
        assert Browser.has_process("notepad.exe") is False

    def test_has_process_empty_string(self):
        assert Browser.has_process("") is False


class TestStatus:
    def test_enum_values(self):
        assert Status.MAXIMIZED.value == "Maximized"
        assert Status.MINIMIZED.value == "Minimized"
        assert Status.NORMAL.value == "Normal"
        assert Status.HIDDEN.value == "Hidden"


class TestSize:
    def test_to_string_standard(self):
        s = Size(width=1920, height=1080)
        assert s.to_string() == "(1920,1080)"

    def test_to_string_zero(self):
        s = Size(width=0, height=0)
        assert s.to_string() == "(0,0)"


class TestWindow:
    def test_to_row(self, sample_window):
        row = sample_window.to_row()
        assert row == ["Untitled - Notepad", 0, "Normal", 200, 100, 12345]


class TestDesktopState:
    def test_active_desktop_to_string(self, sample_desktop_state):
        result = sample_desktop_state.active_desktop_to_string()
        assert "Desktop 1" in result

    def test_desktops_to_string(self, sample_desktop_state):
        result = sample_desktop_state.desktops_to_string()
        assert "Desktop 1" in result
        assert "Desktop 2" in result

    def test_active_window_to_string_none(self):
        ds = DesktopState(
            active_desktop={"name": "Desktop 1"},
            all_desktops=[],
            active_window=None,
            windows=[],
        )
        assert ds.active_window_to_string() == "No active window found"

    def test_active_window_to_string_with_window(self, sample_desktop_state):
        result = sample_desktop_state.active_window_to_string()
        assert "Untitled - Notepad" in result
        assert "Normal" in result

    def test_windows_to_string_empty(self):
        ds = DesktopState(
            active_desktop={"name": "Desktop 1"},
            all_desktops=[],
            active_window=None,
            windows=[],
        )
        assert ds.windows_to_string() == "No windows found"

    def test_windows_to_string_with_windows(self, sample_desktop_state):
        result = sample_desktop_state.windows_to_string()
        assert "Untitled - Notepad" in result
