"""aelvoxim.learn.validator — Knowledge auto-verifier

Bypass design: stacks on top of existing validate() without affecting it.
AutoValidator appends verification scores as a transparent pass-through.

|L1 Search verification — cross-source search validation (3 engines, 2/3 pass)
|L2 LLM Debate — pro/con debate via LLM, outputs credibility score
|L3 AggregateScore — combines verification rounds into validated_count

Usage:
  validator = AutoValidator()
  result = validator.verify(entry)
  # -> {"verified": True/False, "search_score": 0.8, "debate_score": 0.7, "combined": 0.75}

Integration:
  KnowledgeBase.store() triggers async verification automatically
  KnowledgeBase.validate() layers auto-validation scores
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── L1: Search Verifier ──────────────────────────────


class SearchVerifier:
    """Search verifier: cross-source search validation.

    Searches across 3 engines (media + bing_cn + duckduckgo), 2/3 pass required.

    Source credibility weights:
    - Whitelisted media (MEDIA_WHITELIST) match -> 1.0 (full score)
    - Regular site match -> 0.6 (discounted)
    - Personal blog/forum/UGC -> 0.3

    Voting:
    - At least 2 engines return valid results with weighted score >=0.5 -> pass
    - Only 1 engine matches -> weak pass (lower contribution)
    - 0 engines match -> fail

    Degradation: when search is unavailable returns 0.5 neutral score.
    """

    # Low-credibility source domain patterns (personal blogs, forums, UGC platforms)
    _LOW_CREDIBILITY_DOMAINS = [
        "blogspot.com", "wordpress.com", "github.io",
        "medium.com", "dev.to", "hashnode.dev",
        "stackoverflow.com", "stackexchange.com",
        "reddit.com", "quora.com", "tumblr.com",
        "weebly.com", "wixsite.com", "jimdofree.com",
    ]

    def __init__(self):
        self._search_fn = None
        self._cross_engines = ["media", "bing_cn", "duckduckgo"]
        # Lazily load whitelist
        self._whitelist = None

    def _lazy_init_search(self):
        if self._search_fn is None:
            from aelvoxim.learn.search import search
            self._search_fn = search

    def _lazy_init_whitelist(self):
        if self._whitelist is None:
            try:
                from aelvoxim.learn.search import MEDIA_WHITELIST
                self._whitelist = set(MEDIA_WHITELIST)
            except ImportError:
                self._whitelist = set()

    @staticmethod
    def _get_domain_credibility(url: str) -> float:
        """Determine source credibility weight from URL.

        Returns:
            1.0  - Whitelisted media
            0.6  - Regular website
            0.3  - Personal blog/forum/UGC
        """
        from urllib.parse import urlparse
        try:
            domain = urlparse(url).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
        except Exception:
            return 0.6

        # Whitelisted media (lazy import to avoid circular dependencies)
        try:
            from aelvoxim.learn.search import MEDIA_WHITELIST
            for wl in MEDIA_WHITELIST:
                if domain == wl or domain.endswith("." + wl):
                    return 1.0
        except (ImportError, AttributeError):
            pass  # non-critical, continue

        # Low-credibility sources
        for low in SearchVerifier._LOW_CREDIBILITY_DOMAINS:
            if low in domain:
                return 0.3

        return 0.6

    # ── Search Query Optimization ──────────────────────────

    _QUERY_NOISE_WORDS = [
        "是什么", "什么是", "如何", "怎么", "怎样", "为何",
        "简介", "介绍", "概述", "详解", "教程", "指南",
        "实例", "案例", "Example", "实战",
        "总结", "心得", "笔记", "备忘",
        "最佳实践", "经验分享",
        "了解", "学习", "入门",
    ]

    _EN_ENTITY_RE = re.compile(r"[A-Z][A-Za-z0-9+#./-]{1,}(?:\s*[0-9]+[A-Za-z]*)?")

    @staticmethod
    def _extract_english_entities(text: str) -> list:
        """Extract meaningful English entities (capitalized, technical terms)."""
        found = SearchVerifier._EN_ENTITY_RE.findall(text)
        return [e.strip() for e in found if len(e.strip()) >= 2 and not e.strip().isdigit()]

    @staticmethod
    def _extract_chinese_keywords(text: str) -> list:
        """Extract meaningful Chinese keywords (removing query intent words)."""
        chunks = re.findall(r"[\u4e00-\u9fff]{2,30}", text)
        result = []
        for chunk in chunks:
            for noise in SearchVerifier._QUERY_NOISE_WORDS:
                chunk = chunk.replace(noise, "")
            chunk = chunk.strip()
            if len(chunk) >= 2:
                result.append(chunk)
        return result

    @staticmethod
    def _optimize_search_query(title: str, topic: str = "", content: str = "") -> str:
        """Build search query from title/topic.

        1. Extract English proper entities (technical terms preferred)
        2. Extract Chinese keywords (removing query intent words)
        3. Dedup and merge

        Returns: optimized query string, empty if no valid content.
        """
        src = f"{title} {topic}"
        entities = SearchVerifier._extract_english_entities(src)
        chinese_kws = SearchVerifier._extract_chinese_keywords(src)

        parts = []
        if entities:
            parts.extend(entities)
        seen_ch = set()
        for kw in chinese_kws:
            nk = kw.replace(" ", "")
            if nk not in seen_ch:
                seen_ch.add(nk)
                parts.append(kw)

        unique = []
        seen = set()
        for p in parts:
            pl = p.lower()
            if pl not in seen:
                seen.add(pl)
                unique.append(p)

        query = " ".join(unique)
        if len(query) > 80:
            query = query[:80].rsplit(" ", 1)[0]

        if len(query) < 3:
            return ""
        return query

    def verify(self, entry: Dict) -> Dict:
        self._lazy_init_search()
        self._lazy_init_whitelist()

        title = entry.get("title", "")
        summary = entry.get("summary", "")
        content = entry.get("content", "")
        topic = entry.get("topic", "")

        # Use optimized search query, fall back to original title
        query = self._optimize_search_query(title, topic, content)
        if not query:
            query = title[:100]
        if len(query) < 10 and summary:
            query = summary[:100]

        if not query:
            return {"score": 0.5, "detail": "No searchable content", "results": 0, "engines": 0}

        # Multi-source cross-validation
        engine_results = []
        for engine in self._cross_engines:
            try:
                results = self._search_fn(query, max_results=3, engine=engine)
                engine_results.append({"engine": engine, "results": results or []})
            except Exception:
                engine_results.append({"engine": engine, "results": [], "error": "Search failure"})

        combined_text = f"{title} {summary} {content}".lower()
        keywords = [w for w in combined_text.split() if len(w) > 1]
        if not keywords:
            return {"score": 0.5, "detail": "No valid keywords", "results": 0, "engines": 0}

        engine_scores = []
        engine_details = []
        for er in engine_results:
            results = er["results"]
            if not results:
                engine_scores.append(0.0)
                engine_details.append({"engine": er["engine"], "score": 0, "detail": "No results"})
                continue

            weighted_total = 0.0
            weighted_count = 0
            for r in results:
                result_text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
                matched = sum(1 for kw in keywords if kw in result_text)
                ratio = matched / max(len(keywords), 1)
                if ratio > 0.1:
                    # Weight by source credibility
                    credibility = self._get_domain_credibility(r.get("url", ""))
                    weighted_total += min(ratio * 1.5, 1.0) * credibility
                    weighted_count += 1

            if weighted_count == 0:
                engine_scores.append(0.0)
                engine_details.append({"engine": er["engine"], "score": 0, "detail": "No matches"})
            else:
                # Weighted average: base score × mean credibility
                base_score = min(weighted_total / weighted_count + 0.1 * (weighted_count / min(len(results), 5)), 1.0)
                avg_credibility = weighted_total / max(weighted_count, 1)
                score = round(base_score * min(avg_credibility + 0.3, 1.0), 2)
                engine_scores.append(score)
                engine_details.append({
                    "engine": er["engine"],
                    "score": score,
                    "matches": weighted_count,
                    "detail": f"{weighted_count}/{len(results)} matches (credibility-weighted)",
                })

        # 2/3 majority vote
        passed = sum(1 for s in engine_scores if s >= 0.5)
        if passed >= 2:
            avg_score = sum(s for s in engine_scores if s >= 0.5) / max(passed, 1)
            boost = min(0.15, passed * 0.05)
            final_score = min(avg_score + boost, 1.0)
            detail = f"Cross-verify passed: {passed}/{len(engine_scores)} engines consistent"
        elif passed == 1:
            final_score = max(s for s in engine_scores) * 0.6
            detail = f"Cross-verify weak pass: only 1/{len(engine_scores)} engine matched"
        else:
            best = max(engine_scores) if engine_scores else 0
            if best == 0:
                # All engines returned 0 — search unavailable or no results
                # Return neutral score instead of failing
                final_score = 0.5
                detail = "Search unavailable, neutral score"
            else:
                final_score = best * 0.3
                detail = f"Cross-verify failed: 0/{len(engine_scores)} engines matched"

        return {
            "score": round(final_score, 2),
            "detail": detail,
            "results": sum(len(er["results"]) for er in engine_results),
            "engines": len(engine_results),
            "engine_details": engine_details,
        }


# ── L2: LLM Debate Verifier ──────────────────────────


class DebateVerifier:
    """LLM Debate Verifier: uses LLM to debate from pro and con perspectives and score.

    Principle:
    - For pending knowledge, constructs a debate prompt
    - LLM outputs pro/con arguments + credibility score
    - High score (>= 0.7) -> contributes to validated_count progress
    - Low score (< 0.3) -> flagged "suggest manual review"
    - Returns neutral score when no LLM available

    Degradation chain: DeepSeek -> Ollama -> Rule-based scoring (no LLM)
    """

    def __init__(self):
        self._models = None

    def _lazy_init(self):
        """Lazy load LLM config"""
        if self._models is None:
            from aelvoxim.learn.llm import default_models
            self._models = default_models()

    def verify(self, entry: Dict) -> Dict:
        """Run LLM debate verification on a knowledge entry.

        Returns:
            score: 0.0 ~ 1.0 debate credibility score
            detail: debate details
        """
        self._lazy_init()

        title = entry.get("title", "")
        content = entry.get("content", "") or entry.get("summary", "")

        if not content:
            return {"score": 0.5, "detail": "No content to debate", "confidence": 0.0}

        # Has available LLM -> call debate
        available = [m for m in self._models if m.is_available()] if self._models else []
        if available:
            return self._llm_debate(available[0], title, content)

        # No LLM -> rule-based fallback
        return self._rule_fallback(title, content)

    def _llm_debate(self, model, title: str, content: str) -> Dict:
        """Call LLM for pro/con debate."""
        from aelvoxim.learn.llm import call_llm

        system_prompt = """You are a knowledge review expert. Strictly evaluate the following knowledge claim.

Analyze from three aspects:
1. Supporting arguments (at least 2): reasons the claim is valid
2. Opposing arguments (at least 2): issues or limitations with the claim
3. Comprehensive credibility score (0.0-1.0): based on argument quality, verifiability, universality

Output strict JSON format, no extra text:
{"supporting": ["argument1", "argument2"], "opposing": ["concern1", "concern2"], "score": 0.0, "reasoning": "review summary"}"""

        user_prompt = f"Knowledge title: {title}\n\nKnowledge content: {content[:1500]}"

        try:
            response = call_llm(model, system_prompt, user_prompt,
                               temperature=0.3, max_tokens=1024, timeout=15)
            result = self._parse_debate_response(response)
            result["model"] = model.name
            return result
        except Exception:
            return self._rule_fallback(title, content)

    @staticmethod
    def _parse_debate_response(text: str) -> Dict:
        """Parse JSON from the LLM debate response."""
        # Extract JSON
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return {"score": 0.5, "detail": "Failed to parse debate response",
                            "confidence": 0.3, "supporting": [], "opposing": []}
            else:
                return {"score": 0.5, "detail": "Debate response had no JSON",
                        "confidence": 0.3, "supporting": [], "opposing": []}

        score = float(data.get("score", 0.5))
        score = max(0.0, min(1.0, score))
        supporting = data.get("supporting", [])
        opposing = data.get("opposing", [])

        # Confidence = number/quality of arguments + score
        confidence = min(1.0, (len(supporting) + len(opposing)) * 0.1 + score * 0.5)

        return {
            "score": round(score, 2),
            "confidence": round(confidence, 2),
            "detail": f"LLM Debate: {len(supporting)} pro / {len(opposing)} con",
            "supporting": supporting,
            "opposing": opposing,
        }

    @staticmethod
    def _rule_fallback(title: str, content: str) -> Dict:
        """Rule-based scoring fallback when no LLM is available.

        Simple credibility assessment based on text features:
        - Too short content -> low score
        - Contains absolute words ("always", "never", "absolutely") -> penalty
        - Contains source citations -> bonus
        """
        score = 0.5
        reasons = []

        # Content length
        if len(content) < 50:
            score -= 0.2
            reasons.append("Content too short")

        # Absolute words
        absolutes = ["永远", "绝对", "一定", "所有", "全部", "从不"]
        abs_count = sum(1 for a in absolutes if a in content)
        if abs_count > 0:
            score -= 0.1 * abs_count
            reasons.append(f"Contains {abs_count} absolute word(s)")

        # Source citations
        citations = ["根据", "研究表明", "数据显示", "据调查", "参考", "来源"]
        if any(c in content for c in citations):
            score += 0.2
            reasons.append("Has source citations")

        # Length bonus
        if len(content) > 500:
            score += 0.1
            reasons.append("Detailed content")

        score = max(0.1, min(1.0, score))
        return {
            "score": round(score, 2),
            "confidence": 0.3,
            "detail": "Rule score: " + "; ".join(reasons) if reasons else "No LLM, neutral rule-based score",
            "supporting": [],
            "opposing": reasons,
        }


# ── L4: Falsification Search Verifier ──────────────────────────


class FalsificationVerifier:
    """Falsification verifier: actively searches for refutations/limitations/controversies of knowledge.

    Positive verification (L1) only finds "supporting evidence", but false knowledge is often
    propagated by marketing accounts copying each other. Falsification verification searches for
    "X's disadvantages/limitations/controversies/refutations" and checks whether authoritative
    sources raise doubts.

    Falsification penalty rules:
    - >=2 authoritative sources (weight 1.0) match with score >=0.5: -0.4 (real controversy/misreading)
    - >=1 authoritative source match: -0.2 (mild doubt)
    - >=2 regular sources match: -0.1 (scattered doubts)
    - No match: 0 (no searchable falsification -> pass)
    - Search unavailable: 0 (skip, doesn't affect main flow)
    """

    # Falsification search suffixes matched by knowledge type
    _FALSIFICATION_SUFFIXES = [
        "缺点 局限性 不适用",
        "争议 反驳 反对",
        "问题 Error 误导",
    ]

    def __init__(self):
        self._search_fn = None
        self._search_verifier = None

    def _lazy_init(self):
        if self._search_fn is None:
            from aelvoxim.learn.search import search
            self._search_fn = search
        if self._search_verifier is None:
            self._search_verifier = SearchVerifier()

    def verify(self, entry: Dict, optimized_query: str = "") -> Dict:
        """Run falsification search verification.

        Args:
            entry: knowledge entry
            optimized_query: pre-optimized search query (reuses P0 results)

        Returns:
            {"detected": bool, "penalty": float, "detail": str, "matches": [...]}
        """
        self._lazy_init()

        title = entry.get("title", "")
        content = entry.get("content", "") or entry.get("summary", "")

        # Build falsification search query
        if not optimized_query:
            optimized_query = title[:60]

        # Skip short titles / no content
        if len(optimized_query) < 5 or len(content) < 30:
            return {"detected": False, "penalty": 0.0,
                    "detail": "Content too short, skipping falsification", "matches": []}

        # Use the shortest falsification suffix to avoid search engine truncation
        suffix = self._FALSIFICATION_SUFFIXES[0]  # "缺点 局限性 不适用"
        falsification_query = f"{optimized_query[:50]} {suffix}"

        # Search
        try:
            results = self._search_fn(falsification_query, max_results=5,
                                      engine="media")  # Prefer authoritative media
        except Exception:
            return {"detected": False, "penalty": 0.0,
                    "detail": "Search unavailable, skipping", "matches": []}

        if not results:
            return {"detected": False, "penalty": 0.0,
                    "detail": "No falsification search results", "matches": []}

        # Evaluate falsification results
        matched = []
        keyword_set = set(w.lower() for w in content.split() if len(w) > 2)

        for r in results:
            result_text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
            # Search result should contain falsification/criticism keywords
            has_falsification = any(kw in result_text
                                     for kw in ["缺点", "问题", "局限", "不足",
                                                 "不适用", "争议", "风险",
                                                 "Error", "误导", "反对"])

            # Keyword relevance between falsification content and claim
            match_count = sum(1 for kw in keyword_set if kw in result_text)
            relevancy = match_count / max(len(keyword_set), 1)

            if has_falsification and relevancy > 0.05:
                credibility = SearchVerifier._get_domain_credibility(
                    r.get("url", ""))
                matched.append({
                    "title": r.get("title", "")[:80],
                    "url": r.get("url", "")[:80],
                    "credibility": credibility,
                    "relevancy": round(relevancy, 3),
                    "score": round(min(relevancy * 2, 1.0) * credibility, 2),
                })

        if not matched:
            return {"detected": False, "penalty": 0.0,
                    "detail": "No relevant falsification content", "matches": []}

        # Credibility-weighted falsification score
        authority_hits = sum(1 for m in matched if m["credibility"] >= 1.0)
        normal_hits = sum(1 for m in matched if m["credibility"] >= 0.6)
        # Average authority source match score
        authority_scores = [m["score"] for m in matched if m["credibility"] >= 1.0]
        avg_authority_score = sum(authority_scores) / max(len(authority_scores), 1)

        penalty = 0.0
        detail_parts = []

        if authority_hits >= 2 and avg_authority_score >= 0.5:
            penalty = -0.4
            detail_parts.append(f"{authority_hits} authoritative source(s) flagged this issue")
        elif authority_hits >= 1 and avg_authority_score >= 0.5:
            penalty = -0.2
            detail_parts.append(f"{authority_hits} authoritative source(s) raised concerns")
        elif normal_hits >= 2:
            penalty = -0.1
            detail_parts.append(f"{len(matched)} source(s) with scattered doubts")
        else:
            penalty = 0.0
            detail_parts.append("Falsification relevancy insufficient")

        detected = penalty < -0.05
        return {
            "detected": detected,
            "penalty": round(penalty, 2),
            "detail": "; ".join(detail_parts) if detail_parts else "No relevant falsification",
            "matches": matched[:5],
        }


# ── L3: Aggregate Validator ──────────────────────────────


class AutoValidator:
    """Automatic knowledge validator (three-stage bypass pipeline).

    Bypass design:
    - Does not affect existing validate() x3 counting flow
    - Automatically adds search + debate scores on each verify call
    - Search verification passed -> contributes 0.3 points
    - LLM debate high score (>= 0.7) -> contributes 0.5 points
    - Accumulated score reaches 3.0 -> auto-promotion

    Score breakdown:
      human validate() = 1 point
      search verification passed = 0.3 points (auto)
      LLM debate high score = 0.2~0.5 points (auto, depends on confidence)
    """

    # Contribution per auto-verification round
    _SEARCH_VERIFY_CONTRIBUTION = 0.3  # Search verification passed
    _DEBATE_HIGH_CONTRIBUTION = 0.5    # LLM debate high score (>= 0.7)
    _DEBATE_MED_CONTRIBUTION = 0.2     # LLM debate medium score (0.4-0.7)
    _DEBATE_LOW_CONTRIBUTION = -0.1    # LLM debate low score (< 0.4), slight penalty

    # Time decay: half-life 2 years (730 days)
    _TIME_DECAY_HALF_LIFE_DAYS = 730

    @staticmethod
    def _time_decay(knowledge_date: str) -> float:
        """Compute time decay coefficient.

        Half-life 2 years:
        - Within 1 year -> ~1.0 (no decay)
        - 2 years      -> ~0.5
        - 3 years      -> ~0.25
        - 5 years      -> ~0.06
        - No date info -> 0.8 (neutral, slight trust)
        - Future date  -> 1.0 (no decay)
        """
        if not knowledge_date:
            return 0.8
        try:
            kd = datetime.strptime(knowledge_date[:10], "%Y-%m-%d")
            delta = (datetime.now() - kd).days
            if delta < 0:
                return 1.0  # Future date
            return 2 ** (-delta / AutoValidator._TIME_DECAY_HALF_LIFE_DAYS)
        except (ValueError, TypeError):
            return 0.8  # Date format could not be parsed

    def __init__(self):
        self._search_verifier = SearchVerifier()
        self._debate_verifier = DebateVerifier()
        self._falsification_verifier = FalsificationVerifier()
        # Verification log
        from ..utils import DATA_DIR

        self._log_path = DATA_DIR / "validator" / "log.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def verify(self, entry: Dict) -> Dict:
        """Run full four-level auto-verification + adversarial sample detection.

        Returns verification result and contribution score;
        caller (KnowledgeBase.validate) adds contribution to validated_count.
        """
        start = time.time()

        # Extract topic from entry (needed for P0 search optimization)
        topic = entry.get("topic", "")
        title = entry.get("title", "")

        # Time decay
        knowledge_date = entry.get("_knowledge_date", "") or entry.get("knowledge_date", "")
        time_factor = self._time_decay(knowledge_date)

        # L1: Search verification
        search_result = self._search_verifier.verify(entry)
        search_score = search_result.get("score", 0.5)
        optimized_query = search_result.get("_query_used", "")

        # L2: LLM Debate verification
        debate_result = self._debate_verifier.verify(entry)
        debate_score = debate_result.get("score", 0.5)
        debate_confidence = debate_result.get("confidence", 0.3)

        # L4: Falsification search verification (before adversarial detection)
        falsification_result = self._falsification_verifier.verify(
            entry, optimized_query=optimized_query)
        falsification_penalty = falsification_result.get("penalty", 0.0)

        # Adversarial sample detection: find semantically similar but oppositely concluded active knowledge
        adversarial = self._check_adversarial(entry)

        # If adversarial sample detected, reduce search score
        if adversarial.get("detected", False):
            search_score = max(search_score * 0.5, 0.1)
            adversarial["action"] = "Search score halved"

        # Time weighting: outdated knowledge search score decays (does not affect LLM Debate score, basic concepts don't age)
        time_adjusted_search = search_score * time_factor

        # Combined score = time-weighted search * 0.35 + Debate * 0.55 + falsification penalty
        combined_score = (time_adjusted_search * 0.35 + debate_score * 0.55
                          + falsification_penalty * 0.4)
        combined_score = max(0.0, min(1.0, combined_score))

        # Calculate contribution (falsification penalty also affects)
        contribution = self._calc_contribution(
            search_score, debate_score, debate_confidence, falsification_penalty)

        result = {
            "verified": combined_score >= 0.5,
            "combined_score": round(combined_score, 2),
            "time_factor": round(time_factor, 2),
            "search": search_result,
            "debate": debate_result,
            "falsification": falsification_result,
            "adversarial": adversarial,
            "contribution": round(contribution, 2),
            "duration_ms": round((time.time() - start) * 1000),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Write log
        self._log(entry.get("id", ""), entry.get("title", ""), result)

        return result

    def _check_adversarial(self, entry: Dict) -> Dict:
        """Adversarial sample detection: find semantically similar but potentially contradictory knowledge in the knowledge base.

        Detection method:
        1. Use title text similarity matching (SequenceMatcher)
        2. For entries with similarity >0.6, compare keyword sentiment of their content
        3. If texts are highly similar but keyword distribution differs significantly -> flag as adversarial

        Returns:
            {"detected": True/False, "matches": [...], "detail": "..."}
        """
        from difflib import SequenceMatcher
        from aelvoxim.learn.knowledge import KnowledgeBase

        title = entry.get("title", "").lower()
        content = entry.get("content", "") or entry.get("summary", "")
        content_lower = content.lower()

        if not title or len(title) < 5:
            return {"detected": False, "matches": [], "detail": "Title too short, skipping"}

        # Sentiment analysis keywords
        positive_kw = ["优势", "好处", "有效", "提高", "增长", "Success", "正确", "推荐", "支持", "促进"]
        negative_kw = ["风险", "问题", "缺陷", "限制", "不足", "Failure", "Error", "反对", "危害", "降低"]

        my_pos = sum(1 for kw in positive_kw if kw in content_lower)
        my_neg = sum(1 for kw in negative_kw if kw in content_lower)
        my_sentiment = "positive" if my_pos > my_neg else ("negative" if my_neg > my_pos else "neutral")

        all_active = KnowledgeBase.get_all_active()
        matches = []
        for existing in all_active:
            if existing.get("id") == entry.get("id"):
                continue

            existing_title = existing.get("title", "").lower()
            sim = SequenceMatcher(None, title, existing_title).ratio()
            if sim < 0.6:
                continue

            # Check sentiment
            existing_content = f"{existing.get('content', '')} {existing.get('summary', '')}".lower()
            ex_pos = sum(1 for kw in positive_kw if kw in existing_content)
            ex_neg = sum(1 for kw in negative_kw if kw in existing_content)
            ex_sentiment = "positive" if ex_pos > ex_neg else ("negative" if ex_neg > ex_pos else "neutral")

            opposite = (
                (my_sentiment == "positive" and ex_sentiment == "negative")
                or (my_sentiment == "negative" and ex_sentiment == "positive")
            )

            matches.append({
                "entry_id": existing.get("id", ""),
                "title": existing.get("title", "")[:60],
                "similarity": round(sim, 2),
                "my_sentiment": my_sentiment,
                "existing_sentiment": ex_sentiment,
                "opposite": opposite,
            })

        if not matches:
            return {"detected": False, "matches": [], "detail": "No similar entries found"}

        # At least one clearly opposite entry -> flag as adversarial
        opposite_matches = [m for m in matches if m["opposite"]]
        if opposite_matches:
            return {
                "detected": True,
                "matches": opposite_matches,
                "detail": f"Found {len(opposite_matches)} entry(ies) with opposite conclusions",
            }

        return {
            "detected": False,
            "matches": matches[:3],
            "detail": f"Found {len(matches)} similar entry(ies) with consistent sentiment",
        }

    def _calc_contribution(self, search_score: float,
                           debate_score: float,
                           debate_confidence: float,
                           falsification_penalty: float = 0.0) -> float:
        """Calculate auto-verification contribution to validated_count."""
        contribution = 0.0

        # Search verification
        if search_score >= 0.6:
            contribution += self._SEARCH_VERIFY_CONTRIBUTION
        elif search_score >= 0.3:
            contribution += self._SEARCH_VERIFY_CONTRIBUTION * 0.5

        # LLM Debate
        if debate_score >= 0.7 and debate_confidence >= 0.5:
            contribution += self._DEBATE_HIGH_CONTRIBUTION
        elif debate_score >= 0.4:
            contribution += self._DEBATE_MED_CONTRIBUTION * debate_confidence
        else:
            contribution += self._DEBATE_LOW_CONTRIBUTION  # Low score may deduct

        # Falsification penalty (stackable, even search-verified entries can be penalized)
        if falsification_penalty < -0.05:
            contribution += falsification_penalty * 0.5  # Half falsification penalty applies to contribution

        return contribution

    def _log(self, entry_id: str, title: str, result: Dict):
        """Write verification log (JSONL format, auditable)."""
        try:
            log_entry = {
                "entry_id": entry_id,
                "title": title[:80],
                "combined_score": result["combined_score"],
                "time_factor": result.get("time_factor", 0.8),
                "contribution": result["contribution"],
                "search_score": result["search"].get("score", 0),
                "debate_score": result["debate"].get("score", 0),
                "falsification_detected": result.get("falsification", {}).get("detected", False),
                "falsification_penalty": result.get("falsification", {}).get("penalty", 0),
                "adversarial_detected": result.get("adversarial", {}).get("detected", False),
                "adversarial_count": len(result.get("adversarial", {}).get("matches", [])),
                "duration_ms": result["duration_ms"],
                "timestamp": result["timestamp"],
            }
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # non-critical, continue  # Logging does not affect main flow

    def get_logs(self, limit: int = 50) -> List[Dict]:
        """Get recent verification logs."""
        if not self._log_path.exists():
            return []
        logs = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        logs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return logs[-limit:]

    def get_stats(self) -> Dict:
        """Get validator statistics."""
        logs = self.get_logs(1000)
        if not logs:
            return {
                "total_verified": 0,
                "avg_contribution": 0,
                "avg_combined_score": 0,
                "auto_promoted": 0,
            }
        total = len(logs)
        avg_contrib = sum(l.get("contribution", 0) for l in logs) / total
        avg_score = sum(l.get("combined_score", 0) for l in logs) / total
        promoted = sum(1 for l in logs if l.get("contribution", 0) >= 1.0)
        return {
            "total_verified": total,
            "avg_contribution": round(avg_contrib, 2),
            "avg_combined_score": round(avg_score, 2),
            "auto_promoted": promoted,
        }


# Global singleton
_validator_instance: Optional[AutoValidator] = None


def get_validator() -> AutoValidator:
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = AutoValidator()
    return _validator_instance
