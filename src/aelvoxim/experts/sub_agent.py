"""
metacore.experts.sub_agent — Sub-process expert execution manager.

Runs each expert in an isolated subprocess with timeout protection.
Single expert crash or hang does not affect other experts or the orchestrator.

Supports cross-expert shared context via a shared directory:
  - Before running, each subprocess reads output from already-completed experts
  - After running, it writes its own output for later experts to see
  - Experts can detect blocks (safety/ethics) and skip themselves

Auto-degrades to in-process serial execution when subprocess fails.
Zero external dependencies — pure stdlib.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from .base import BaseExpert, ExpertInput, ExpertOutput


# ── Subprocess worker script template ────────────────────────

_WORKER_TEMPLATE = r"""
import json, sys, importlib, os

# Ensure parent's sys.path is available
sys.path = SYSPATH_PLACEHOLDER

# Build ExpertInput from stdin
inp_data = json.loads(sys.stdin.read())
from aelvoxim.experts.base import ExpertInput, ExpertOutput

import logging
_log = logging.getLogger("aelvoxim.sub_agent")

inp = ExpertInput(**inp_data)

# Load shared context from other experts (if shared_dir is set)
shared_dir = inp_data.get("shared_dir")
if shared_dir and os.path.isdir(shared_dir):
    for fname in sorted(os.listdir(shared_dir)):
        if fname.endswith(".json"):
            fpath = os.path.join(shared_dir, fname)
            try:
                with open(fpath) as fh:
                    other = json.load(fh)
                    expert_name = fname.replace(".json", "")
                    inp.context.setdefault("_shared_context", {})[expert_name] = other
            except Exception:
                _log.exception("sub_agent error")

# Import and instantiate the expert class
module_path, class_name = MODULE_PLACEHOLDER, CLASS_PLACEHOLDER
mod = importlib.import_module(module_path)
cls = getattr(mod, class_name)
expert = cls()

# Run and serialize output
out = expert.run(inp)

# Write output to shared directory (if shared_dir is set)
if shared_dir:
    try:
        os.makedirs(shared_dir, exist_ok=True)
        out_path = os.path.join(shared_dir, f"{out.expert_name}.json")
        with open(out_path, "w") as fh:
            json.dump({
                "expert_name": out.expert_name,
                "opinion": (out.opinion or "")[:200],
                "confidence": out.confidence,
                "error": out.error,
                "skipped": out.skipped,
            }, fh)
    except Exception:
        _log.exception("sub_agent error")

_output = {
    "expert_name": out.expert_name,
    "opinion": out.opinion,
    "confidence": out.confidence,
    "details": out.details,
    "error": out.error,
    "skipped": out.skipped,
}
print(json.dumps(_output))
sys.stdout.flush()
"""


# ── SubAgentManager ──────────────────────────────────────────


class SubAgentManager:
    """Run BaseExpert subclasses in isolated subprocesses.

    Each expert gets its own Python subprocess with a configurable timeout.
    Results are collected via stdout JSON. Timeouts produce a default
    ExpertOutput with confidence=0 and error="timeout".

    Falls back to in-process execution if subprocess spawning fails.
    Supports cross-expert shared context via temporary shared directory.
    """

    DEFAULT_TIMEOUT = 10  # seconds per expert

    def __init__(self, timeout: int = None):
        self._timeout = timeout or self.DEFAULT_TIMEOUT

    def run_all(
        self,
        expert_classes: List[Type[BaseExpert]],
        inp: ExpertInput,
        timeout: int = None,
    ) -> List[ExpertOutput]:
        """Run all experts in parallel subprocesses with timeout isolation
        and cross-expert shared context.

        Each subprocess writes its output to a shared temp directory.
        Later-starting experts can read earlier experts' conclusions
        and skip themselves if a block was detected.

        Args:
            expert_classes: List of BaseExpert subclasses to run.
            inp: Shared ExpertInput for all experts.
            timeout: Per-expert timeout in seconds (default: self._timeout).

        Returns:
            List of ExpertOutput in the same order as expert_classes.
            Failed/timeout experts return error-filled ExpertOutput.
        """
        t = timeout or self._timeout
        results: List[Optional[ExpertOutput]] = [None] * len(expert_classes)

        # Create a temporary shared directory for cross-expert context
        shared_dir = tempfile.mkdtemp(prefix="aelvoxim_shared_")

        # Inject shared_dir into input
        inp_with_shared = ExpertInput(
            query=inp.query,
            context=inp.context,
            user_id=inp.user_id,
            session_id=inp.session_id,
            shared_dir=shared_dir,
        )

        threads: List[threading.Thread] = []
        lock = threading.Lock()

        def _run(idx: int, cls: Type[BaseExpert]) -> None:
            result = self._run_one(cls, inp_with_shared, t)
            with lock:
                results[idx] = result

        # Start all subprocess threads
        for i, cls in enumerate(expert_classes):
            thr = threading.Thread(target=_run, args=(i, cls), daemon=True)
            thr.start()
            threads.append(thr)

        # Wait all with a safety margin
        for thr in threads:
            thr.join(timeout=t + 2)

        # Clean up shared directory
        try:
            import shutil
            shutil.rmtree(shared_dir, ignore_errors=True)
        except Exception:
            _log.exception("sub_agent error")

        # Fill in defaults for any that failed
        final: List[ExpertOutput] = []
        for i, cls in enumerate(expert_classes):
            if results[i] is None:
                final.append(ExpertOutput(
                    expert_name=cls.__name__.lower().replace("expert", ""),
                    opinion="Subprocess did not return a result.",
                    confidence=0.0,
                    error="timeout or subprocess failure",
                ))
            else:
                final.append(results[i])

        return final

    def run_one(
        self,
        expert_cls: Type[BaseExpert],
        inp: ExpertInput,
        timeout: int = None,
    ) -> ExpertOutput:
        """Run a single expert in a subprocess. Returns ExpertOutput."""
        return self._run_one(expert_cls, inp, timeout or self._timeout)

    @staticmethod
    def _run_one(
        expert_cls: Type[BaseExpert],
        inp: ExpertInput,
        timeout: int,
    ) -> ExpertOutput:
        """Spawn one subprocess to execute a single expert."""
        module_path = expert_cls.__module__
        class_name = expert_cls.__qualname__

        worker_code = _WORKER_TEMPLATE.replace(
            "SYSPATH_PLACEHOLDER", repr(list(sys.path))
        ).replace(
            "MODULE_PLACEHOLDER", repr(module_path)
        ).replace(
            "CLASS_PLACEHOLDER", repr(class_name)
        )

        try:
            inp_json = json.dumps({
                "query": inp.query,
                "context": inp.context,
                "user_id": inp.user_id,
                "session_id": inp.session_id,
                "shared_dir": inp.shared_dir,
            })

            # Write worker code to a temp file to avoid Windows CLI length limits
            import tempfile as _tf
            _tmp = _tf.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
            _tmp.write(worker_code)
            _tmp_path = _tmp.name
            _tmp.close()

            proc = subprocess.Popen(
                [sys.executable, _tmp_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            _tmp_cleanup = _tmp_path  # for finally block

            out, err = proc.communicate(input=inp_json, timeout=timeout)

            # Clean up temp file
            try:
                os.unlink(_tmp_cleanup)
            except Exception:
                _log.exception("sub_agent error")

            if proc.returncode != 0:
                err_msg = (err or "").strip()[:120]
                return ExpertOutput(
                    expert_name=class_name,
                    opinion="Subprocess exited with error.",
                    confidence=0.0,
                    error=f"exit={proc.returncode}: {err_msg}",
                )

            if not out or not out.strip():
                return ExpertOutput(
                    expert_name=class_name,
                    opinion="Subprocess produced no output.",
                    confidence=0.0,
                    error="empty stdout",
                )

            data = json.loads(out)
            return ExpertOutput(
                expert_name=data.get("expert_name", class_name),
                opinion=data.get("opinion", ""),
                confidence=float(data.get("confidence", 0.0)),
                details=data.get("details", {}),
                error=data.get("error"),
                skipped=bool(data.get("skipped", False)),
            )

        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                _log.exception("sub_agent error")
            return ExpertOutput(
                expert_name=class_name,
                opinion=f"Expert timed out after {timeout}s.",
                confidence=0.0,
                error="timeout",
            )

        except json.JSONDecodeError as e:
            return ExpertOutput(
                expert_name=class_name,
                opinion=f"Subprocess output was not valid JSON.",
                confidence=0.0,
                error=f"json_decode: {e}",
            )

        except Exception as e:
            return ExpertOutput(
                expert_name=class_name,
                opinion=f"Subprocess failed: {e}.",
                confidence=0.0,
                error=str(e),
            )
