# MetaCore 整体架构与运行逻辑

## 项目定位

8,373 行，纯标准库（零第三方依赖），自进化 AI Agent 框架。

---

## 一、全局架构

```
┌─────────────────────────────────────────────────┐
│                 aelvoxim.ui                      │
│   纯展示面板 / html.server / 3卡片 + 方向表格     │
├─────────────────────────────────────────────────┤
│                aelvoxim.api                      │
│   get_config / set_config / list_config          │
├─────────────────────────────────────────────────┤
│               aelvoxim.memory                    │
│   store / get / search / delete / list_keys      │
├─────────────────────────────────────────────────┤
│               aelvoxim.hooks                     │
│   record_outcome / emit_metric / learning_sw      │
├─────────────────────────────────────────────────┤
│               aelvoxim.core                      │
│   DGM-H 元认知核心 (7文件, 2,372行)               │
│   ├─ dgmh.py:      全局超脑 + 安全闸门            │
│   ├─ metacog.py:   元认知触发器                   │
│   ├─ selfmodel.py: 能力画像 + Beta分布评分         │
│   ├─ belief.py:    信念引擎 + 贝叶斯地基           │
│   ├─ judge.py:     提案评分 (S/A/B/C/D)           │
│   ├─ calibration:  7类默认参数 + 版本追踪          │
│   └─ cognitive.py: 知识冲突检测                    │
├─────────────────────────────────────────────────┤
│               aelvoxim.learn                     │
│   自学习引擎 (10文件, 4,228行)                     │
│   ├─ learner.py:   学习循环 + 方向管理 + 复习      │
│   ├─ decompose.py: 方向分解 (5语种检测)            │
│   ├─ validate.py:  执行→验证→入库管道              │
│   ├─ execute.py:   真执行模板 (8个模板)            │
│   ├─ extract.py:   知识提取 (LLM+搜索+质量校验)     │
│   ├─ discover.py:  自动发现建议                    │
│   ├─ monitor.py:   健康监控 + 自修复 (6种修复)      │
│   ├─ search.py:    5引擎降级链                     │
│   ├─ llm.py:       LLM调用适配                    │
│   └─ knowledge.py: 知识库存储/检索                │
├─────────────────────────────────────────────────┤
│               aelvoxim.utils                     │
│   METACORE_DIR / JSON 读写 / i18n 中英切换        │
└─────────────────────────────────────────────────┘
```

---

## 二、学习循环运行流程

```
用户: python -m aelvoxim learn start
  ↓
Learner.start()
  ├── 检查 LLM 可用 (无则暂停)
  ├── 检查 enabled.flag (无则暂停)
  └── 启动 _main_loop 后台线程
       ↓
       _main_loop (永久运行，每轮 ~30s-150s)
       ┌──────────────────────────────────────────────┐
       │  while running:                               │
       │                                               │
       │  遍历所有 active 方向:                         │
       │  ┌──────────────────────────────────────────┐ │
       │  │ _learn_one_cycle(direction)              │ │
       │  │ 1. 无任务队列 → decompose_direction      │ │
       │  │ 2. 取当前任务 → execute_and_validate     │ │
       │  │ 3. 有结果 → on_store 回调                │ │
       │  │ 4. 无结果 → 下次重试 (3次后完成)          │ │
       │  └──────────────────────────────────────────┘ │
       │                                               │
       │  检查到期复习 → _check_reviews                │
       │  无活跃方向 → _auto_add_direction             │
       │  无活跃方向 → HealthMonitor.tick()            │
       │                                               │
       │  _sleep(3-60秒)                               │
       └──────────────────────────────────────────────┘
```

---

## 三、方向分解流程 (_learn_one_cycle 中的 step 1)

```
用户添加方向 "FastAPI async optimization"
  ↓
decompose_direction(topic)
  │
  ├─ [策略1] 关键词预设匹配
  │   └─ "FastAPI" matched → 返回 8 个预设子任务
  │       ["Route and dependency injection design",
  │        "Pydantic models and data validation",
  │        "Async request processing optimization",
  │        "Database session management",
  │        "Middleware and CORS configuration",
  │        "API documentation generation",
  │        "Authentication and authorization",
  │        "Performance benchmarking and tuning"]
  │
  ├─ [策略2] 多语种搜索分解 (当策略1无匹配时)
  │   ├─ detect_lang(topic) → 'en' / 'zh' / 'ja' / 'kr' / 'other'
  │   ├─ 'en':    搜 topic, 提取英文技术词 [a-zA-Z-]{4,20}
  │   ├─ 'zh':    搜 topic+" 学习 分解", 提取中文词 [\u4e00-\u9fff]{4,16}
  │   ├─ 'ja':    搜 topic, 提取假名+汉字 [\u3040-\u30ff\u4e00-\u9fff]{2,8}
  │   ├─ 'kr':    搜 topic, 提取한글 [가-힣]{2,8}
  │   └─ 每种语言有自己的停用词表，提取到 ≥3 个词才返回
  │
  └─ [策略3] 通用分类 fallback (当策略1+2都失败)
      └─ 返回 6 个通用子任务
          ["{topic} - Core Concepts",
           "{topic} - Main Tools and Frameworks",
           "{topic} - Implementation Steps",
           "{topic} - Common Issues and Solutions",
           "{topic} - Best Practices",
           "{topic} - Performance Optimization"]
```

---

## 四、执行→验证→入库流程 (execute_and_validate)

```
execute_and_validate(topic, task, log_func, on_store)
  │
  ├─ [去重] 检查该 title 是否已存在
  │   └─ 存在 → return True (不重复入库)
  │
  ├─ [尝试1] 真执行
  │   └─ try_execute_task(topic, task)
  │       ├─ 关键词匹配 8 个模板 (路由/依赖/数据库/索引/测试/部署/配置/函数)
  │       ├─ 生成 .py 脚本 → subprocess.run(timeout=10)
  │       ├─ 成功 → return stdout (source=execution_result, conf=0.9)
  │       └─ 失败 → return None
  │
  ├─ [尝试2] 搜索提取 (当真执行失败时)
  │   └─ extract_knowledge(task, "practice")
  │       ├─ [L1] LLM 蒸馏 (需 LLM 可用)
  │       ├─ [L2] 搜索 + LLM 精炼
  │       └─ [L3] 拒绝编造 → return None
  │
  ├─ [质量校验 — 3道关卡]
  │   1. content_has_real_value(content)
  │      └─ 含: 数据/数字、技术关键词、代码语法、外部引用 (≥2项)
  │   2. 长度校验
  │      ├─ 搜索来源 ≥ 80 字符
  │      └─ 真执行 ≥ 20 字符
  │   3. is_valid_content (跨 topic 重复校验)
  │
  ├─ [AutoValidator] (仅搜索来源)
  │   ├─ L1: SearchVerifier (3引擎交叉验证)
  │   ├─ L2: DebateVerifier (LLM 正反辩论)
  │   └─ 综合评分 < 0.4 → 拒绝入库
  │
  └─ [入库]
      ├─ KnowledgeBase.store(topic, title, summary, content, source, confidence)
      │   ├─ _find_duplicate(title, summary, topic) — 相似度去重
      │   ├─ 写入 JSON 文件 → ~/.aelvoxim/knowledge/entries/{id}.json
      │   └─ 更新 index.json
      └─ 回调 on_store(topic, title, score)
          ├─ direction.entries_created += 1
          ├─ direction.completed_tasks 追加当前 task
          └─ _save_config()
```

---

## 五、间隔重复复习流程

```
方向完成时
  ↓
_submit_verification_task(topic, is_review=False)
  ├─ 记录到 SelfModel (DecisionEntry)
  ├─ 不是复习 → 只是标记完成
  └─ 调用 _schedule_review(topic)
      └─ review_history = ["2026-05-30 17:13:00"] (1天后)

后台循环
  ↓
_check_reviews()
  ├─ 遍历所有 completed/mastery 方向
  ├─ 对比 review_history[-1] 与当前时间
  ├─ 到期 → 调 _submit_verification_task(topic, is_review=True)
  │   ├─ 复习间隔: 1天 → 3天 → 7天 → 30天
  │   └─ 记录到 SelfModel
  └─ 未到期 → 跳过
```

---

## 六、自动发现流程

```
_auto_add_direction()  (每 3 分钟最多一次)
  │
  ├─ [策略1] 从知识库高频 topic 提取新方向
  │   ├─ 优先: source=user_chat + value_level≥2
  │   └─ 次优: 其他高 conf 条目
  │
  ├─ [策略2] 搜索 fallback (只搜英文，避免中文噪音)
  │   ├─ 搜索 "AI agent development system..."
  │   ├─ 只提取英文技术词
  │   └─ 停用词过滤 (≥3 个停用词拦截)
  │
  └─ 都不成功 → return False (不下次 fallback)
```

---

## 七、健康监控自修复流程

```
HealthMonitor.tick()  (每 5 分钟在 Learner 空闲时触发)
  │
  ├─ _collect_metrics()
  │   ├─ active 方向数 / completed 方向数
  │   ├─ 知识库近 24h 增长率
  │   ├─ 搜索引擎配置 (是否 mock)
  │   ├─ 自动发现异常次数
  │   └─ Learner 空闲超时
  │
  ├─ _diagnose(snapshot)
  │   ├─ [规则1] 搜索引擎 mock → 诊断级别 MEDIUM
  │   ├─ [规则2] 无活跃方向 > 1h → MEDIUM
  │   ├─ [规则3] 自动发现异常 > 3次 → MEDIUM
  │   ├─ [规则4] 方向卡死 2h 无产出 → MEDIUM
  │   ├─ [规则5] Learner 空闲 > 24h → MEDIUM
  │   ├─ [规则6] 待审核知识堆积 >20 → MEDIUM
  │   └─ [规则7] SelfModel 无快照 → MEDIUM
  │
  └─ _heal(diagnosis)
      ├─ 修复1: 改 search-config.json mock → bing_cn
      ├─ 修复2: 从知识库添加新方向
      ├─ 修复3: 重置卡死方向任务队列
      ├─ 修复4: 批量 approve 高置信度待审核知识
      ├─ 修复5: 记录自动发现异常诊断日志
      └─ 修复6: 强制生成 SelfModel 快照
```

---

## 八、模块依赖关系 (无循环)

```
learner.py
  ├── decompose.py  ← search.py
  ├── validate.py   ← execute.py, extract.py (← search.py, knowledge.py, llm.py)
  │                 ← knowledge.py, validator.py
  ├── discover.py  (纯函数)
  └── monitor.py   ← knowledge.py, selfmodel.py

core/ 模块:
  belief ← calibration
  cognitive ← calibration
  judge ← calibration
  metacog ← selfmodel, calibration
  selfmodel ← calibration

memory / hooks / api / ui / utils:
  各自独立，不依赖 learn/ 或 core/
```

---

## 九、数据流总图

```
用户 CLI 命令
    │
    ▼
__main__.py
    │
    ├─ learn add "Topic"     → Learner.add_direction()
    ├─ learn start           → Learner.start() → _main_loop
    ├─ learn stop            → Learner.stop()
    ├─ learn status          → Learner.get_status()
    ├─ ui --port 9700        → DashboardHandler / serve()
    │                          ├─ GET / → dashboard.html
    │                          └─ GET /api/overview → _get_overview()
    └─ status                → Learner.get_status()

数据存储:
    ~/.aelvoxim/
    ├── learner/
    │   ├── config.json      ← LearningDirection[] (方向状态)
    │   ├── status.json      ← running / directions_count (跨进程)
    │   ├── learner.log      ← 日志
    │   └── enabled.flag     ← 学习开关
    ├── knowledge/
    │   ├── index.json       ← 知识库索引
    │   ├── entries/{id}.json ← 知识条目
    │   └── pending.json     ← 待审核知识
    ├── llm-config.json      ← LLM 配置
    ├── search-config.json   ← 搜索引擎配置
    ├── config.json          ← 通用配置 (aelvoxim.api)
    ├── memory.json          ← 精简记忆
    ├── selfmodel.json       ← SelfModel 决策+快照
    └── heal_log.jsonl       ← 自修复日志
```

---

## 十、质量保证体系

```
入口 → 知识入库 → 质量关卡

               content_has_real_value()
               ├─ 含数字/百分比          +1
               ├─ 含技术关键词           +1
               ├─ 含代码语法            +1
               └─ 含外部引用            +1
               ≥2项 → 通过

               is_valid_content()
               ├─ 跨主题重复检测
               ├─ 精确内容重复检测
               └─ 同主题内容重复检测

               AutoValidator (仅搜索来源)
               ├─ L1 SearchVerifier → 3引擎交叉验证
               ├─ L2 DebateVerifier → LLM 正反辩论
               └─ combined_score ≥ 0.4 → 通过

               _find_duplicate()
               ├─ title 相似度
               ├─ summary/content 相似度
               └─ 同 topic 加分
               ≥0.95 → rejected_duplicate
```
