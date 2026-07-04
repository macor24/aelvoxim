"""
metacore.experts.ethics — Ethics Expert with 15-rule ethical screening.

Evaluates ethical implications using a priority matrix and
sensitive pattern detection across 15 categories.
Pure rule-based — does NOT call SentriKit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseExpert, ExpertInput, ExpertOutput, register


# ── 15 ethical check categories ──────────────────────────────
# Each has: category, keywords, priority, block_threshold

_ETHICS_RULES: List[Dict] = [
    # (category_name, keywords, priority, is_blocking)
    ("privacy_leak",        ["phone", "phone number", "身份证", "银行卡", "credit card",
                             "ssn", "social security", "passport", "address",
                             "phone number", "id number", "id_card"], 90, True),
    ("copyright_violation", ["pirate", "盗版", "crack", "warez", "未经授权",
                             "copyright infringement", "plagiarism", "抄袭",
                             "unauthorized copy"], 85, True),
    ("discrimination",      ["racial", "racist", "sexist", "歧视", "种族",
                             "gender discrimination", "ageism"],
                             80, True),
    ("manipulation",        ["manipulate", "骗", "诱导", "trick", "deceive",
                             "scam", "诈骗", "deception"], 80, True),
    ("misinformation",      ["fake news", "disinformation", "misinformation",
                             "谣言", "虚假信息", "conspiracy"], 75, False),
    ("impersonation",       ["impersonate", "冒充", "伪装", "pretend to be",
                             "fake identity", "假扮"], 75, False),
    ("violence",            ["violence", "暴力", "attack", "hurt", "伤害",
                             "assault", "torture", "虐待"], 85, True),
    ("illegal_activity",    ["illegal", "违法", "犯罪", "unlawful", "crime",
                             "money laundering", "洗钱"], 90, True),
    ("adult_content",       ["adult", "porn", "explicit", "色情", "裸露",
                             "nsfw", "sexual"], 80, True),
    ("self_harm",           ["suicide", "自伤", "自杀", "self-harm", "自残",
                             "kill myself", "不想活"], 100, True),
    ("hate_speech",         ["hate speech", "仇恨", "slur", "nazi",
                             "white supremacist"], 85, True),
    ("child_protection",    ["child", "未成年", "minor", "儿童",
                             "underage"], 95, True),
    ("financial_fraud",     ["guaranteed return", "高回报", "investment scheme",
                             "ponzi", "庞氏", "pyramid", "传销"], 80, True),
    ("drugs_substances",    ["drug", "毒品", "cocaine", "heroin", "weed",
                             "marijuana", "meth"], 85, True),
    ("malware",             ["virus", "trojan", "ransomware", "木马", "virus",
                             "malware", "keylogger", "backdoor"], 85, True),
]

# ── Sensitive data patterns ──
_SENSITIVE_PATTERNS = [
    "api_key", "password", "secret", "token", "credential",
    "ssh-key", "private key",
]


@register
class EthicsExpert(BaseExpert):
    """Evaluates ethical implications using 15-rule matrix.

    Pipeline:
    1. Scan query against all 15 rule categories
    2. Check sensitive data patterns (credentials/keys)
    3. Apply priority-based blocking (blocking rules >= 80 auto-block)
    4. Return opinion with triggered categories and priority chain
    """
    _capabilities = ["ethics", "safety", "compliance", "audit"]

    name = "ethics"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Check if safety has already blocked
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "concerns": [],
            "priority_chain": [],
            "affected_rules": [],
            "triggered_categories": [],
        }

        query = inp.query.lower()

        # Step 1: Scan all 15 ethical categories
        for rule in _ETHICS_RULES:
            category, keywords, priority, is_blocking = rule
            for kw in keywords:
                if kw in query:
                    details["triggered_categories"].append(category)
                    details["concerns"].append(
                        f"[{category.upper()}] keyword: '{kw}' (priority {priority})"
                    )
                    details["priority_chain"].append(
                        f"{category} (priority {priority}, block={is_blocking})"
                    )
                    break  # one match per category

        # Step 2: Sensitive data pattern check
        for pattern in _SENSITIVE_PATTERNS:
            if pattern in query:
                details["concerns"].append(f"Sensitive data: {pattern}")
                details["triggered_categories"].append("sensitive_data")

        # Step 3: Decide — blocking or warning
        if details["triggered_categories"]:
            # Check if any blocking rule with priority >= 80 triggered
            # or self_harm (100) always blocks
            blocking_hits = [
                cat for cat in details["triggered_categories"]
                for rule in _ETHICS_RULES
                if rule[0] == cat and rule[3] and rule[2] >= 80
            ]
            self_harm = any("self_harm" in cat for cat in details["triggered_categories"])
            highest_priority = max(
                (r[2] for r in _ETHICS_RULES if r[0] in details["triggered_categories"]),
                default=0,
            )

            if blocking_hits or self_harm or highest_priority >= 90:
                return ExpertOutput(
                    expert_name=self.name,
                    opinion=(
                        f"ETHICAL BLOCK: "
                        f"{', '.join(details['triggered_categories'][:4])}"
                    ),
                    confidence=0.0,
                    details=details,
                    error="ETHICAL BLOCK",
                )
            else:
                return ExpertOutput(
                    expert_name=self.name,
                    opinion=(
                        f"Ethical concern: "
                        f"{', '.join(details['triggered_categories'][:3])} "
                        f"(priority {highest_priority})"
                    ),
                    confidence=0.3,
                    details=details,
                    error=None,
                )

        return ExpertOutput(
            expert_name=self.name,
            opinion="No ethical concerns detected.",
            confidence=0.8,
            details=details,
            error=None,
        )
