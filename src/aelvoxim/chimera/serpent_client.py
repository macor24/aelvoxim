# SPDX-License-Identifier: MIT

"""
metacore.chimera.serpent_client — HTTP client for Serpent API.

MetaCore calls Serpent when an intent requires desktop manipulation.
This is a pure HTTP client — no code sharing with Serpent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("aelvoxim.chimera.serpent_client")


@dataclass
class SerpentResult:
    """Result from a Serpent execute call."""
    success: bool = False
    execution_id: str = ""
    mode: str = ""
    method: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)


class SerpentClient:
    """HTTP client for Chimera-Serpent API.

    Args:
        base_url: Serpent API base URL (e.g. http://127.0.0.1:8877).
        timeout_secs: Request timeout.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8877",
        timeout_secs: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_secs = timeout_secs

    async def execute(
        self,
        task: str,
        target_app: str = "",
        target_element: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> SerpentResult:
        """Execute a desktop operation via Serpent.

        Args:
            task: Task name (e.g. "search_in_chrome").
            target_app: Target application.
            target_element: Optional UI element.
            params: Additional parameters.

        Returns:
            SerpentResult with execution outcome.
        """
        import aiohttp

        payload = {
            "task": task,
            "target_app": target_app,
            "target_element": target_element,
            "params": params or {},
        }

        url = f"{self.base_url}/api/v1/serpent/execute"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout_secs),
                ) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        logger.info(
                            "Serpent execute: %s/%s → %s",
                            task, target_app, "OK" if data.get("success") else "FAIL",
                        )
                        return SerpentResult(
                            success=data.get("success", False),
                            execution_id=data.get("execution_id", ""),
                            mode=data.get("mode", ""),
                            method=data.get("method", ""),
                            error=data.get("error", ""),
                            raw=data,
                        )
                    else:
                        logger.warning("Serpent returned %d: %s", resp.status, data)
                        return SerpentResult(
                            error=f"Serpent HTTP {resp.status}",
                            raw=data,
                        )
        except Exception as e:
            logger.error("Serpent API call failed: %s", e)
            return SerpentResult(error=str(e))

    async def health(self) -> bool:
        """Check if Serpent API is reachable."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/health",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
