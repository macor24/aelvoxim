"""
path_registry — 中心化路径注册与查找。

所有 .py 文件应通过此模块获取路径，而非硬编码。
用法:
    from path_registry import registry
    path = registry.get("data:llm_config")   # → ~/.metacore/llm-config.json
    path = registry.get("module:auth")        # → /abs/path/to/metacore/server/auth.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

# ── 配置 ──

# 默认指向项目根目录下的 config/path_registry.json
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent.parent  # utils/ → metacore/ → src/ → Aelvoxim/
_SRC_ROOT = _PROJECT_ROOT / "src"
_DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "path_registry.json"


# ── 注册器 ──


class PathRegistry:
    """路径注册表 —— 启动时加载，运行时可动态注册。

    >>> registry = PathRegistry()
    >>> registry.get("data:user_data")   # 展开 ~/ 并拼接 root
    """

    def __init__(self, config_path: str | Path = ""):
        self._config_path = Path(config_path or _DEFAULT_CONFIG)
        self._registry: Dict = self._load()

    # ── 公开接口 ──

    def get(self, key: str, default: str = "") -> str:
        """按 'category:name' 格式获取完整路径。

        示例:
            registry.get("data:llm_config")
            registry.get("module:auth")
        """
        if ":" not in key:
            return default

        category, name = key.split(":", 1)
        # 兼容单数/复数: "module" → "modules", "tool" → "tools", "config" → "configs"
        _plural_map = {"module": "modules", "tool": "tools", "config": "configs", "data": "data"}
        lookup = _plural_map.get(category, category)
        relative = self._registry.get(lookup, {}).get(name)
        if not relative:
            return default

        return self._resolve(relative)

    def register(self, category: str, name: str, relative_path: str) -> None:
        """运行时注册/更新一个路径。"""
        if category not in self._registry:
            self._registry[category] = {}
        self._registry[category][name] = relative_path
        self._save()

    def list_all(self) -> Dict[str, str]:
        """列出所有已注册路径，key 为 'category:name' 格式。"""
        result: Dict[str, str] = {}
        for category, items in self._registry.items():
            if isinstance(items, dict):
                for name, rel in items.items():
                    result[f"{category}:{name}"] = self._resolve(rel)
            elif category != "root":
                # 顶层字符串（如 config 文件路径）
                result[category] = self._resolve(str(items))
        return result

    # ── 内部 ──

    def _load(self) -> Dict:
        if self._config_path.exists():
            try:
                return json.loads(self._config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {"root": "", "modules": {}, "tools": {}, "data": {}}
        return {"root": "", "modules": {}, "tools": {}, "data": {}}

    def _save(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(self._registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _resolve(self, relative: str) -> str:
        """将相对路径展开为绝对路径。
        
        规则:
        - ~/ → 展开为用户 home 目录
        - 有 root 时拼接 root
        - 已经是绝对路径的直接返回
        """
        raw = os.path.expanduser(relative)
        if raw.startswith("/"):
            return os.path.abspath(raw)
        root = self._registry.get("root", "")
        if root:
            return os.path.abspath(os.path.join(root, raw))
        # 没有 root 时，从项目根目录推算
        return os.path.abspath(os.path.join(str(_PROJECT_ROOT), raw))


# ── 模块级单例 ──

_registry: Optional[PathRegistry] = None


def get_registry(config_path: str | Path = "") -> PathRegistry:
    """获取/初始化全局路径注册表。"""
    global _registry
    if _registry is None:
        _registry = PathRegistry(config_path)
    # 确保已加载配置（文件可能在首次导入后更新）
    if not _registry._registry.get("modules"):
        _registry._registry = _registry._load()
    return _registry


# 快捷引用
registry = get_registry()
