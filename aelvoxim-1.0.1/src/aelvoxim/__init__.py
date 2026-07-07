"""aelvoxim.init — Aelvoxim MetaCore

A zero-dependency, pure-stdlib, self-evolving AI Agent framework.
"""

from __future__ import annotations

import os

_EDITION = os.environ.get("METACORE_EDITION", os.environ.get("AELVOXIM_EDITION", "enterprise"))
__version__ = "1.0.1"
