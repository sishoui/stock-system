# Domain docs

Single-context layout: root `GLOSSARY.md` + `docs/adr/`（尚未建立 ADR 目录，规格决策溯源见 `docs/spec.md` 各节「来源：#n」标注）。

## Consumer rules

- **实现任何票之前**：通读 `GLOSSARY.md` 相关小节；领域术语（硬门槛/三判断/股票池/卖出线等）以词汇表为准，不得自造同义词。
- **代码级枚举值**（信号级别、校验类型等字符串字面量）以 `backend/app/common/enums.py` 为单一事实源，GLOSSARY 只定义语义不定义取值；一致性由 `backend/tests/test_enum_guard.py` 机械守卫。
- 规格原文在 `docs/spec.md`，章节号（§3.2、§7.5 等）直接对应 spec 内锚点。
- 引入新领域概念时：先补 GLOSSARY 词条，再写代码。
