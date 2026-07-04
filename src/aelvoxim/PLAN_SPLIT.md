# Phase 3 — 大文件拆分计划

## 拆分原则
- 不破坏任何 import（keep backward compatibility via __init__.py or direct import fallback）
- 每个新文件保持独立可导入
- 当前所有 import 路径不变（通过 from .knowledge import X 仍能工作）

---

## 1. learn/knowledge.py (1267行) → 3个文件

### 拆分点

```
knowledge.py (原)                 新文件
├── I/O 工具函数 (L1-180)          → knowledge_io.py     (锁/读写/目录)
├── 语义去重 + 常识检查 (L184-282) → knowledge_quality.py (dedup/sanity)
├── 向量搜索 (L283-443)            → knowledge_vectors.py (vocab/embedding/similarity)
└── KnowledgeBase 类 (L444-1267)   → knowledge.py 保留   (主类 + auto_review + review)
```

### 具体边界

- **knowledge_io.py**: `_acquire_process_lock` ~ `_write_rejected` (L45-163)，+ `_ensure_dirs`, `_entry_path`, `_read/write_*`
- **knowledge_quality.py**: `_tokenize` ~ `_find_duplicate` (L187-282)，+ `_check_content_sanity`
- **knowledge_vectors.py**: `_segment` ~ `_vector_search` (L291-443)
- **knowledge.py** 保留: `KnowledgeBase` 类 (L444~1267)，+ `auto_review`, `cleanup_low_value`, `periodic_review`

### import 调整
- `knowledge.py` 在新的 `knowledge/` 包中作为 `__init__.py` 或直接 import
- 最简单: 把 3 个新文件放在 `learn/knowledge/` 子包，`knowledge.py` 保留原名但做 thin 导入

---

## 2. learn/validator.py (879行) → 3个文件

### 拆分点

```
validator.py (原)                   新文件
├── SearchVerifier (L34-284)        → validator_search.py
├── DebateVerifier (L288-439)       → validator_debate.py
├── FalsificationVerifier (L443-579)→ validator_falsify.py
└── AutoValidator (L583-879)         → validator.py 保留
```

### import 调整
- `AutoValidator` 和 `get_validator()` 保留在 `validator.py`
- 3 个 verifier 类各自独立文件

---

## 3. learn/llm.py (925行) → 3个文件

### 拆分点

```
llm.py (原)                           新文件
├── ModelConfig + default models      → llm_config.py
├── call_llm + provider impls         → llm_providers.py (callers + ollama/openai/anthropic)
├── FallbackEngine                    → llm_engine.py
└── SmartOrchestrator + CoT + rest    → llm.py 保留
```

---

## 4. learn/learner.py (694行) → 2个文件

### 拆分点
```
learner.py (原)                      新文件
├── 工具函数/常量 (L1-100)           → learner.py 保留
├── Learner 类主逻辑 (L115-690)       → learner.py 保留
├── _learn_one_cycle 太长 (L193-279)  → learner_cycle.py
└── review/schedule 逻辑 (L302-550)   → learner_review.py
```

---

## 5. learn/monitor.py (739行) → 2个文件

### 拆分点
```
monitor.py (原)                       新文件
├── 工具函数/常量 (L1-215)            → monitor_utils.py
├── HealthMonitor 类 (L219-739)        → monitor.py 保留
└── self_heal 逻辑 (L530-739)         → monitor_heal.py
```

---

## 执行策略

这些拆分涉及大量 import 路径调整。风险较高，建议：
1. 先拆分 knowledge.py（独立模块，import 影响最小）
2. 测试后再拆分 validator.py
3. 最后拆分 llm.py / learner.py / monitor.py（import 引用最多）

或者：**先不做拆分，Phase 4 接口契约加固完成后，等有真实测试覆盖再拆分**。
拆分有风险，当前没有 pytest 测试覆盖，拆分后很难确认没断引用。
