# Contributing to Aelvoxim

感谢你对 Aelvoxim 的关注！

## 快速开始

```bash
git clone https://github.com/gmxchz/aelvoxim
cd aelvoxim
pip install -e .
python src/run_server.py 9701
```

## 开发环境

```bash
pip install -e .[dev]  # pytest
python -m pytest tests/ -v
```

## 代码规范

- **纯标准库优先** — 只有 bcrypt、fastapi、uvicorn 是外部依赖
- **全英文** — 标识符、注释、文档字符串全部用英文
- **类型标注** — 公开 API 必须有类型提示
- **文档字符串** — 每个公开函数都需要
- **异常处理** — 每个 `except` 必须有理由（设计选择，不是偷懒）

## 项目架构

```
aelvoxim/
├── core/         认知引擎（信念/元认知/推理/Judge/DGM-H/校准）
├── learn/        学习引擎（Learner/知识库/验证器/搜索/好奇心）
├── memory/       记忆系统（4层/融合/遗忘曲线/冲突/评分）
├── server/       API服务（FastAPI路由/认证/对话管线/会话管理）
├── cortex/       大脑皮层（路由/调度器）
├── experts/      专家系统（7+专家/编排器/子进程管理）
├── storage/      存储层（SQLite/PostgreSQL双模）
├── utils/        工具函数
└── edition.py    版本门控（开源版/Pro版配置控制）
```

## 添加新专家

1. 创建 `aelvoxim/experts/my_expert.py`
2. 继承 `BaseExpert`，实现 `run(inp) -> ExpertOutput`
3. 添加 `@register` 装饰器和 `_capabilities` 列表
4. 在 `experts/__init__.py` 底部添加 import
5. 专家自动注册——无需修改编排器

```python
from .base import BaseExpert, ExpertInput, ExpertOutput, register

@register
class MyExpert(BaseExpert):
    _capabilities = ["my_domain", "specialized_skill"]
    name = "my"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        # Your logic here
        return ExpertOutput(
            expert_name=self.name,
            opinion="...",
            confidence=0.8,
        )
```

## 版本门控说明

Aelvoxim 使用配置门控区分社区版和 Pro 版：

- **社区版**：`edition="community"` — 手动学习、5个基础专家
- **Pro 版**：`edition="pro"` — 自动学习、全部12个专家

通过环境变量 `AELVOXIM_EDITION` 或 license key 设置。

## Pull Request 流程

1. 保持改动聚焦——一个 PR 一个功能
2. 新功能必须有测试
3. 如果 API 变更，更新 README
4. 提交前运行 `pytest tests/ -v`
5. 通过 `python -c "from aelvoxim import __version__"` 导入检查
