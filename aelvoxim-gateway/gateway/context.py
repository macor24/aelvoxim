# SPDX-License-Identifier: MIT
"""
aelvoxim_gateway.context — Read and normalize JSON snapshots from software scripts.

Each software outputs a JSON file to the temp directory.
This module reads them and converts to Aelvoxim-readable context.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Snapshot scanning ──


def scan_snapshots(temp_dir: str = "/tmp/aelvoxim_gateway") -> Dict[str, Any]:
    """Scan temp directory for the latest snapshot from each software.

    Returns dict keyed by software name (e.g. "photoshop"),
    Each value is the parsed JSON snapshot.
    """
    results: Dict[str, Any] = {}
    td = Path(temp_dir)
    if not td.exists():
        return results

    for f in sorted(td.glob("aelvoxim_snapshot_*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sw = data.get("software", "unknown")
            if sw not in results:
                results[sw] = data
        except (json.JSONDecodeError, OSError):
            continue
    return results


def get_latest(temp_dir: str = "/tmp/aelvoxim_gateway",
               software: str = "") -> Optional[Dict[str, Any]]:
    """Get the latest snapshot for a specific software."""
    snapshots = scan_snapshots(temp_dir)
    if software:
        return snapshots.get(software)
    # Return the most recent across all software
    best = None
    best_ts = ""
    for sw, data in snapshots.items():
        ts = data.get("timestamp", "")
        if ts > best_ts:
            best_ts = ts
            best = data
    return best


# ── Normalization ──


def normalize_photoshop(snapshot: Dict[str, Any]) -> str:
    """Convert a Photoshop snapshot to Aelvoxim-readable text context."""
    lines: List[str] = []
    canvas = snapshot.get("canvas", {})
    err = snapshot.get("error")
    if err:
        return f"[Photoshop] {err}"

    lines.append("[Photoshop Canvas]")
    lines.append(f"  Size: {canvas.get('width','?')}x{canvas.get('height','?')} "
                 f"@ {canvas.get('resolution','?')}dpi")
    lines.append(f"  Color Mode: {canvas.get('colorMode','?')}")
    lines.append(f"  Timestamp: {snapshot.get('timestamp','?')}")

    layers = snapshot.get("layers", [])
    lines.append(f"\n[Layers] ({len(layers)} total)")
    for i, l in enumerate(layers[:25]):
        vis = "V" if l.get("visible") else "H"
        bounds = l.get("bounds", [])
        bounds_str = f"({bounds[0]},{bounds[1]})→({bounds[2]},{bounds[3]})" if len(bounds) == 4 else ""
        text = l.get("text", "")
        text_str = f' ="{text[:40]}"' if text else ""
        lines.append(f"  {vis} #{i}: {l.get('name','?')} "
                     f"op={l.get('opacity',1)} {bounds_str}{text_str}")
    if len(layers) > 25:
        lines.append(f"  ... and {len(layers) - 25} more layers")

    sel = snapshot.get("selection")
    if sel:
        b = sel.get("bounds", [])
        lines.append(f"\n[Selection] ({b[0]},{b[1]})→({b[2]},{b[3]})")

    guides = snapshot.get("guides", [])
    if guides:
        lines.append(f"\n[Guides] ({len(guides)} total)")
        for g in guides[:5]:
            lines.append(f"  {g.get('direction','?')} @ {g.get('position','?')}")

    return "\n".join(lines)


def normalize(snapshot: Dict[str, Any]) -> str:
    """Auto-detect software type and normalize."""
    sw = snapshot.get("software", "")
    if sw == "photoshop":
        return normalize_photoshop(snapshot)
    # Fallback: JSON dump
    return f"[{sw.upper() if sw else 'UNKNOWN'}] " + json.dumps(snapshot, indent=2)


# ── Cleanup ──


def cleanup_old(temp_dir: str = "/tmp/aelvoxim_gateway",
                max_files: int = 30,
                max_age_sec: int = 3600) -> int:
    """Delete old snapshot files. Returns count removed."""
    td = Path(temp_dir)
    if not td.exists():
        return 0
    removed = 0
    now = time.time()
    for f in sorted(td.glob("aelvoxim_snapshot_*.json"),
                    key=lambda p: p.stat().st_mtime):
        try:
            age = now - f.stat().st_mtime
            if age > max_age_sec:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed
