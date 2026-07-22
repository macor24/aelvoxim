"""
metacore.experts.safety — Safety Expert.

Pure safety/SentriKit layer — separate from ethics.
Calls SentriKit API via HTTP for red-line rule checking (R0-R28).
Auto-degrades to local fallback when SentriKit is unreachable.

This expert does NOT import any metacore modules —
it's a pure HTTP client to SentriKit (8899).
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register

import logging
_log = logging.getLogger("aelvoxim.experts.safety")

# ── SentriKit config ──
_SENTRIKIT_HOST = os.environ.get("SENTRIKIT_HOST", "https://127.0.0.1:8899")
_SENTRIKIT_API_KEY = os.environ.get("SENTRIKIT_API_KEY", "")
if not _SENTRIKIT_API_KEY:
    try:
        from ..utils import DATA_DIR
        _key_file = DATA_DIR / "sentrikit.key"
        if _key_file.exists():
            _SENTRIKIT_API_KEY = _key_file.read_text().strip()
    except Exception:
        _log.exception("safety error")
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ── R-label to rule name mapping (SentriKit returns "R3", use local names for priority) ──
_R_LABELS: Dict[str, str] = {
    "R0": "sandbox_escape",
    "R1": "delete_protection",
    "R2": "data_leak",
    "R3": "destructive_action",
    "R4": "safety_rules_protection",
    "R5": "self_auth",
    "R9": "prompt_injection",
    "R12": "network_exfil",
    "R13": "self_replication",
    "R26": "memory_poisoning",
    "R28": "recursive_improvement",
}

_RULE_PRIORITY: Dict[str, int] = {
    "sandbox_escape": 100,
    "delete_protection": 90,
    "data_leak": 85,
    "destructive_action": 80,
    "safety_rules_protection": 65,
    "self_auth": 70,
    "prompt_injection": 60,
    "network_exfil": 55,
    "self_replication": 50,
    "memory_poisoning": 45,
    "recursive_improvement": 40,
}

# ── Local fallback patterns (when SentriKit is unreachable) ──
_SAFETY_BLOCK_PATTERNS = [
    "DROP TABLE", "DROP DATABASE", "TRUNCATE",
    "rm -rf", "rm -rf /", ":(){ :|:& };:", "fork bomb",
    "chmod 777", "chown root",
    "self-replicat", "self_replicat", "clone itself",
    "fork bomb", "replicate", "autonomous replicat",
]

# ── Low-level helpers ──
def _call_sentrikit_safety(
    action: str,
    target: str,
    content: str = "",
) -> Optional[Dict]:
    """Call SentriKit /api/safety/check. Returns None on failure."""
    try:
        body = json.dumps({
            "action": action,
            "target": (target or "")[:500],
            "trigger": "safety_expert",
            "content": (content or "")[:2000],
        }).encode()
        req = urllib.request.Request(
            f"{_SENTRIKIT_HOST}/api/safety/check",
            data=body,
            headers={"Content-Type": "application/json", "X-API-Key": _SENTRIKIT_API_KEY},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _local_safety_check(text: str) -> Dict:
    """Local safety fallback when SentriKit unreachable."""
    text_upper = (text or "").upper()
    for pattern in _SAFETY_BLOCK_PATTERNS:
        if pattern.upper() in text_upper:
            return {"allowed": False, "reason": f"Local block: pattern '{pattern}' detected"}
    return {"allowed": True, "reason": "Local check passed"}


def _extract_rule(reason: str) -> str:
    """Extract rule name from SentriKit reason string (e.g. 'R3' → 'destructive_action')."""
    if not reason:
        return ""
    for r_label, r_name in _R_LABELS.items():
        if r_label in reason:
            return r_name
    return ""


_BLOCK_SUGGESTIONS: Dict[str, str] = {
    "forbidden pattern": (
        "Avoid using '~' or '..' in paths — use the full absolute path instead."
    ),
    "R2": "This looks like a request to share sensitive information.",
    "R3": "Destructive operation detected. Use '--dry-run' to preview changes first.",
}


def _add_suggestion(reason: str) -> str:
    """Append user-friendly suggestion to block reason."""
    rl = reason.lower()
    for key, suggestion in _BLOCK_SUGGESTIONS.items():
        if key.lower() in rl:
            return f"{reason} {suggestion}"
    return reason


@register
class SafetyExpert(BaseExpert):
    """Evaluates safety via SentriKit red-line rules (R0-R28)."""
    _capabilities = ["safety", "security", "audit", "compliance"]

    name = "safety"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if ethics has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "sentrikit_check": {},
            "rules_triggered": [],
            "priority_chain": [],
        }

        # 1. Try SentriKit
        sentrikit_result = _call_sentrikit_safety("create", inp.query, content=inp.query)
        if sentrikit_result is None:
            sentrikit_result = _local_safety_check(inp.query)
            source = "local_fallback (sentrikit unavailable)"
        else:
            source = "sentrikit"

        # 2. Local double-check (always run, even when SentriKit is available)
        local_result = _local_safety_check(inp.query)
        local_blocked = not local_result.get("allowed", True)

        details["sentrikit_check"] = {
            "allowed": sentrikit_result.get("allowed", True),
            "reason": _add_suggestion(sentrikit_result.get("reason", "")),
            "source": source,
        }
        details["local_check"] = {
            "allowed": local_result.get("allowed", True),
            "reason": local_result.get("reason", ""),
        }

        # 3. Check for disagreement (double-check flag)
        sentrikit_allowed = sentrikit_result.get("allowed", True)
        if sentrikit_allowed != local_result.get("allowed", True):
            details["double_check"] = {
                "sentrikit": sentrikit_allowed,
                "local": local_result.get("allowed", True),
                "disagreement": True,
            }

        # 4. Extract triggered rules
        reason = sentrikit_result.get("reason", "")
        rule = _extract_rule(reason)
        if rule:
            details["rules_triggered"].append(rule)
            details["priority_chain"].append(f"{rule} (priority {_RULE_PRIORITY.get(rule, 0)})")

        # 5. Build opinion — conservative: block if either check blocks
        blocked = not sentrikit_allowed or not local_result.get("allowed", True)
        if blocked:
            opinion = f"SAFETY BLOCK (via {source}): {details['sentrikit_check']['reason']}"
        else:
            opinion = f"Safety check passed (via {source})"

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion,
            confidence=0.0 if blocked else 0.9,
            details=details,
            error="Safety block" if blocked else None,
        )
