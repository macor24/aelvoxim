"""MetaCore quality check script.

Usage:
    python scripts/quality_check.py

Or import and use programmatically:
    from scripts.quality_check import run_quality_check
    report = run_quality_check()
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run_quality_check() -> dict:
    """Run all quality checks and return a report dict."""
    import json, time
    from pathlib import Path

    report = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": {}}

    # ── 1. Direction decomposition ──
    from aelvoxim.learn.decompose import decompose_direction, detect_lang

    test_dirs = [
        "FastAPI async optimization patterns",
        "Python threading safety patterns",
        "Database index optimization SQLite",
        "FastAPI 异步优化实践",
        "非同期処理の最適化",
    ]

    decompose_ok = 0
    for topic in test_dirs:
        tasks = decompose_direction(topic, lambda m: None)
        if len(tasks) >= 6:
            decompose_ok += 1
    report["checks"]["decompose"] = {
        "score": round(decompose_ok / len(test_dirs), 2),
        "detail": f"{decompose_ok}/{len(test_dirs)} directions produced >=6 tasks",
    }

    # ── 2. True execution ──
    from aelvoxim.learn.execute import try_execute_task
    from aelvoxim.learn.extract import is_generic_template_output, content_has_real_value

    test_tasks = [
        ("route design", "FastAPI"),
        ("dependency", "FastAPI"),
        ("database setup", "SQLite"),
        ("index", "SQLite"),
        ("test coverage", "pytest"),
        ("deploy to production", "Docker"),
        ("config setup", "aelvoxim"),
        ("function implementation", "Python"),
    ]

    exec_ok = 0
    knowledge_ok = 0
    value_ok = 0
    for task, topic in test_tasks:
        content = try_execute_task(topic, task)
        if content:
            exec_ok += 1
            if "=== Knowledge:" in content:
                knowledge_ok += 1
            if content_has_real_value(content) and not is_generic_template_output(content):
                value_ok += 1

    report["checks"]["execution"] = {
        "score": round(exec_ok / len(test_tasks), 2) if test_tasks else 0,
        "success_rate": f"{exec_ok}/{len(test_tasks)}",
        "knowledge_rate": f"{knowledge_ok}/{exec_ok}",
        "value_rate": f"{value_ok}/{exec_ok}",
    }

    # ── 3. Quality gate ──
    from aelvoxim.core.judge import KnowledgeProposal, score_knowledge_entry, JudgeGrade

    test_contents = [
        ("template_meta", '{"python":"3.12","platform":"linux","task":"t"}', False),
        ("short_status", "FastAPI route/DI pipeline validated", False),
        ("real_knowledge", "FastAPI uses ASGI. Depends() enables DI. APIRouter groups routes with prefix.", True),
    ]

    gate_ok = 0
    for name, content, should_pass in test_contents:
        is_template = is_generic_template_output(content)
        has_value = content_has_real_value(content)
        kp = KnowledgeProposal(topic="test", content=content, source="execution_result",
                              confidence=0.9, content_length=len(content), has_execution=True)
        jr = score_knowledge_entry(kp)
        passes = not is_template and has_value and jr.grade != JudgeGrade.D
        if passes == should_pass:
            gate_ok += 1

    report["checks"]["quality_gate"] = {
        "score": round(gate_ok / len(test_contents), 2),
        "detail": f"{gate_ok}/{len(test_contents)} filter decisions correct",
    }

    # ── 4. Knowledge base ──
    from aelvoxim.learn.knowledge import KnowledgeBase

    kb = KnowledgeBase()
    result = kb.store("qc-test", "QC Test Entry", "Quality check test entry", source="manual")
    store_ok = result.get("_status") == "active"
    found = kb.get_by_title("QC Test Entry") is not None
    search_ok = len(list(kb.search("QC", limit=5))) > 0

    report["checks"]["knowledge_base"] = {
        "score": 1.0 if (store_ok and found and search_ok) else 0.0,
        "store": store_ok,
        "get_by_title": found,
        "search": search_ok,
    }

    # ── 5. Overall score ──
    scores = [v["score"] if isinstance(v, dict) and "score" in v else 0
              for v in report["checks"].values()]
    report["overall"] = round(sum(scores) / len(scores), 2) if scores else 0

    return report


if __name__ == "__main__":
    import json
    report = run_quality_check()
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # Summary bar
    print()
    print(f"Overall score: {report['overall']:.0%}")
    bar = "█" * int(report["overall"] * 10) + "░" * (10 - int(report["overall"] * 10))
    print(f"  {bar}")
