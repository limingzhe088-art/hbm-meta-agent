---
name: hbm-meta
description: "Human Biomonitoring Meta-Analysis Agent — orchestrates the full pipeline for meta-analyses of internal-exposure biomarker concentrations (urine / blood / cord blood / breast milk / nail / hair), from search-strategy construction through screening, quality assessment, AI-assisted data extraction with mandatory human proofing, unit and statistic standardization, sample-size-weighted geometric-mean pooling, estimated daily intake (EDI), sensitivity analysis, and a closed-loop reviewer-revision engine. Analyte-agnostic and population-agnostic: designed for any exposure biomarker (e.g. arsenic, cadmium, lead, mercury, PFOA/PFAS, antibiotics, drug concentrations) in any region and any period. Bundles a frozen data contract, six human quality gates, reproducible Python scripts (number audit, cohort de-duplication, sensitivity analysis, DOI verification, schema validation, unit conversion, weighted pooling, EDI, Wan et al. 2014 median-to-mean conversion), PRISMA 2020 flow accounting, AHRQ 11-item quality scoring, ICRP 89 urine-correction reference values, and six independently triggerable sub-skills. Triggers on: meta-analysis, systematic review, biomonitoring, internal exposure, human exposure, 内暴露, 人体暴露, 生物监测, meta 分析, 系统综述, 检索式, 检索策略, 文献检索, 数据库检索, 文献筛选, 纳入排除标准, 质量评价, 偏倚分析, 数据提取, 提取表, 主表构建, 单位换算, 浓度换算, 尿肌酐校正, GM 转换, 加权合并, 加权几何均值, 合并分析, 描述性分析, EDI, 估计每日摄入量, 暴露评估, 敏感性分析, 去重筛查, 重复计数, 数值审计, 稿件核对, 参考文献核对, DOI 核验, 审稿意见, 返修, 逐条回复, search strategy, Boolean search string, literature retrieval, screening, eligibility criteria, risk of bias, quality assessment, data extraction, unit conversion, creatinine correction, geometric mean conversion, sample-size-weighted pooling, estimated daily intake, sensitivity analysis, robustness check, duplicate cohort detection, number audit, citation crosswalk, DOI verification, reviewer response."
license: MIT (code) / CC-BY-4.0 (docs)
compatibility: opencode claude-code dsh
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
  - WebFetch
  - WebSearch
  - Task
  - TodoWrite
  - AskUserQuestion
metadata:
  version: "1.0.0"
  last_updated: "2026-07-19"
  status: active
  contract_version: "1.0.0"
  task_type: open-ended
  tool_name_mapping: "DSH 不解析 allowed-tools；上表沿用 Claude Code 规范写法。若运行于 DSH，`Task`→`subagent`/`subagent_fork`，`TodoWrite`→`todo_write`，`AskUserQuestion`→`ask_user_question`（DSH 内置大小写不敏感的 CC 工具名映射表）。"
---

# HBM-Meta-Agent — 人体内暴露浓度 Meta 分析 Agent

**一句话**：把"人体内暴露生物标志物浓度"的 Meta 分析，从检索到返修做成一条**可复现、可审计、人工裁决点明确**的流水线。

---

## 1. 这个技能包解决什么问题

做内暴露浓度 Meta 分析的研究者反复踩同一组坑。本工作包把一类真实项目的四类历史事故编码成机制：

| 事故 | 表现 | 机制化对策 |
|---|---|---|
| **快照漂移** | 手稿数值基于旧数据表，返修时才发现时段/地区数值系统性过时，连带一整轮重写 | `shared/data-contract.md` §4 **快照指纹**（sha256 + 时间 + 记录数），手稿数字追溯不到来源即判 `STALE_MANUSCRIPT` |
| **分期不一致** | 描述性分析用一套时段边界、EDI 脚本用另一套、手稿混用 → 审稿人直接指出 | `project.yaml → period_scheme` **单点定义 + 冻结日**，任何改动 bump `config_version` 并强制下游重算 |
| **单位口径混用** | 尿样的 μg/L、μg/g Cr、比重校正三类记录被直接合并，噪音吞掉信号 | 契约 §3.6/3.7 把 `unit_original`（原文留痕）与 `unit_final`（标准化输出）**分列建模**，`adjusted` 状态强制登记 |
| **重复计数** | 同一出生队列被 35 篇论文报告，参与者总数被重复累加，审稿人质疑"一般人群"代表性 | 契约 §3.2 `cohort_id` + `dedup_group` 字段，GATE-2/GATE-5 强制队列聚簇裁决 |

**再加两条本项目用血换来的教训，已写进硬约束**：

- **AI 初提必漏字段**（采样年份、尿校正状态、血基质、统计量类型），因此 GATE-3 要求**逐条人工校对**，`verified=TRUE` 比例不足的记录不得用于主结论。
- **绝不允许推断未报告的数据**：`gm_summary` / `sample_size` / `time` / `sample_type` 四个字段**禁止插补**；其余任何非原文直接值必须登记在 `inferred_flags`（**20 项**文档化标记；单一来源 `shared/inferred_flags.py`，权威定义 `shared/data-contract.md` §6）。

---

## 2. 通用性设计（换暴露物 / 换人群为什么不用重写）

可变项与不变项在架构上分离：

| 每次会变 | 在本包里改哪里 |
|---|---|
| 暴露物（砷 → PFOA / 抗生素 / 药物浓度） | `project.yaml → analyte` + 该物质的单位换算表 |
| 人群（中国一般人群 → 全球 / 特定人群） | `project.yaml → country` + `population_group` 归类规则 |
| 时空范围（1980–2024 中国 → 任意时段/区域） | `project.yaml → period_scheme` + `region_map` |
| 检索式 | 由 S1 每次重新构造（搜索式本身不可复用，**排除词银行**可复用） |
| 单位与报告形式 | `project.yaml → unit_policy` + S4 换算表 |

| 不变（核心资产） | 存放位置 |
|---|---|
| 内暴露基质枚举（尿/血/脐血，可扩展母乳/指甲/头发） | `shared/data-contract.md` §3.5 |
| 数据提取逻辑（AI 初提 + 人工逐条校对） | `skills/S3-extraction-standardization/` |
| 三级标准化流程（统一暴露指标 → 统一浓度单位 → 统一为 GM） | `skills/S4-unit-statistic-conversion/` |
| 加权合并方法（样本量加权 GM/GSD） | `skills/S5-weighted-pooling-edi/` |
| 质控与返修体系（数值审计/去重/敏感性/DOI 核验/审稿闭环） | `skills/S6-qc-audit-revision/` |

**判据**：只要你的研究是"把多篇文献里的人体生物样本浓度合并成总体水平"，本包适用；如果你做的是效应量合并（OR/RR/HR）、单个体的原始数据、或非人体样本，本包不适用。

---

## 3. 目录结构

```
hbm-meta/
├── SKILL.md                    本文件：总编排 + 子技能加载规则
├── agent/                      主 Agent 运行时与编排规则
│   ├── routing.md              ★ 6 个子 Agent 路由表 + 权限原则（进入流程前必读）
│   ├── main-agent.md           编排人格 + 闸门职责
│   ├── orchestration.md        9 阶段流程图与状态转移
│   ├── state-schema.json       项目状态文件 schema（断点续跑）
│   └── decision-order.md       三级人工介入优先级
├── shared/                     ★ 跨技能共享资产（其他一切的依赖）
│   ├── data-contract.md        主表契约：字段定义、单位口径、快照指纹、project.yaml 规范
│   ├── quality-gates.md        GATE-0…GATE-5 六道人工闸门定义与状态机
│   ├── human-verification-protocol.md  AI 提取的强制人工复核机制
│   ├── reproducibility-rules.md 快照/版本/命名规范
│   └── glossary.md             中英术语表
├── agents/                     6 个子 Agent 定义（A1–A6）
├── skills/                     6 个可独立触发的子技能
│   ├── S1-search-strategy/
│   ├── S2-screening-quality/
│   ├── S3-extraction-standardization/
│   ├── S4-unit-statistic-conversion/
│   ├── S5-weighted-pooling-edi/
│   └── S6-qc-audit-revision/
│     每个子技能统一结构：SKILL.md + references/ + scripts/ + templates/
├── templates/
│   ├── project-scaffold/       项目目录骨架（01-search … 11-revision）
│   └── docs/                   6 份过程文档空模板
├── examples/                   脱敏范例
├── scripts/                    通用化 Python 脚本（去硬编码，CLI + --self-test）
├── .github/workflows/          CI：schema 校验 + 脚本自检 + frontmatter 校验
└── requirements.txt            Python 环境（单栈，无 R 依赖）
```

**目录职责一句话版**：`shared/` 放规则，`agent/` 放编排，`agents/` 放执行者，`skills/` 放知识，`templates/` 放脚手架，`examples/` 放范例，`scripts/` 放工具。

---

## 4. 九阶段主线

> **进入流程前，主 Agent 必须先读取 `agent/routing.md`**，获取 6 个子 Agent 的路由表与权限规则；本文件只负责阶段概览与子技能加载，具体调度、权限边界、stale 回滚与违规自检以 `agent/routing.md` 为准。

```
[0] 立项与口径冻结      → GATE-0  人工设定
[1] 检索                → GATE-1  人工确认检索充分性
[2] 筛选与质量评价      → GATE-2  【领域知识】人群边界裁决
[3] 提取与逐条校对      → GATE-3  【领域知识】数据真实性复核
[4] 单位与统计量标准化  → GATE-4  人工确认标准化口径
[5] 加权合并 + EDI
[6] 去重与敏感性分析    → GATE-5  【领域知识】去重与敏感性裁决
[7] 数值审计与成稿      → 贯穿闸门：数值审计
[8] 投稿与返修闭环      → 循环至全部意见闭环
```

| 阶段 | 负责 | 产出 | 闸门 |
|---|---|---|---|
| 0 | 人 + 主 Agent | `project.yaml`、`00_charter.md` | **GATE-0** |
| 1 | A1 search-agent | `检索记录.csv`、`检索策略.md` | **GATE-1** |
| 2 | A2 screening-agent | PRISMA 数字、`质量评分表`、`疑似排除清单` | **GATE-2** |
| 3 | A3 extraction-agent | 主表草案、`缺字段清单`、`逐条核对任务表` | **GATE-3** |
| 4 | A4 statistics-agent | `换算痕迹表`、冻结主表 + 快照指纹 | **GATE-4** |
| 5 | A4 statistics-agent | 分层加权 GM/GSD、EDI、图表 | — |
| 6 | A5 audit-agent | `敏感性报告`、`去重报告` | **GATE-5** |
| 7 | A5 audit-agent | `数值审计对照表` | 贯穿闸门 |
| 8 | A6 revision-agent | 6 份过程文档、修订稿、逐条回复 | 贯穿闸门 |

**贯穿机制**：`state.json` 记录阶段/闸门/快照版本/未决人工项；任何上游口径变更自动把所有下游产物标记为 `stale` 并强制重算。

---

## 5. 何时加载哪个子技能

> **加载规则**：主 Agent 依据下表按当前阶段加载对应子技能；用户直接说出触发词时，可单独跳载某一子技能（子技能之间通过数据契约解耦，允许中途进入）。

| 子技能 | 加载触发条件（用户说什么 / 处于哪个阶段） | 产出物 | 前置依赖 |
|---|---|---|---|
| **S1-search-strategy** | "设计检索式""检索策略""系统检索""文献检索""数据库检索""检索词""文献查阅策略""命中数""PRISMA identification" / 阶段 1 | `检索记录.csv`、`检索策略.md`、检索充分性诊断 | GATE-0 通过 |
| **S2-screening-quality** | "文献筛选""纳入排除标准""质量评价""偏倚分析""风险偏倚""AHRQ""纳入流程图""PRISMA 流程图""去重与纳入" / 阶段 2 | PRISMA 两阶段数字、`质量评分表`、三类边界人群疑似清单、同队列聚簇清单 | S1 产出 |
| **S3-extraction-standardization** | "数据提取""信息提取""文献提取""提取表""主表构建""数据录入""校对""复核""提取提示词" / 阶段 3 | 主表草案、`缺字段清单`、`可疑值清单`、`逐条核对任务表`、`校对日志.md` | GATE-2 通过 |
| **S4-unit-statistic-conversion** | "单位换算""浓度换算""单位统一""尿肌酐校正""比重校正""GM 转换""几何均值""中位数转均值""四分位数""Wan 公式""换算模板" / 阶段 4 | `换算痕迹表`、冻结主表（含 `gm_summary`/`unit_final`/`conversion_path`）、快照指纹 | GATE-3 通过 |
| **S5-weighted-pooling-edi** | "加权合并""加权几何均值""合并分析""描述性分析""分时段""分省份""分人群""趋势分析""EDI""估计每日摄入量""暴露评估" / 阶段 5 | 分层加权 GM/GSD 表、EDI 结果、趋势与空间图表 | GATE-4 通过 |
| **S6-qc-audit-revision** | "数值审计""稿件核对""对不上""数值不一致""重复计数""队列重复""去重筛查""敏感性分析""稳健性""DOI 核验""参考文献核对""引用错配""EndNote""审稿意见""返修""逐条回复" / 阶段 6–8 | `数值审计对照表`、`去重与纳入标准筛查报告`、`敏感性报告`、EndNote 修改清单、引用对照表、审稿进度清单 | 任一阶段均可进入 |

**跨技能引导**：若用户直接说"帮我做内暴露 meta 分析"而不指定阶段，主 Agent 从阶段 0 开始并逐步加载；若用户已有主表，直接从 S3/S4 进入；若用户已投稿，直接从 S6 进入。

**子技能独立性**：S1–S6 之间只通过 `shared/data-contract.md` 与 `project.yaml` 通信，不互相 import。因此可以只用其中一个（例如只想要 S6 的返修引擎），无需跑完整流水线。

---

## 6. 快速上手（使用者的第一步）

```bash
# 1. 复制项目脚手架到你的工作目录
cp -r templates/project-scaffold/ /path/to/my-meta-project/
cd /path/to/my-meta-project

# 2. 复制并填写项目配置（这是唯一的强制配置步骤）
cp templates/project.yaml ./project.yaml
#    必填：analyte / country / matrix_scope / period_scheme / region_map / unit_policy
#    可改：edi 参数、weighting 口径

# 3. 安装 Python 环境
pip install -r requirements.txt

# 4. 校验配置与契约一致性
python scripts/validate_table.py --config project.yaml --check-config-only

# 5. 跑 GATE-0（生成 charter + 冲突预判清单，然后停下来等人确认）
#    在 Agent 中说出："开始内暴露 meta 分析，暴露物是 PFOA"
```

**目录骨架**（`templates/project-scaffold/`）：
`01-search / 02-screening / 03-extraction / 04-standardization / 05-pooling-edi / 06-qc-audit / 07-references / 08-manuscript / 09-presentation / 10-reference-tools / 11-revision`

**最小可用路径**：如果你已有符合契约的主表草案，只想做 **S3 + S4 + S5**（提取校对 + 标准化 + 加权合并 + EDI），可以：

1. 复制 `templates/project-scaffold/` 到工作目录
2. 填写 `project.yaml`，把 `input.master_table` 指向你的表
3. 直接跳到上面第 4 步跑校验，然后加载 **S4** 子技能

**不需要跑 S1/S2 的检索与筛选**。若主表尚未逐条校对，先加载 **S3** 完成 GATE-3。

**最短检查清单**（开始前确认）
- [ ] 我的研究对象是**人体生物样本浓度**（不是效应量、不是环境样本浓度）
- [ ] 我接受"AI 只产疑似清单，排除/定稿由我裁决"
- [ ] 我能在 GATE-3 抽出时间做**逐条人工校对**（这是全流程最大的时间投入，也是最不可省的一步）
- [ ] 我理解"分期方案一旦冻结，改动要重算全流程"

---

## 7. 不可逾越的红线（IRON RULES）

1. **不推断未报告的数据**——`gm_summary`/`sample_size`/`time`/`sample_type` 禁止插补；缺即不可合并。
2. **正文不允许孤儿数字**——任何出现在稿件里的数字必须能追溯到"快照某一行 + 配置某一条"。
3. **闸门只由人开启**——任何子 Agent 不得把闸门状态从 `pending` 改为 `passed`。
4. **口径变更即全局失效**——改 `period_scheme` / `unit_policy` / 换算路径，必须 bump 版本并重算所有下游产物。
5. **决策顺序不可颠倒**——数据真实性 → 人群边界 → 合并合理性；任一级未通过，不进入下一级。
6. **敏感性分析未做，不得声明结论稳健**。

---

## 8. 相关文件

- `agent/routing.md` — 6 个子 Agent 的路由表与权限原则
- `shared/data-contract.md` — 主表契约（先读这个）
- `shared/quality-gates.md` — 六道人工闸门
- `agent/orchestration.md` — 9 阶段详细流程
- `agent/decision-order.md` — 三级人工介入优先级
