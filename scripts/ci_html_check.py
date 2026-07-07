#!/usr/bin/env python3
"""CI-only HTML/JS syntax check — runs in GitHub Actions."""
import re
import sys
from pathlib import Path

errors = 0
for f in sorted(Path("src").rglob("*.html")):
    content = f.read_text()
    scripts = re.findall(r"<script>(.*?)</script>", content, re.DOTALL)
    for idx, s in enumerate(scripts):
        if s.count("{") != s.count("}"):
            print(f"ERROR: {f}: script#{idx+1} braces unbalanced")
            errors += 1
        if s.count("(") != s.count(")"):
            print(f"ERROR: {f}: script#{idx+1} parens unbalanced")
            errors += 1
    for tag in ["script", "form"]:
        opens = len(re.findall(f"<{tag}[\\s>]", content))
        closes = content.count(f"</{tag}>")
        if opens != closes:
            print(f"ERROR: {f}: <{tag}> mismatch (open={opens}, close={closes})")
            errors += 1
    print(f"  CHECK OK: {f}")

if errors:
    print(f"\n{errors} errors found")
    sys.exit(1)
print("\nAll HTML checks passed")
