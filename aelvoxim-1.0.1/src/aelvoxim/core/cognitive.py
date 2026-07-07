"""aelvoxim.core.cognitive — Cognitive conflict detection

Detects contradictions between knowledge entries.
Pure algorithm — accepts input data, returns conflict results.
No dependency on MemorySystem or any storage layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .calibration import get_calibration


class CognitiveConflict:
    """A cognitive conflict record."""

    def __init__(
        self,
        source_a: str,
        source_b: str,
        description: str,
        severity: str = "mild",
        layer: str = "knowledge",
        resolved: bool = False,
    ):
        self.source_a = source_a
        self.source_b = source_b
        self.description = description
        self.severity = severity
        self.layer = layer
        self.resolved = resolved
        self.detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.resolved_at: str = ""

    def to_dict(self) -> Dict:
        return {
            "source_a": self.source_a,
            "source_b": self.source_b,
            "description": self.description,
            "severity": self.severity,
            "layer": self.layer,
            "resolved": self.resolved,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
        }


class ConflictDetector:
    """Conflict detector — scans provided entries for cognitive contradictions.
    
    Pure algorithm. Takes list of entries as input, no storage dependency.
    """

    def __init__(self):
        self._conflicts: List[CognitiveConflict] = []
        self._cal = get_calibration()

    def scan_entries(self, entries: List[Dict]) -> List[CognitiveConflict]:
        """Scan a list of knowledge entries for conflicts.
        
        Args:
            entries: List of dicts with keys: topic, content, confidence, source
        """
        self._conflicts = []
        
        # Compare all entry pairs for contradictions
        for i, a in enumerate(entries):
            for b in entries[i + 1:]:
                topic_a = str(a.get("topic", "") or "")
                topic_b = str(b.get("topic", "") or "")
                content_a = str(a.get("content", "") or "")
                content_b = str(b.get("content", "") or "")
                
                # Skip if same topic
                if topic_a == topic_b and topic_a:
                    continue
                
                # Detect direct contradictions: one says "don't X", other says "do X"
                negation_words = ["not", "never", "avoid", "don't", "shouldn't", "不", "不要", "禁止"]
                for word in negation_words:
                    if word in content_a.lower() and word not in content_b.lower():
                        # Extract what's being negated
                        for tech_word in ["fastapi", "postgresql", "docker", "async", "threading",
                                         "cache", "logging", "deploy", "config", "migration"]:
                            if tech_word in content_a.lower() and tech_word in content_b.lower():
                                self._conflicts.append(CognitiveConflict(
                                    source_a=topic_a or f"entry-{i}",
                                    source_b=topic_b or f"entry-{i+1}",
                                    description=f"Conflict: {topic_a} advises against {tech_word}, "
                                                f"but {topic_b} recommends it",
                                    severity="moderate",
                                    layer="knowledge",
                                ))
                                break
                    if self._conflicts and self._conflicts[-1].source_a == (topic_a or f"entry-{i}"):
                        break
        
        return self._conflicts

    def get_status(self) -> Dict:
        unresolved = [c for c in self._conflicts if not c.resolved]
        return {
            "total_conflicts": len(self._conflicts),
            "unresolved": len(unresolved),
            "by_severity": {
                "critical": sum(1 for c in unresolved if c.severity == "critical"),
                "moderate": sum(1 for c in unresolved if c.severity == "moderate"),
                "mild": sum(1 for c in unresolved if c.severity == "mild"),
            },
        }


__all__ = [
    "CognitiveConflict",
    "ConflictDetector",
]
