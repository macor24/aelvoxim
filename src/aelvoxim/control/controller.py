"""
metacore.control.controller — GenerationController (single-pass mode).

Calls the LLM once with the full prompt, then runs metacognition checks
on the complete response. If SEVERE issues detected, triggers retry.

Single-pass avoids memory-injection compounding that chunked mode causes
with pipelines that inject context on every call (like 9701's chat_pipeline).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from .metacog_check import evaluate as metacog_evaluate
from .retry_queue import RetryQueue

log = logging.getLogger("aelvoxim.control")

MAX_RETRIES = 3


class GenerationController:
    """Controller that wraps LLM generation with metacognition checks.

    Single-pass mode: one LLM call, then evaluate the full response.
    If the response has SEVERE issues, retry with correction prompt.
    """

    def __init__(self, max_retries: int = MAX_RETRIES, llm_check_enabled: bool = False):
        self.max_retries = max_retries
        self.llm_check_enabled = llm_check_enabled
        self.retry_queue = RetryQueue(max_attempts=max_retries)

    def generate(
        self,
        query: str,
        system_prompt: str,
        topic: str = "",
        call_llm: Callable[[str], str] = None,
        existing_text: str = "",
    ) -> Dict[str, Any]:
        """Generate a response with post-generation metacognition check.

        Args:
            query: User's question.
            system_prompt: System prompt (passed directly to LLM).
            topic: Topic string for drift detection.
            call_llm: Function that takes a prompt string and returns text.
            existing_text: If provided, skip initial LLM call and check this text
                           directly (avoids double LLM call when caller already has a result).

        Returns:
            Dict with keys: text, blocked, retries, chunks, issues.
        """
        if call_llm is None:
            return {"text": "", "blocked": True, "error": "no call_llm provided"}

        self.retry_queue.reset()
        llm_check_fn = self._make_llm_check_fn(call_llm)

        # Use existing text if provided, otherwise generate
        if existing_text and existing_text.strip():
            text = existing_text
        else:
            # Build the full prompt
            full_prompt = system_prompt
            # Generate full response (single call)
            text = call_llm(full_prompt)
        if not text or not text.strip():
            return {"text": "", "blocked": False, "chunks": 0, "issues": 0, "retries": 0}

        # Metacognition check on the complete response
        severity, issues = metacog_evaluate(
            chunk=text,
            accumulated="",
            topic=topic,
            llm_check_enabled=self.llm_check_enabled,
            call_llm_fn=llm_check_fn,
        )

        if severity == "SEVERE":
            log.info("SEVERE issues detected: %s", [i["type"] for i in issues])
            entry = self.retry_queue.push(text, issues)

            # Try to correct
            for attempt in range(self.max_retries):
                corr_prompt = self.retry_queue.correction_prompt(entry)
                fixed = call_llm(corr_prompt)
                if not fixed or not fixed.strip():
                    self.retry_queue.increment(entry.id)
                    continue

                sev2, _ = metacog_evaluate(
                    chunk=fixed,
                    accumulated="",
                    topic=topic,
                    llm_check_enabled=self.llm_check_enabled,
                    call_llm_fn=llm_check_fn,
                )
                if sev2 != "SEVERE":
                    text = fixed
                    self.retry_queue.resolve(entry.id)
                    log.info("Corrected after %d retries", attempt + 1)
                    break
                self.retry_queue.increment(entry.id)
            else:
                self.retry_queue.fail(entry.id)
                log.warning("Failed to correct after %d retries", self.max_retries)

        # Build supplement for MINOR issues
        supplement = self.retry_queue.build_supplement()
        if supplement:
            text += "\n\n---\n" + supplement

        return {
            "text": text,
            "blocked": False,
            "chunks": 1,
            "issues": len(issues),
            "retries": len([e for e in self.retry_queue.entries if not e.is_minor]),
        }

    def _make_llm_check_fn(self, call_llm: Callable[[str], str]) -> Callable[[str], str]:
        def _check(prompt: str) -> str:
            try:
                return call_llm(prompt)
            except Exception as e:
                log.warning("LLM check call failed: %s", e)
                return ""
        return _check
