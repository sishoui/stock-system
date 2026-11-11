# stock-system

A股量化选股/回测/模拟盘系统。规格见 docs/spec.md，术语见 GLOSSARY.md。
agent 协作前先读 docs/agents/ 下的工作约定（Windows 读 issue 需先设 UTF-8 输出编码）。

## 结构

- `backend/app/`：data / analysis / strategy / backtest / api / scheduler / common
  依赖单向 data → analysis → strategy → backtest/api，反向 import 由
  `backend/tests/test_import_rules.py` 拦截。
- `frontend/`：Vue 3 + Vite 脚手架
- `configs/`：risk.yaml / ai.yaml / data.yaml（YAML + Pydantic v2 校验，
  实现见 `backend/app/common/config.py`，默认值来源 docs/spec.md §7.5 / §4.4 / §12）
- 代码级枚举值（信号级别/校验类型/AI 状态）单一事实源：`backend/app/common/enums.py`，
  一致性由 `backend/tests/test_enum_guard.py` 守卫
- `docs/agents/`：agent 工作约定（issue tracker、triage 标签、领域文档规则、
  subagent 提示词模板）
- CI：`.github/workflows/ci.yml`（backend pytest + frontend build）

## 运行测试

```bash
python -m pytest backend/tests -q
```

## 前端

```bash
cd frontend && npm install && npm run dev
```
