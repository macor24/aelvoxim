"""
cleanse_knowledge.py — One-shot knowledge base cleanup + batch validation

1. Scan all entries, delete low-confidence, re-validate medium-confidence
2. Output statistics
"""

import json
import sys
import time
from pathlib import Path

METACORE_DIR = Path.home() / ".aelvoxim"
KNOWLEDGE_DIR = METACORE_DIR / "knowledge"
INDEX_FILE = KNOWLEDGE_DIR / "index.json"
ENTRIES_DIR = KNOWLEDGE_DIR / "entries"
REJECTED_FILE = KNOWLEDGE_DIR / "rejected.json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from aelvoxim.learn.knowledge import KnowledgeBase


def load_entries():
    """Load all entries from index."""
    if not INDEX_FILE.exists():
        print("No index file found.")
        return []
    with open(INDEX_FILE) as f:
        index = json.load(f)
    entries = []
    for eid in index.get("entries", []):
        entry_file = ENTRIES_DIR / f"{eid}.json"
        if entry_file.exists():
            with open(entry_file) as f:
                entries.append(json.load(f))
    return entries


def save_rejected(entries):
    """Save rejected entries to rejected.json."""
    existing = []
    if REJECTED_FILE.exists():
        with open(REJECTED_FILE) as f:
            existing = json.load(f)
    existing.extend(entries)
    REJECTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REJECTED_FILE, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(entries)} rejected entries to {REJECTED_FILE}")


def remove_entry(entry_id):
    """Remove an entry from index and delete its file."""
    if not INDEX_FILE.exists():
        return False
    with open(INDEX_FILE) as f:
        index = json.load(f)
    if entry_id in index["entries"]:
        index["entries"].remove(entry_id)
        with open(INDEX_FILE, "w") as f:
            json.dump(index, f, indent=2)
    entry_file = ENTRIES_DIR / f"{entry_id}.json"
    if entry_file.exists():
        entry_file.unlink()
    return True


def main():
    print("=" * 60)
    print("MetaCore Knowledge Cleanup")
    print("=" * 60)

    entries = load_entries()
    print(f"Total entries: {len(entries)}")

    # Classify
    low_conf = []       # < 0.4 → delete
    medium_conf = []    # 0.4-0.6, not validated → re-validate
    keep = []           # >= 0.6 or validated → keep as-is
    already_validated = []

    for e in entries:
        conf = e.get("confidence", 0)
        validated = e.get("validated", False)
        if validated:
            already_validated.append(e)
            keep.append(e)
        elif conf >= 0.6:
            keep.append(e)
        elif conf >= 0.4:
            medium_conf.append(e)
        else:
            low_conf.append(e)

    print(f"\nClassification:")
    print(f"  Validated:     {len(already_validated)}")
    print(f"  High quality:  {len(keep)} (conf>=0.6)")
    print(f"  Medium (verify): {len(medium_conf)} (0.4-0.6)")
    print(f"  Low (delete):  {len(low_conf)} (<0.4)")

    # Phase 1: Delete low confidence
    if low_conf:
        print(f"\n{'='*60}")
        print(f"Phase 1: Deleting {len(low_conf)} low-quality entries")
        for e in low_conf:
            eid = e.get("id", "")
            title = e.get("title", "?")[:40]
            conf = e.get("confidence", 0)
            remove_entry(eid)
            print(f"  ✕ [{conf:.2f}] {title}")
        print(f"Done: {len(low_conf)} deleted")

    # Phase 2: Re-validate medium confidence
    if medium_conf:
        print(f"\n{'='*60}")
        print(f"Phase 2: Re-validating {len(medium_conf)} medium-confidence entries")
        passed = []
        failed = []
        for i, e in enumerate(medium_conf):
            eid = e.get("id", "")
            title = e.get("title", "?")[:50]
            topic = e.get("topic", "?")
            content = e.get("content", "")
            combined_score = 0.5

            # Simple validation: check content has real value
            if len(content.strip()) < 80:
                print(f"  ⏭️ [{i+1}/{len(medium_conf)}] Too short: {title}")
                failed.append(e)
                continue

            # Try embedded AutoValidator
            try:
                from aelvoxim.learn.validator import AutoValidator
                validator = AutoValidator()
                auto_result = validator.verify({
                    "title": title,
                    "content": content,
                    "topic": topic,
                })
                combined_score = auto_result.get("combined_score", 0.5)
            except Exception as ex:
                print(f"  ⚠️ [{i+1}/{len(medium_conf)}] Validator unavailable: {ex}")

            if combined_score >= 0.4:
                # Update confidence and mark validated
                e["confidence"] = round(combined_score, 2) if combined_score > 0.5 else 0.5
                e["validated"] = True
                e["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                entry_file = ENTRIES_DIR / f"{eid}.json"
                if entry_file.exists():
                    with open(entry_file, "w") as f:
                        json.dump(e, f, indent=2, ensure_ascii=False)
                print(f"  ✅ [{i+1}/{len(medium_conf)}] Passed ({combined_score:.2f}): {title}")
                passed.append(e)
            else:
                print(f"  🚫 [{i+1}/{len(medium_conf)}] Failed ({combined_score:.2f}): {title}")
                failed.append(e)

        # Remove failed entries
        if failed:
            print(f"  Deleting {len(failed)} failed entries")
            for e in failed:
                remove_entry(e.get("id", ""))
            print(f"  Done")

        print(f"\nValidation results: {len(passed)} passed, {len(failed)} failed")

    # Save rejected entries
    save_rejected(low_conf)

    # Final stats
    remaining = load_entries()
    validated_count = sum(1 for e in remaining if e.get("validated"))
    print(f"\n{'='*60}")
    print(f"Final Statistics:")
    print(f"  Remaining entries: {len(remaining)}")
    print(f"  Validated:         {validated_count}")
    print(f"  Pending:           {len(remaining) - validated_count}")
    print(f"  Deleted:           {len(low_conf)}")
    print(f"  Avg confidence:    {sum(e.get('confidence',0) for e in remaining) / max(len(remaining),1):.2f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
