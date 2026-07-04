"""
metacore.learn.patches.validate_safe — Monkey-patch fix for _jr NameError (P0-5).

Original bug: validate.py:189
    is_validated = combined_score >= 0.6 and _jr.grade.value in ("A", "S", "B") if '_jr' in dir() else False

Problem: When combined_score >= 0.6 and Judge block raised an exception,
_jr is never assigned. The guard 'if '_jr' in dir() else False' is evaluated
AFTER 'combined_score >= 0.6 and _jr.grade.value...' — the _jr access happens
before the guard, causing NameError.

Fix: Replace the unsafe expression with a try/except wrapper.
Applied at runtime via monkey-patch in server startup.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("aelvoxim.patches.validate_safe")


def safe_is_validated(combined_score: float, _jr_grade_value=None) -> bool:
    """Safe version of the is_validated expression.
    
    Returns True only if combined_score >= 0.6 AND judge grade is A/S/B.
    Never raises NameError when _jr is unavailable.
    """
    if combined_score < 0.6:
        return False
    try:
        return _jr_grade_value in ("A", "S", "B")
    except Exception:
        return False


def patch_execute_and_validate():
    """Monkey-patch the problematic line in validate.execute_and_validate.
    
    Replaces the ENTIRE function with a corrected version that uses
    safe_is_validated() instead of the inline ternary expression.
    """
    import sys
    import os
    
    # Read the original file
    here = os.path.dirname(os.path.abspath(__file__))
    validate_path = os.path.join(here, "..", "validate.py")
    
    if not os.path.exists(validate_path):
        _log.warning("validate.py not found at %s, skip patch", validate_path)
        return False
    
    import ast
    import textwrap
    
    try:
        with open(validate_path, "r", encoding="utf-8") as f:
            source = f.read()
        
        # Backup
        bak_path = validate_path + ".bak.p0"
        if not os.path.exists(bak_path):
            with open(bak_path, "w", encoding="utf-8") as f:
                f.write(source)
            _log.info("Backup saved to %s", bak_path)
        
        # The problematic line pattern we need to replace
        old_line = "    is_validated = combined_score >= 0.6 and _jr.grade.value in (\"A\", \"S\", \"B\") if '_jr' in dir() else False"
        new_line = "    is_validated = safe_is_validated(combined_score, _jr.grade.value if '_jr' in dir() else None)"
        
        if old_line in source:
            source = source.replace(old_line, new_line, 1)
            # Add the import at the top
            import_stmt = "from .patches.validate_safe import safe_is_validated\n"
            if import_stmt not in source:
                source = source.replace(
                    "from ..core import judge as _judge",
                    "from ..core import judge as _judge\n" + import_stmt,
                )
            
            with open(validate_path, "w", encoding="utf-8") as f:
                f.write(source)
            
            # Verify syntax
            try:
                ast.parse(source)
                _log.info("validate.py patched successfully (P0-5)")
                return True
            except SyntaxError as e:
                _log.error("Patch produced invalid syntax: %s", e)
                # Restore backup
                with open(bak_path, "r", encoding="utf-8") as f:
                    source = f.read()
                with open(validate_path, "w", encoding="utf-8") as f:
                    f.write(source)
                return False
        else:
            _log.warning("Patched line not found in validate.py (already patched?)")
            return True
    except Exception as e:
        _log.error("Patch failed: %s", e)
        return False
