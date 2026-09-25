# routing.md — 子 Agent 路由表与权限原则

> **本文件是路由表与权限原则，进入流程前由主 Agent 主动读取。不命名为 AGENTS.md 是为了避免被 DSH 自动注入为常驻指令。**
>
> **适用**：HBM-Meta-Agent（`hbm-meta`）　**版本**：`1.0.0`　**配套**：`{skill_root}/SKILL.md`、`{skill_root}/shared/data-contract.md`、`{skill_root}/shared/quality-gates.md`

---

## 0. 角色分层

```
人类研究者（唯一裁决者）
  │  唯一可写 gate_status / inclusion_decision / 定稿数字
  ▼
主 Agent（hbm-meta）
  │  编排 9 阶段、加载子技能、维护 state.json、执行 stale 回滚、请求人工裁决
  ▼
6 个子 Agent（A1–A6）
  │  各自独立可复用；只通过 data-contract + project.yaml 通信，互不 import
  ▼
6 个子技能（S1–S6）—— 存知识；scripts/ 存工具
```

**关键设计**：子 Agent 是"执行者"，子技能是"知识库"，两者**多对多解耦**——A4 同时调用 S4 与 S5；S6 同时服务 A5 与 A6。因此子技能可单独使用，无需整套流水线。

---

## 1. 路由表

| ID | 子 Agent | 职责 | 调用 Skill | 工具权限 | 输出契约 |
|---|---|---|---|---|---|
| **A1** | `search-agent` | 构造数据库特异性检索式、执行检索、记录命中数、跨库去重、产出检索充分性诊断 | **S1** | `Read` `Write` `Grep` `Glob` `WebFetch` `WebSearch`<br>**无 `Edit`** | `01-search/检索记录.csv`（库/检索式原文/日期/命中数）<br>`01-search/检索策略.md`<br>检索充分性诊断（含排除词过度杀伤预警） |
| **A2** | `screening-agent` | 题摘筛 → 全文筛 → AHRQ 11 条质量评分 → 产出三类边界人群疑似清单 + 同队列聚簇清单 | **S2** | `Read` `Write` `Grep` `Glob`<br>**无 `Edit`** | `02-screening/` PRISMA 两阶段数字、`质量评分表.xlsx`、`疑似排除清单.md`、`同队列聚簇清单.md`<br>**不得写入** `inclusion_decision` 实体结论（只可写 `Pending`） |
| **A3** | `extraction-agent` | AI 初提数据 → 生成缺字段/可疑值清单与逐条核对任务表 → 在人工裁决后落库 | **S3**（+ **S4** 接口） | `Read` `Write` `Edit` `Grep` `Glob` `Bash`（限 `scripts/` 下工具，禁网络） | `03-extraction/{analyte}_{date}.xlsx` 主表草案<br>`缺字段清单`、`可疑值清单`、`逐条核对任务表`、`校对日志.md` |
| **A4** | `statistics-agent` | 单位与统计量标准化（三级流程）→ 冻结主表并生成快照指纹 → 分层加权 GM/GSD → EDI → 制图数据 | **S4** + **S5** | `Read` `Write` `Edit` `Grep` `Glob` `Bash`（执行 Python 脚本） | `04-standardization/换算痕迹表.xlsx`、冻结主表<br>`05-pooling-edi/` 分层结果表、EDI 结果、图表数据 |
| **A5** | `audit-agent` | 数值审计（手稿 vs 快照）、队列重复计数筛查、敏感性分析、DOI/Crossref 核验、引用对照 | **S6** | `Read` `Write` `Grep` `Glob` `Bash` `WebFetch`<br>**无 `Edit`** | `06-qc-audit/{02_数值审计对照表.md, 03_去重与纳入标准筛查报告.md, 敏感性报告.md}`<br>**只产出差异清单，不修改主表与稿件** |
| **A6** | `revision-agent` | 审稿意见分派与闭环、稿件数值同步、逐条回复撰写、参考文献库修改清单 | **S6** | `Read` `Write` `Edit` `Grep` `Glob` `WebFetch` | `11-revision/` 6 份过程文档 + 修订稿 + 逐条回复<br>审稿进度清单（每条意见的状态与负责人） |

### 路由决策树

```
用户请求
├─ 提到"检索式/检索策略/检索词/命中数"          → A1
├─ 提到"筛选/纳入排除/质量评价/偏倚/流程图"        → A2
├─ 提到"提取/主表/录入/校对/提取提示词"           → A3
├─ 提到"单位/GM 换算/校正/换算模板"               → A4（加载 S4）
├─ 提到"加权合并/分时段/趋势/EDI/暴露评估"        → A4（加载 S5）
├─ 提到"数值审计/去重/敏感性/DOI 核验/引用错配"    → A5
├─ 提到"审稿意见/返修/逐条回复/参考文献修改"       → A6
└─ 只说"帮我做内暴露 meta 分析"                  → 主 Agent 从阶段 0 起步，按阶段依次调度
```

**歧义处理**：若请求跨越 A2/A3（例如"筛完直接提数据"），主 Agent **必须先完成并过 GATE-2 再启动 A3**，不得并行跳过闸门。

---

## 2. 权限原则

### 2.1 写权限分级

| 级别 | Agent | 允许的操作 | 禁止的操作 |
|---|---|---|---|
| **L1 只读 + 建报告** | A1、A2、A5 | 读任何文件；在 `01-/02-/06-` 目录**新建**报告与清单 | 修改主表；修改稿件；修改 `project.yaml`；修改 `shared/` 下任何文件 |
| **L2 数据写** | A3、A4 | 新建/修改提取表与标准化表；执行 `scripts/` 下工具；生成快照 | 修改原始文献与原始数据；修改稿件正文；修改 `shared/`；改闸门状态 |
| **L3 文档写** | A6 | 修改稿件、回复信、参考文献过程文档 | 修改主表数据；修改 `shared/`；改闸门状态 |
| **L4 编排 + 闸门** | **主 Agent / 人类** | 写 `state.json`、`gates.json`、`inclusion_decision`；批准快照冻结 | — |

### 2.2 原始数据目录默认只读

`10-reference-tools/`（参考附件）、原始文献 PDF、原始导出表（数据库检索原始结果）**对所有子 Agent 只读**。需要修改时必须复制到对应工作目录后再改，保留原始件。

**理由**：原始件一旦被就地修改，检索可复现性即被破坏，且无法事后发现。

### 2.3 结论性动作必须经人工确认门

以下动作**AI 不得自行生效**，只能产出"建议 + 证据"，由人在闸门处裁决：

| 结论性动作 | 归属闸门 | AI 只能做什么 | 人做什么 |
|---|---|---|---|
| 排除某个研究 | GATE-2 | 列疑似清单 + 命中关键词 + 原文片段 | 逐条给 `Include`/`Exclude` + `exclusion_reason` |
| 判定某数值为错 | 数值审计 | 列出 `手稿原值 vs 重算值 vs 差值` | 采信重算值 / 修正换算 / 保留并注明 |
| 指定队列归属 | GATE-2 | 同城同基质聚簇 + 同作者聚类 | 指定 `cohort_id`、决定去重策略 |
| 判定敏感性结论 | GATE-5 | 三套分层结果 + 主形态一致性判定 | 批准哪些子集可用于结论 |
| 批准快照冻结 | GATE-4 | 完整性校验 + 换算痕迹 | 批准并出具 `snapshot_tag` |
| 定稿稿件数字 | 数值审计 | 全量对照表（`OK`/`MISMATCH`/`NO_SOURCE`） | 批准"全部 OK"后定稿 |

### 2.4 闸门权限铁律

> **任何子 Agent（A1–A6）都不得把闸门状态从 `pending` / `in_review` 改为 `passed`（或 `partial`）。**
> **唯一可写者是主 Agent 在人类明确裁决之后写入，或人类直接写入。**

**执行机制**
1. 子 Agent 的输出模板中不存在 `gate_status` 字段——它们只能提交"闸门输入包"（证据 + 建议 + 待决清单）。
2. `shared/quality-gates.md` 的 `_state/gates.json` 由主 Agent 独占写入。
3. 下游脚本启动时读取 `gates.json`；`status != passed` 且不属于被允许的 `partial` 情形 → **拒绝执行，退出码 2**。
4. 若检测到子 Agent 越权写入闸门状态 → 主 Agent 必须回滚该写入并在 `state.json` 记录 `VIOLATION` 事件。

### 2.5 网络与外部调用

- 仅 A1（WebSearch/WebFetch 用于检索与核验）、A5（WebFetch 用于 Crossref DOI 核验）、A6（WebFetch 用于期刊要求核对）可联网。
- A3、A4 **默认离线**：提取与计算必须只用本地数据，避免"从网上补一个看起来合理的值"。
- 联网 Agent **不得下载 PDF 全文后自动提取数据**——全文获取与解读须有人参与（版权与准确性双重原因）。

### 2.6 ★ 源码改动纪律（永久规则）

> 本节是**永久规则**，不是待办项。所有 Agent 在修改本工作包任何源码时必须遵守。
> 来源：一次真实事故的复盘（详见 `{skill_root}/STEP4-TODO.md` A1 区块的事故记录）。

**事故**：用 PowerShell 的 `Get-Content -Raw` + `Set-Content` 往返批量替换常量名时，
PowerShell 的 UTF-8 往返对源码做了**字符级替换**并加了 BOM：

| 正确 | 被改成 | 正确 | 被改成 |
|---|---|---|---|
| `check` | `chdck` | `rows` | `rogs` |
| `with` | `gith` | `weighted` | `geighted` |
| `name` | `namd` | `False` / `None` | `Falsd` / `Nond` |
| `def` / `edi` | `ddf` / `ddi` | `header` | `hdaddr` |

结果：损坏 3 个文件（`gen_templates.py`、`dedup_screen.py`、`sensitivity.py`），
其中 `gen_templates.py` 破坏到**无法逐字逆转**，只能依据其他文件中的正确实现**重建**。

**五条纪律（必须遵守）**

| # | 规则 |
|---|---|
| 1 | **禁止**用 PowerShell 的 `Get-Content -Raw` + `Set-Content` 往返改写源码文件 |
| 2 | 源码改动一律用**编辑工具**（`edit` / `write`），**不用 shell 文本替换** |
| 3 | shell 只用于**执行**与**只读检查**（运行脚本、grep、列目录） |
| 4 | 必须批处理时，用 **Python 全程读写**（`io.open(..., encoding="utf-8")`），**不经 PowerShell 管道** |
| 5 | 每次批量改动后**立即用 Python 做语法 + 探针扫描**（`compile()` + 损坏 token 正则） |

**批量改动的标准流程**

```python
# 1) 用 Python 读改写（不用 PowerShell）
import io
src = io.open(path, encoding="utf-8").read().lstrip("\ufeff")   # 去 BOM
src = src.replace(old, new)
io.open(path, "w", encoding="utf-8", newline="").write(src)

# 2) 立即验证
compile(src, path, "exec")                    # 语法
#    探针扫描：\b(chdck|gith|rogs|geighted|Falsd|Nond|ddf|ddi|negline|loger)\b
# 3) 跑该文件的 --self-test
```

**违反后果**：源码被静默破坏 → 自检可能仍通过（若损坏落在字符串里）→ 错误进入生产。
因此**纪律 5 不可省略**，且探针扫描必须覆盖**全部** `.py` / `.md` / `.csv` / `.yaml`。

**责任**：发现损坏后必须①立即停止批量操作 ②主动报告 ③用 `write` 工具重写受损文件
④明确区分"恢复"与"重建"（重建须说明依据）。**不得用 shell 修复 shell 造成的损坏。**

---

## 3. 主 Agent 编排逻辑

### 3.1 状态机

**状态文件**：`_state/state.json`（schema 见 `agent/state-schema.json`）

```json
{
  "project": {"name": "...", "analyte": "As", "country": "China"},
  "contract_version": "1.0.0",
  "config_version": "1.0.0",
  "current_stage": 4,
  "stages": {
    "0": {"status": "done", "artifacts": ["project.yaml", "00_charter.md"]},
    "1": {"status": "done", "artifacts": ["01-search/检索记录.csv"]},
    "2": {"status": "done", "decisions": 130},
    "3": {"status": "in_progress", "verified_ratio": 0.62},
    "4": {"status": "blocked", "blocked_by": "GATE-3"}
  },
  "gates": { "...": "见 gates.json" },
  "snapshot_tag": null,
  "stale_artifacts": [],
  "violations": []
}
```

**阶段状态**：`pending` / `in_progress` / `done` / `blocked` / `stale`

### 3.2 九阶段与五道闸门的对应

| 阶段 | 执行者 | 完成判据 | 闸门 | 闸门通过后方可 |
|---|---|---|---|---|
| 0 立项与口径冻结 | 主 Agent + 人 | `project.yaml` 六项必填齐全 + `period_scheme.freeze_date` 已设 | **GATE-0** | 启动 A1 |
| 1 检索 | A1 | 检索记录含 ≥4 库原文检索式 + 命中数 | **GATE-1** | 启动 A2 |
| 2 筛选与质量评价 | A2 | PRISMA 数字自洽 + 评分表完成 + 疑似清单产出 | **GATE-2** | 启动 A3 |
| 3 提取与逐条校对 | A3 | `validate_table.py` 无 ERROR + `verified=TRUE` ≥ 阈值 | **GATE-3** | 启动 A4 |
| 4 单位与统计量标准化 | A4 | 换算路径 100% 覆盖 + 无推断未标记记录 | **GATE-4** | 启动 S5 合并 |
| 5 加权合并 + EDI | A4 | 分层结果表 + EDI 结果 + 图表数据 | — | 启动 A5 |
| 6 去重与敏感性分析 | A5 | 去重报告 + 三套敏感性结果 | **GATE-5** | 允许声明稳健性 |
| 7 数值审计与成稿 | A5 → A6 | 数值审计对照表全部 `OK` | 贯穿闸门 | 定稿/投稿 |
| 8 投稿与返修闭环 | A6 | 全部审稿意见 `closed` | 贯穿闸门 | 结束 |

> 注：GATE-3 → GATE-4 之间允许 A4 先做**换算草案**（不冻结），但冻结与快照只能发生在 GATE-4 通过后。

### 3.3 决策顺序（不可颠倒）

```
① 数据真实性   → ② 人群边界   → ③ 合并合理性
   GATE-3          GATE-2         GATE-4 / GATE-5
```

**含义**：
- **① 数据真实性优先**：如果一条记录的 `gm_summary` 是错的或推断来的，讨论它该不该纳入毫无意义。
- **② 人群边界其次**：数据为真但人群不符合纳入标准 → 必须先裁决，再谈合并。
- **③ 合并合理性最后**：只有"真数据 + 对人群"才轮到讨论"能不能把两类研究放进同一个合并池"（如尿校正方式不同、血基质不同、暴露物形态不同）。

**执行约束**：主 Agent 在 `current_stage` 推进前必须校验此顺序；若检测到"人群边界未裁决（GATE-2 `pending`）却已开始计算合并结果"→ 阻断并报 `ORDER_VIOLATION`。

### 3.4 stale 回滚机制

**触发**：以下任一发生（均在 `project.yaml` 或契约中）
- `period_scheme` 边界变更
- `unit_policy` 目标单位变更
- `region_map` 变更
- `edi` 参数变更
- `weighting.method` 变更
- 主表新建快照（`snapshot_tag` 变化）
- 契约 `major` 版本变更

**动作序列**
1. `config_version` bump（minor 或 major）
2. 定位所有产物的快照指纹，凡指纹中的 `config_version` / `snapshot_sha256` 与当前不一致 → 写入 `stale_artifacts`
3. 受影响阶段状态置为 `stale`，其闸门状态置回 `in_review`
4. **通知人类**：列出失效产物清单 + 预计需重跑的脚本
5. 重跑顺序严格按阶段：`S4 → S5 → S6 → 稿件数值同步`
6. 重跑完成后产出**新旧对照表**（这是本项目血 EDI 公式纠错的做法），并保留在 `06-qc-audit/`

**硬约束**：stale 产物**不得**用于投稿、不得引用其数字。主 Agent 在生成任何交付物前必须检查 `stale_artifacts` 为空。

### 3.5 人工介入请求规范

主 Agent 触发闸门时必须输出**结构化待决包**（而非笼统"请确认"）：

```markdown
## GATE-2 待裁决包（第 1 批，共 130 项）

### 待决项 1/130
- record_id: R00142 / study_no: S038
- 类型: 疑似职业暴露
- AI 证据: 标题含「冶炼厂工人」; 摘要未出现 non-occupational 否定词
- 关键原文片段: "...workers from a smelting plant..."
- 影响的记录数: 3 条（Urine ×2, Blood ×1）
- 请裁决: [ ] Include  [ ] Exclude（请填 exclusion_reason）  [ ] 需查原文
```

**要求**：每项必须带 `record_id`、AI 证据、原文片段、影响范围、明确的选项。人只需勾选与填理由，无需自己去找证据。

---

## 4. 子 Agent 之间的交接契约

| 交接 | 上游产物 | 下游读取方式 | 校验 |
|---|---|---|---|
| A1 → A2 | `01-search/检索记录.csv` + 原始导出表 | 去重后进入题摘筛 | 命中数与标题数一致 |
| A2 → A3 | 纳入研究清单 + `质量评分表` | 只对 `inclusion_decision=Include` 的研究提取 | 无 `Pending` 项 |
| A3 → A4 | 主表草案（`verified` 标记齐全） | 校验后换算 | `validate_table.py` 无 ERROR |
| A4 → A5 | 冻结主表 + 快照指纹 + 分层结果 | 审计与敏感性基于同一快照 | 快照哈希一致 |
| A5 → A6 | 数值审计对照表 + 差异清单 | 稿件数值同步与回复撰写 | 无 `MISMATCH` / `NO_SOURCE` |
| A6 → A5 | 返修引发的口径变更请求 | **不得直接改数据**，须走 GATE 重开流程 | 触发 stale 回滚 |

**核心约束**：任何跨 Agent 的数据传递都通过**文件**（而非会话记忆），且文件必须带快照指纹。这保证任一子 Agent 可被单独重启而不丢失上下文。

---

## 5. 违规检测与自检

主 Agent 在每个阶段结束时执行以下自检（`scripts/` 提供实现）：

| 检查 ID | 检查内容 | 违规后果 |
|---|---|---|
| `V-01` | 子 Agent 是否写了 `gate_status` | 回滚 + 记 `VIOLATION` |
| `V-02` | 是否存在无来源的正文数字（`NO_SOURCE`） | 阻断定稿 |
| `V-03` | 是否有推断值未登记 `inferred_flags` | 阻断 GATE-4 |
| `V-04` | 是否有 `gm_summary`/`sample_size`/`time`/`sample_type` 来自插补 | **严重违规**，阻断全流程并需人工复核 |
| `V-05` | 阶段推进是否违反决策顺序（①②③） | 阻断 + 报 `ORDER_VIOLATION` |
| `V-06` | 是否存在 stale 产物被引用 | 阻断交付 |
| `V-07` | `verified` 比例是否低于阈值即进入 S5 | 阻断 S5 |
| `V-08` | 原始数据目录是否被就地修改（对比只读快照哈希） | 记 `VIOLATION`，需从备份恢复 |

---

## 6. 快速参考：一句话权限记忆法

- **A1 找**、**A2 筛**、**A3 抄**（抄进表）、**A4 算**、**A5 查**、**A6 改稿**
- **只有抄表（A3）、算数（A4）、改稿（A6）能动笔写内容**；其余只出报告。
- **谁都不能自己开门（闸门）**——门钥匙在人手里。

---

**维护者**：HBM-Meta-Agent　**变更**：修改权限原则须走 GitHub Issue 并 bump 版本
