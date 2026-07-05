# Aelvoxim

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**Aelvoxim — 轻量级 AI 认知引擎框架。**

给你的 AI 应用装上"记忆体"——能记住、能推理、能学习、有元认知。

---

## 定位

| 版本 | 定位 | 一句话 |
|------|------|--------|
| **开源版** | 有生命感的记忆体 | 会遗忘、会过载、会怀疑自己、会从对话中沉淀知识 |
| **Pro 版** | 自动进化的生产级大脑 | 7×24 自动学习、自我修复、动态优化 |

## 核心特性

### 🧠 记忆系统
- **四层记忆架构**：工作记忆 / 情节记忆 / 语义记忆 / 程序记忆
- **MemoryFusion**：倒排索引 + 分层优先级检索
- **遗忘曲线**：知识随时间自然衰减（艾宾浩斯曲线）
- **记忆冲突检测**：新旧知识矛盾自动发现，主动提醒
- **Confidence Matrix**：五维度置信度评分，每次回答标注可信度

### 🔧 专家系统
| 专家 | 社区版 | Pro版 |
|------|--------|-------|
| LogicExpert（逻辑推理） | ✅ | ✅ |
| MemoryExpert（记忆检索） | ✅ | ✅ |
| EthicsExpert（伦理规则） | ✅ | ✅ |
| SafetyExpert（安全检查） | ✅ | ✅ |
| CodeReviewExpert（代码审查） | ✅ | ✅ |
| EmotionExpert（情感分析） | ❌ | ✅ |
| CreativeExpert（创作引导） | ❌ | ✅ |
| IntrospectionExpert（自我反思） | ❌ | ✅ |
| HypothesisEngine（假设推理） | ❌ | ✅ |

### 📚 学习系统
| 能力 | 社区版 | Pro版 |
|------|--------|-------|
| 手动学习（API触发） | ✅ | ✅ |
| 7×24 自动学习循环 | ❌ | ✅ |
| 好奇心驱动发现 | ❌ | ✅ |
| 自动参数调优 | ❌ | ✅ |
| 知识缺口分析 | ❌ | ✅ |
| 后置验证审计 | ❌ | ✅ |

### 🔌 完整 API
- RESTful API（FastAPI + Swagger）
- SSE 流式输出
- 多用户认证（邮箱/API Key）
- 知识库 CRUD + 搜索
- 会话管理

## 快速开始

```bash
# 安装
pip install aelvoxim

# 启动服务
aelvoxim server --port 9701

# 浏览器打开
open http://localhost:9701
```

### 从源码运行

```bash
git clone https://github.com/gmxchz/aelvoxim.git
cd aelvoxim
pip install -e .
python src/run_server.py 9701
```

## 项目结构

```
aelvoxim/
├── src/aelvoxim/
│   ├── core/         认知引擎（信念/元认知/推理/Judge/DGM-H）
│   ├── learn/        学习引擎（Learner/知识库/验证器/搜索）
│   ├── memory/       记忆系统（4层/融合/遗忘/冲突）
│   ├── server/       API服务（FastAPI路由/认证/对话管线）
│   ├── cortex/       大脑皮层（路由/调度）
│   ├── experts/      专家系统（7+专家/编排器/注册器）
│   ├── storage/      存储层（SQLite/PostgreSQL双模）
│   └── utils/        工具函数
├── tests/            测试（130+ 测试用例）
├── docs/             文档
└── README.md
```

## 开源 / Pro 对比

| 维度 | 社区版（免费） | Pro版（¥99/月） |
|------|--------------|----------------|
| 学习方式 | 手动触发 | 7×24 自动循环 |
| 知识发现 | 用户指定 | 好奇心自动探索 |
| 参数调优 | 出厂参数 | 自动动态调优 |
| 专家数量 | 5个 | 12个（含高级专家） |
| 后置验证 | 无 | 定时自动扫描 |
| 安全 | 本地规则 | SentriKit 深度引擎 |
| 授权 | MIT 开源 | License Key |

## 外部依赖

- **bcrypt** — 密码哈希
- **fastapi** — API 框架
- **uvicorn** — ASGI 服务器

其余全部 Python 标准库。

## 许可证

MIT License

Copyright (c) 2026 gmxchz
