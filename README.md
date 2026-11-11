# stock-system

A股量化选股/回测/模拟盘系统。规格见 docs/spec.md，术语见 GLOSSARY.md。

## 结构

- `backend/app/`：data / analysis / strategy / backtest / api / scheduler / common
  依赖单向 data → analysis → strategy → backtest/api，反向 import 由
  `backend/tests/test_import_rules.py` 拦截。
- `frontend/`：Vue 3 + Vite 脚手架
- `configs/`：risk.yaml / ai.yaml / data.yaml（YAML + Pydantic v2 校验，
  实现见 `backend/app/common/config.py`，默认值来源 docs/spec.md §7.5 / §4.4 / §12）

## 运行测试

```bash
python -m pytest backend/tests -q
```

## 前端

```bash
cd frontend && npm install && npm run dev
```
