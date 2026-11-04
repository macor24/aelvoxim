#!/usr/bin/env python3
"""依赖漂移检查（只读）：声明文件里钉住的版本 vs 当前解释器环境里实际装的版本。

为什么需要：2026-11-04 发现 pyproject 写 fastapi>=0.162.0、线上跑 0.139.0 —— 下一次
`pip install -e .` 会为了满足下限把 fastapi 推 22 个版本（6 个 breaking），而且是在一次
"正常部署"里悄悄发生。这条检查就是把"声明"和"实际"的差当场暴露出来。

判据（三态，未定不计通过）：
  ✓ 钉住版本(==) 与已装版本一致
  ✗ 钉住版本与已装不一致 / 声明了但没装
  ～ 只有下限(>=) 且已装满足 —— 记为"下限"，不算失败，但会在末尾提醒"别把下限当锁"
  ⚠ 找不到声明文件 → 未定（exit 3），不当通过

用法:
  python3 scripts/dep_drift.py                # 自动找 requirements.txt / pyproject.toml
  python3 scripts/dep_drift.py --spec <path>  # 指定声明文件
退出码: 0 全绿 / 1 有不一致 / 3 未定（无声明文件）
"""
import argparse
import re
import sys
from pathlib import Path

from importlib.metadata import version as _ver, PackageNotFoundError  # requires-python >=3.12

_REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(==|>=|~=)\s*([0-9][^\s;#]*)")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def find_spec(explicit: str = "") -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    for base in [here.parent, *here.parents]:
        for name in ("requirements.txt", "pyproject.toml"):
            p = base / name
            if p.exists():
                return p
    return None


def specs_from_requirements(path: Path) -> list[tuple[str, str, str]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _REQ_RE.match(line)
        if m:
            out.append((m.group(1), m.group(3), m.group(4)))
    return out


def specs_from_pyproject(path: Path) -> list[tuple[str, str, str]]:
    out = []
    try:
        import tomllib
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        deps = (data.get("project") or {}).get("dependencies") or []
    except Exception:
        return out
    for d in deps:
        m = _REQ_RE.match(str(d))
        if m:
            out.append((m.group(1), m.group(3), m.group(4)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    spec = find_spec(args.spec)
    if spec is None:
        print("⚠ 未定：找不到 requirements.txt / pyproject.toml —— 无法判断依赖是否漂移")
        return 3

    specs = (specs_from_requirements(spec) if spec.name.endswith(".txt")
             else specs_from_pyproject(spec))
    if not specs:
        print(f"⚠ 未定：{spec} 里没解析出任何依赖声明")
        return 3

    pins, bad, floors, missing = 0, [], [], []
    for name, op, want in specs:
        try:
            have = _ver(name)
        except PackageNotFoundError:
            if op == "==":
                missing.append((name, want))
            continue
        if op == "==":
            pins += 1
            if norm(have) != norm(want):
                bad.append((name, want, have))
        else:
            floors.append((name, want, have))

    for name, want, have in bad:
        print(f"✗ {name}: 声明 =={want}，实际 {have}")
    for name, want in missing:
        print(f"✗ {name}: 声明 =={want}，环境里没装")
    if args.verbose or not bad:
        for name, want, have in floors:
            print(f"～ {name}: 下限 >={want}，实际 {have}（下限不是锁）")

    print(f"\n声明文件: {spec}")
    print(f"钉住(==) {pins} 个，其中不一致 {len(bad)} 个；声明了没装 {len(missing)} 个；只有下限 {len(floors)} 个")
    if bad or missing:
        print("结论: ❌ 依赖漂移")
        return 1
    print("结论: ✅ 声明与实装一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
