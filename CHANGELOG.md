# Changelog

## v1.2.0 (2026-07-23)

### Added
- 5-phase meta-cognition improvement:
  - Failure threshold: track consecutive validation failures, pause direction + emit
    reflection log after threshold (adaptive: simple=2, medium=3, complex=5)
  - Saturation v3: weighted formula verify_pass_rate(0.50) + task_rate(0.30) + difficulty(0.20)
  - Strategy pre-check: query SelfModel engine history before switch; cache result
  - Execution guard: prevent re-execution of completed tasks
  - Meta-reviewer daemon (`meta_reviewer.py`): scans metacog logs every 10 ticks,
    detects low scores/stagnation/repair failures, writes to dedicated meta_review.log
- Failure categorization: `fail_by_reason` JSON dict (timeout/quality/search_empty/validation)
- Cost-benefit termination: compare cycles invested vs estimated remaining before aborting
- Topic normalization: auto-merge fullwidth/halfwidth punctuation in knowledge topics
- MetaCogTrigger persistence: metacog history saved to `metacog_history.jsonl`, loaded on restart
- Learner loop now gracefully degrades when PostgreSQL is unavailable (JSON file fallback)
- `CORS`: default `allow_origins=*` for easier cross-origin deployment
- PR/issue templates (.github/): PR template, bug report, feature request
- CI badge: Ruff lint, pytest (3.10/3.11/3.12), CodeQL, Dependabot

### Changed
- KnowledgeBase `store()` now auto-normalizes topic punctuation
- Saturation evaluation: from pure entry-ratio heuristic to verification-quality-weighted formula
- CORS: default origins changed to `*` (configurable via `AELVOXIM_CORS_ORIGINS`)
- Documentation: README now includes Security table, CI table, Contributing quick rules

### Fixed
- Learner loop stuck on PG unavailable — now falls back to JSON storage instead of blocking
- `sentrikit.py`: missing `import logging` (NameError at module level)
- `routes_chat.py`: SSE stream errors now logged via `_log.exception()` instead of swallowed
- Webhook subscribe endpoint no longer leaks `result["error"]` to client
- CORS preflight for `POST /v1/llm/chat/stream` — now returns proper 200 OPTIONS

## v1.0.0 (2026-06-06)

### Added
- L4 procedural memory layer (verified knowledge, never decays)
- MetaCogMonitor unified meta-cognition monitor (overload + rate limit + circuit breaker)
- Ethics L1~L6 full protection (gates independently toggleable)
- Session snapshot manager (auto-save + restore across conversations)
- ProactiveReasoner (rule-based topic similarity matching)
- Pending task reminders (gated by user preference)
- Emotion profile tracking (Bayesian positive/negative/neutral)
- Empathy mode (continuous negative sentiment → system prompt switch)
- Self-iteration engine (simulated task generation from knowledge gaps)
- User feedback requests (low-confidence knowledge → user confirmation)
- Proactive dialog scheduler (time-driven + user permission dynamic adjustment)
- Full-chain audit log (all ethics events + memory writes)
- Ethics gate update API (POST /v1/ethics/update + GET /v1/ethics/gates)
- Knowledge Graph zoom/pan (mouse wheel + drag)
- Cross-platform file lock (fcntl + msvcrt fallback)
- 39 pytest tests covering memory, entity extraction, scorer
- English-only codebase (global developer route)
- Dashboard i18n (en/zh toggle)

### Changed
- All `except Exception: pass` → at minimum error log output (~73 locations)
- Pending knowledge approval threshold: 0.3 → 0.6
- AI name preference now injected into `_identity_prefix`
- system prompt: "Always reply in the same language as the user's message"

### Fixed
- `_forget_cmd` duplicate regex line removed
- `_detect_sentiment` missing `开心`, `谢谢` keywords
- `detect_confirmation` negative prefix ("不对") matched "对"
- `msvcrt.locking` non-blocking → blocking lock for Windows compatibility
- Learner pending spiral: triple anti-spiral (L1 max 20 attempts, L2 stale result, L3 streak)
| - `l.is_running` → `l.is_running()` (method reference bug)

## v1.1.0 (2026-06-11)

### Added
- **Post-validation audit** — `post_validation.py`: FactCrossVerifier, ConsistencyChecker,
  SafetyComplianceFilter. Re-scans stored knowledge for contradictions, PII leaks, and
  dangerous patterns. Triggered by low confidence (<0.7), high-risk topics, or age (>7d).
- **Meta-learning** — `meta_learner.py`: Extracts user correction signals from conversation,
  creates knowledge entries and negative memory anchors from feedback. Learns from repeated
  questions by adding new learning directions.
- **Expert plugin registry** — `experts/__init__.py`: `register_expert()` / `discover_experts()`
  / `@register` decorator. New experts auto-discover without orchestrator changes.
- **Task-based expert routing** — `experts/router.py`: TaskClassifier + RouteSelector selects
  2-4 relevant experts per query instead of all 6. Supports code/analysis/chat/security/
  creative/planning task types.
- **Sub-process expert isolation** — `experts/sub_agent.py`: SubAgentManager runs each expert
  in an isolated subprocess with timeout protection. Single expert crash does not affect others.
- **Cross-expert shared context** — Experts can see each other's conclusions via a shared temp
  directory. Safety/ethics block signals cause other experts to skip automatically.
- **Memory fusion with inverted index** — `memory/fusion.py`: Token-based inverted index search
  with layer priority (procedural > semantic > episodic > working). Configurable via calibration.
- **Feedback extraction** — `chat_monitor.py:extract_feedback_signals()`: Rule-based detection
  of user corrections ("不是A而是B") and repeat questions.
- **All docstrings in English** — meta_learner.py, fusion.py, chat_monitor.py (append),
  calibration.py (25 Chinese comments translated to English).

### Changed
- `routes.py` split: 1552 lines → 6 files (routes_chat/memory/config/task/system.py) + service_chat.py
  (1096 lines business logic). Total 1711 lines, all route files <170 lines each.
- `experts/orchestrator.py`: Dynamic expert discovery from registry, route-based selection,
  sub-process execution with timeout, auto-fallback to serial in-process.
- `experts/__init__.py`: Now auto-imports all expert modules for `@register` decorator to fire.
- `calibration.py`: `fusion` and `meta_learn` config sections added. All Chinese comments
  translated to English.
- `learner.py`: Post-validation audit and meta-learning tick added to idle loop.
- `server/__init__.py`: Sub-routers merged into main router.

### Fixed
- `experts/logic.py`, `ethics.py`, `emotion.py`: Rewritten due to docstring patching corruption
  (identical logic, fixed structure).
- `experts/router.py`: Chinese keyword ordering for TaskClassifier — code/security/planning
  routes now checked before creative/chat to prevent "write python code" being classified as chat.

## v0.3.0 (2026-06-05)

### Added
- Cross-session memory with entity extraction (person, location, preference)
- 3-layer memory architecture (working/episodic/semantic)
- Adaptive memory scoring (confidence based on signal type × repetition × emotion)
- Time-to-live (TTL) tags with auto-expiry ("记住这个信息保留一周")
- Emotion keyword extraction (3 levels: high/medium/low)
- User confirmation mechanism (`[PendingConfirmation]` injection)
- Single-forget command ("忘了XXX") and clear-all ("清理我的记忆")
- Conflict detection (superseded values on re-insert)
- Knowledge gap analysis (KB coverage vs. user queries)
- Query topic prediction (co-occurrence analysis)
- Knowledge graph visualization (Dashboard ECharts)
- Health daemon (30-minute cleanup + re-score + decay cycle)
- SentriKit HTTP bridge (security audit integration)
- System time awareness (injected into LLM context)
- Copy & forward buttons on chat messages
- Health check API (`GET /v1/health`)
- Log viewer API (`GET /v1/logs`)
- One-click startup script (`start.sh`)
- Version file (`__version__.py`)
- README and CHANGELOG
- All code/docs in English (global developer route)
- Privacy-safe system prompt (no algorithm details exposed)

### Changed
- Memory storage from single JSON → SQLite with multi-user isolation
- LLM identity prefix → carries user name + location for cross-session recall
- Learner direction management → 30 directions max, skip self-heal when full
- Person entity type preserved on re-insert (org→location upgrade supported)

### Fixed
- SQLite not written in `store_entity` existing-entity path
- `_identity_prefix` ValueError from missing `type` column in SELECT
- Location regex excluding "住" causing extraction failure
- Organization regex stealing "在" prefix from location patterns
- Loop error from `pending.json` list/dict format mismatch
- `_execution_has_value` using wrong `call_fn` signature
- `_check_pending_promotions` type guard for malformed entries
- `task_queue`/`completed_tasks` type normalization in `_load_config`
