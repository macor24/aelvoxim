from unittest.mock import MagicMock
import pytest
from windows_mcp.desktop.views import Size
from windows_mcp.tree.service import Tree, _is_comtypes_variant_ord_typeerror
from windows_mcp.tree.views import BoundingBox, SemanticNode
from windows_mcp.uia import Rect


@pytest.fixture
def tree_instance():
    mock_desktop = MagicMock()
    mock_desktop.get_screen_size.return_value = Size(width=1920, height=1080)
    mock_desktop.get_screen_box.return_value = make_box(0, 0, 1920, 1080)
    return Tree(mock_desktop)


def make_box(left: int, top: int, right: int, bottom: int):
    return BoundingBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        width=right - left,
        height=bottom - top,
    )


class TestAppNameCorrection:
    def test_progman(self, tree_instance):
        assert tree_instance.app_name_correction("Progman") == "Desktop"

    def test_shell_traywnd(self, tree_instance):
        assert tree_instance.app_name_correction("Shell_TrayWnd") == "Taskbar"

    def test_shell_secondary_traywnd(self, tree_instance):
        assert tree_instance.app_name_correction("Shell_SecondaryTrayWnd") == "Taskbar"

    def test_popup_window_site_bridge(self, tree_instance):
        assert (
            tree_instance.app_name_correction("Microsoft.UI.Content.PopupWindowSiteBridge")
            == "Context Menu"
        )

    def test_passthrough(self, tree_instance):
        assert tree_instance.app_name_correction("Notepad") == "Notepad"
        assert tree_instance.app_name_correction("Calculator") == "Calculator"


class TestIouBoundingBox:
    def test_full_overlap(self, tree_instance):
        window = Rect(0, 0, 500, 500)
        element = Rect(100, 100, 200, 200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.left == 100
        assert result.top == 100
        assert result.right == 200
        assert result.bottom == 200
        assert result.width == 100
        assert result.height == 100

    def test_partial_overlap(self, tree_instance):
        window = Rect(0, 0, 150, 150)
        element = Rect(100, 100, 200, 200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.left == 100
        assert result.top == 100
        assert result.right == 150
        assert result.bottom == 150
        assert result.width == 50
        assert result.height == 50

    def test_no_overlap(self, tree_instance):
        window = Rect(0, 0, 50, 50)
        element = Rect(100, 100, 200, 200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.width == 0
        assert result.height == 0

    def test_screen_clamping(self, tree_instance):
        # Element extends beyond screen (1920x1080)
        window = Rect(0, 0, 2000, 2000)
        element = Rect(1900, 1060, 2000, 1200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.left == 1900
        assert result.top == 1060
        assert result.right == 1920
        assert result.bottom == 1080
        assert result.width == 20
        assert result.height == 20

    def test_screen_box_keeps_virtual_screen_origin(self):
        mock_desktop = MagicMock()
        mock_desktop.get_screen_size.return_value = Size(width=3840, height=1080)
        mock_desktop.get_screen_box.return_value = make_box(-1920, 0, 1920, 1080)

        tree = Tree(mock_desktop)
        result = tree.iou_bounding_box(
            Rect(-1920, 0, 0, 1080),
            Rect(-100, 100, 100, 200),
        )

        assert result.left == -100
        assert result.top == 100
        assert result.right == 0
        assert result.bottom == 200


def _type_error_from(filename: str) -> TypeError:
    namespace = {}
    code = compile("def trigger():\n    ord('hello')\n", filename, "exec")
    exec(code, namespace)

    with pytest.raises(TypeError) as exc_info:
        namespace["trigger"]()

    return exc_info.value


class TestComtypesVariantOrdTypeError:
    def test_matches_comtypes_automation_traceback(self):
        error = _type_error_from(
            "C:/Python313/Lib/site-packages/comtypes/automation.py",
        )

        assert _is_comtypes_variant_ord_typeerror(error) is True

    def test_rejects_same_message_from_non_comtypes_traceback(self):
        error = _type_error_from(
            "C:/QA_Automation/Windows-MCP-PR/tests/helpers/fake_source.py",
        )

        assert _is_comtypes_variant_ord_typeerror(error) is False


class TestTreeTraversal:
    def test_unnamed_interactive_control_does_not_add_semantic_child(
        self, tree_instance, monkeypatch
    ):
        child = MagicMock()
        child.CachedIsOffscreen = False
        child.CachedControlTypeName = "ButtonControl"
        child.CachedIsControlElement = True
        child.CachedBoundingRectangle = Rect(10, 10, 110, 60)
        child.CachedIsEnabled = True
        child.CachedHasKeyboardFocus = False
        child.CachedName = "   "
        child.CachedLocalizedControlType = "button"
        child.CachedAcceleratorKey = ""
        child.GetCachedPropertyValue.return_value = 43

        semantic_root = SemanticNode(
            control_type="Window",
            element_type="window",
            name="Window",
            window_name="Window",
        )

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            lambda node, cache_request: [],
        )
        monkeypatch.setattr(
            "windows_mcp.tree.service.random_point_within_bounding_box",
            lambda node, scale_factor: (60, 35),
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        interactive_nodes = []
        tree_instance.tree_traversal(
            child,
            Rect(0, 0, 200, 200),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
            current_semantic_node=semantic_root,
        )

        assert interactive_nodes == []
        assert semantic_root.children == []
