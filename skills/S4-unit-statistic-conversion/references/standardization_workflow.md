# 标准化工作流（Standardization Workflow）

> **用途**：S4 的端到端操作流程、字段填写责任、GATE-4 出口条件。
> **配套**：`unit_conversion_playbook.md`（换算规则）、`wan2014_formulas.md`（公式）、`icrp89_urine_reference.md`（参数）

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **前置闸门** | **GATE-3 必须为 `passed`**（数据真实性已复核） |
| 前置产物 | `03-extraction/{analyte}_{date}.xlsx` 主表（含 `initial_*`、`unit_original`、`sample_size`）、`校验报告`、`校对日志` |
| 前置校验 | `validate_table.py` 无 `ERROR`；`verified` 比例 ≥ 90% |
| 负责人 | **A4 `statistics-agent`**（权限 L2） |
| 产出闸门 | **GATE-4**（标准化口径确认）——由**人工**开启并批准快照冻结 |
| 可独立使用 | 是。S4 只依赖主表与 `project.yaml`，不依赖 S1/S2 |

**拒绝启动的信号**：
- GATE-3 ≠ `passed`
- 主表存在 `stat_type` 为空或 `unit_original` 为空的记录
- `project.yaml → unit_policy` 缺失，或 `standardization` 区块缺失（GATE-0 未完成的标志）
- `project.yaml` 文件不存在 → **硬错误，退出码 2**（禁止兜底，见 `STEP4-TODO.md` A1）

---

## 2. 输入与输出契约

### 2.1 输入字段（来自 S3，`data-contract.md` §3.5–3.6）

| # | 字段 | S4 如何使用 |
|---|---|---|
| 39 | `sample_type` | 决定目标单位与是否走尿校正 |
| 40 | `blood_matrix` | 决定血样可否与全血合并 |
| 41 | `flag_blood_matrix` | 存疑标记，进分层 |
| 43 | `analyte` | 决定摩尔质量表条目 |
| 44 | `analyte_species` | 决定是否有形态假设 |
| 46 | `stat_type` | ★ 决定换算路径（首要分支） |
| 47–57 | `initial_gm` … `initial_gsd` | ★ 公式的输入值 |
| 58 | `unit_original` | ★ 单位换算的输入 |
| 59 | `sample_size` | 加权与 Wan 公式所需 |
| 10 | `time` | 分期（S5 用，S4 仅继承） |
| 21 | `population_group` / 32 `gender` | ★ ICRP 89 参数选择 |
| 67 | `notes` | 继承到输出 |

### 2.2 输出字段（S4 填写，`data-contract.md` §3.7）

| # | 字段 | 填写规则 |
|---|---|---|
| 60 | `adjusted` | 尿样必填：`No` / `Creatinine` / `SpecificGravity` / `ReferenceConversion` / `NotApplicable` |
| 61 | `unit_final` | 由 `unit_policy` 决定（尿 → `ug/g Cr`；血/脐血 → `ug/L`） |
| 62 | `gm_summary` | ★ 最终合并用值 |
| 63 | `gsd_summary` | 有则可算，用于误差线（不作权重） |
| 64 | `conversion_path` | ★ 结构化编码（见 `unit_conversion_playbook.md` §6.1） |
| 65 | `conversion_params` | 全部参数留痕（公式编号、变体、ICRP 参数、MW…） |
| 66 | `inferred_flags` | ★ 由 `conversion_path` **自动登记**（见 §5） |
| （追加） | `notes` | S4 发现的问题追加，**不覆盖 S3 的原记录** |
| 72 | `row_hash` | 脚本重算，用于与 S3 输入做 diff |

**S4 不改动的字段**：§3.1–3.6 全部（来源、时空、人群、标记、基质、原文统计量），仅可追加 `notes`。

### 2.3 交付产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 标准化主表 | `04-standardization/{analyte}_{date}_standardized.xlsx` | 冻结前为草案 |
| 换算痕迹表 | `04-standardization/conversion_trace.csv` | 每条记录的完整换算链 |
| 标记反推表 | `04-standardization/flags_derivation.csv` | ★ `inferred_flags` 的可审计推导 |
| 换算路径分布 | `04-standardization/换算路径分布.md` | 各路径记录数统计 |
| 尿校正状态分布 | `04-standardization/尿校正分布.md` | 供 GATE-4 判断分层可行性 |
| 标准化报告 | `04-standardization/standardization_report.md` | 用 `templates/standardization_report.md` |
| 快照登记 | `_state/snapshots.csv` | GATE-4 通过后追加一行 |

---

## 3. 七步流程

### 步骤 1 · 预检与配置校验

```
① 读 project.yaml，确认必需区块存在：
   unit_policy / standardization（median_to_gm_strategy, wan_variant, creatinine_path_mode）
   / urine_reference / period_scheme
② 缺失或不可解析 → 硬错误，退出码 2，不产出任何文件
③ 读主表，确认必需列存在且 stat_type / unit_original / sample_size 无空值
④ 输出预检报告：记录数、按 stat_type 的分布、按 unit_original 的分布
```

**人工确认点**：预检报告中的 `unit_original` 分布是否符合预期（是否有陌生单位）。

### 步骤 2 · 第①级：统一暴露指标

```
对每条记录：
  ① 确认 analyte 一致（跨物质记录不允许同池）
  ② 检查 analyte_species：Total 与 iAs / 形态之和不可混池
     └─ 不可混池时 → 标记为分层变量，不剔除（裁决权在 GATE-4/5）
  ③ 检查 sample_type 与 blood_matrix：
     ├─ Blood + WholeBlood → 可合并
     ├─ Blood + Serum/Plasma → 保留但必须分层，并保留 flag_blood_matrix
     └─ Unspecified → 视为 WholeBlood 并登记 blood_matrix_assumed（在 flags 反推表中体现）
```

**不通过即停**：若同一 analyte 下出现无法归类的基质 → 停下交人工。

### 步骤 3 · 第②级：统一浓度单位

```
① 单位字符串归一化（μ/µ/u 统一；大小写不敏感）→ 仅内部使用，
   unit_original 保持原样
② 质量单位换算（见 playbook §2）
③ 摩尔单位换算（见 playbook §3）→ 需已知摩尔质量，否则 FAILED
④ 尿校正（见 playbook §4 + icrp89 决策树）
   ├─ 目标 ug/g Cr：
   │   ├─ 已校正 → reported_creatinine_corrected，不换算
   │   ├─ ug/L 未校正 → 用 ICRP 89 换算（登记 unit_converted_creatinine）
   │   └─ 比重校正 → 保留，进分层，不套 ICRP
   └─ 目标 ug/L：反向换算（登记 unit_converted_volume）
⑤ 逐条写入 conversion_path 的单位段
```

### 步骤 4 · 第③级：统一为 GM

```
按 stat_type 分支（见 wan2014_formulas.md §5 决策树）：
  GM_GSD / GM_only → SKIP 估算，直接取 initial_gm（path = reported_GM，零推断）
  AM_SD            → AMSD_to_LN（对数正态参数转换）
  Median_IQR       → Median_as_GM  或  Wan_S4/Wan_S7 → GM（按配置策略）
  Median_Range     → Wan_S2 → GM
  Min_Max          → Wan_S1（有均值）或 Wan_S3（无 n）
  P25_P75          → 视同 IQR 处理（Wan_S6 或 Wan_S4）
  Median_only      → FAILED（无离散度）
  其他             → 交人工
逐条写入 conversion_path 的统计量段，输出 gm_summary / gsd_summary
```

### 步骤 5 · 自动登记 `inferred_flags` 并生成反推表

```
① 解析每条 conversion_path → 按 playbook §6.2 规则表登记标记
② 产出 flags_derivation.csv：
   record_id | conversion_path | 登记标记 | 命中规则 | 依据字段
③ 校验：reported_GM 的记录 flags 必须为空；非空则报 ERROR
④ 校验：flags 值必须都在封闭字典内；越界报 ERROR
```

### 步骤 6 · 自动校验与统计

```
① 重跑 validate_table.py（此时 unit_final / gm_summary / conversion_path 已有值）
② 输出换算路径分布、尿校正分布、状态统计（OK/SKIP/FAILED/WARN）
③ 量级合理性检查：换算后值是否落在该基质的已知区间
   （尿 μg/g Cr 常见 1–100；血 μg/L 常见 0.1–10）
④ 计算 row_hash，与 S3 输入做 diff（检测是否有记录被意外改动）
```

### 步骤 7 · 提交 GATE-4

产出**结构化待裁决包**（格式见 `agent/routing.md` §3.5），包含：

| 待决项 | 内容 |
|---|---|
| 口径确认 | 尿校正优先规则、血基质假定、形态假定、路径选择策略 |
| 异常清单 | 所有 `FAILED` 与 `WARN` 记录 + 原因 |
| 分层可行性 | 哪些分层（校正方式 / GM 来源）样本量足够做敏感性分析 |
| 冻结请求 | 批准后生成 `snapshot_tag` 与 `sha256` |

**AI 不得自行开启 GATE-4**；批准后主表冻结。

---

## 4. 判断规则（核心决策点）

| # | 规则 | 说明 |
|---|---|---|
| R1 | **先单位后统计量，顺序不可颠倒** | 三级流程强制 |
| R2 | **对 `GM_GSD`/`GM_only` 记录禁止套 Wan** | `wan_convert.py` 硬拒绝并返回 `SKIP` |
| R3 | **`unit_original` 永不被覆盖** | 原样保留；规范化仅内部使用 |
| R4 | **成人性别不明用通用参数** | 不得默认男性（会低估约 10%） |
| R5 | **血/脐血不走尿校正** | ICRP 表仅适用尿样 |
| R6 | **`μg/g` ≠ `μg/g Cr`** | 前者是组织基准，不可互换 |
| R7 | **`inferred_flags` 只能由脚本登记 + 人工复核** | 手工修改必须留痕 |
| R8 | **`reported_GM` 记录 flags 必须为空** | 零推断子集的完整性前提 |
| R9 | **`FAILED` 记录不得进入合并** | S5 必须跳过并计数 |
| R10 | **换算不明即停** | 任何无法归类的单位/统计量 → 交人工，不得猜测 |

---

## 5. 状态处置与 `inferred_flags` 唯一来源规则

### 5.1 `FAILED` / `SKIP` / `WARN` 的处置

| 状态 | 含义 | 处置 |
|---|---|---|
| `OK` | 换算成功 | 进入合并池 |
| `SKIP` | 无需换算（`reported_GM`、已肌酐校正） | 进入合并池，`flags` 为空 |
| `FAILED` | 无法换算 | ★ **不得进入合并**；进 `FAILED` 清单交 GATE-4；计数必须写入结果表与局限说明 |
| `WARN` | 换算成功但存疑 | 进入合并池；进 `WARN` 清单；可作为敏感性分析的剔除候选 |

**必查的 `FAILED` 三类**：
1. 只有中位数、无离散度信息 → 该记录本质上不可合并
2. 分位/极值逻辑矛盾 → 数据可疑，回 GATE-3
3. `η ≤ 0` 或中心值 ≤ 0 → 公式退化

### 5.2 ★ `inferred_flags` 唯一来源规则

> **权威来源**：`conversion_path → derive_flags()` 的反推结果。
> 脚本写主表 `inferred_flags` 字段时**必须**走 `derive_flags()`；
> `convert_record()` 返回的中间 `res.flags` **仅用于自检断言，不得直接写入主表**。

| 角色 | 允许 | 禁止 |
|---|---|---|
| `derive_flags(conversion_path)` | ✅ 唯一写入主表 `inferred_flags` 的来源 | — |
| `convert_record().flags` | ✅ `--self-test` 的一致性断言 | ❌ 写入主表 |
| 人工（GATE-4） | ✅ 增删标记，但须留痕于 `notes` + `conversion_params` | ❌ 无痕修改 |

**执行机制（实现于 `wan_convert.py::main`）**
1. 逐条调用 `derive_flags(res.path)`，结果写入主表 `inferred_flags`
2. 断言 `set(res.flags) == set(derive_flags(res.path))`；不一致 → 记录 `FLAG_MISMATCH` 至 `wan_message`，并使**退出码非零**
3. `flags_derivation.csv` 与主表**同源**生成（同一函数），保证两者在构造上不可能分叉

**为什么必须如此**：若主表用内部标记、审计表用反推结果，两者可能静默分叉——审计链断裂且无法察觉。唯一来源规则使"主表字段"与"审计凭证"在构造上一致。

**实证价值**：该断言在开发期捕获一处真实缺陷——规则表 `Wan_S6` 项曾漏记 `gm_from_median`（S6 的中心值即中位数）。若无此断言，该缺失会静默流入所有 S6 记录（缺 n 的 IQR 数据），导致敏感性分层定义出错。

---

## 6. 与其他 Skill 的接口

### 6.1 上游：S3 → S4

依赖 S3 的交付承诺（见 `S3/SKILL.md` §7.2）：`stat_type` 与 `initial_*` 对位、`unit_original` 与 `sample_size` 非空、S4 字段留空。

**若发现 S3 承诺被违反**（如标 `GM_only` 但 `initial_gm` 为空）：
→ **不得自行修正**，回报 S3 走 GATE-3 重开流程，触发 stale 回滚。

### 6.2 下游：S4 → S5 交接字段表

| # | 字段 | S5 用途 |
|---|---|---|
| 39 | `sample_type` | 分层维度 |
| 59 | `sample_size` | ★ 权重 |
| 62 | `gm_summary` | ★ 合并输入 |
| 63 | `gsd_summary` | 误差线 |
| 61 | `unit_final` | 结果表单位标注 |
| 60 | `adjusted` | ★ 敏感性分析分层 |
| 64 | `conversion_path` | 敏感性分析分层（GM 来源） |
| 66 | `inferred_flags` | ★ "仅直接报告 GM"子集筛选取依据 |
| 10 | `time` + 14 `province` + 17 `region` | ★ 分期与地区分组 |
| 21 | `population_group` | ★ 人群分组 + EDI 取参 |
| 32 | `gender` | 分性别分析 |
| 40 | `blood_matrix` + 41 `flag_blood_matrix` | 分层 |
| 43 | `analyte_species` | 分层与可比性声明 |
| 33–37 | 边界风险标记 | 去重与敏感性 |

### 6.3 下游：S4 → S6

| 产出 | S6 用途 |
|---|---|
| `conversion_trace.csv` | 数值审计的证据链 |
| `flags_derivation.csv` | 推断标记的可审计性复核 |
| 快照指纹 | 判定 `STALE_MANUSCRIPT` |
| `FAILED` / `WARN` 清单 | 局限说明与敏感性分析 |

---

## 7. GATE-4 出口条件

- [ ] `project.yaml` 存在且必需区块完整（**无兜底**）
- [ ] 所有记录的 `conversion_path` 非空（`FAILED` 的也要有失败原因）
- [ ] 所有记录的 `unit_final` 等于 `unit_policy` 对应基质目标单位
- [ ] `inferred_flags` 全部由脚本登记，`flags_derivation.csv` 已产出
- [ ] `reported_GM` 记录的 `flags` 为空（零推断子集完整）
- [ ] `validate_table.py` 无 `ERROR`
- [ ] `FAILED` / `WARN` 清单已产出并提交人工
- [ ] 换算后量级合理性检查通过（或无异常）
- [ ] 快照指纹已生成（`sha256` + `generated_at` + `config_version`）
- [ ] **人工批准**（AI 不得自行开启 GATE-4）
- [ ] 批准后写入 `_state/snapshots.csv` 与 `_state/gates.json`

---

## 8. 常见工作流错误

| 错误 | 后果 | 防范 |
|---|---|---|
| 在 S4 顺手修正 S3 的 `stat_type` 错位 | 审计链断裂，责任不清 | R10，回报 S3 重开 GATE-3 |
| 边换算边合并（跳过 GATE-4） | 口径未经人确认，返修时才发现系统性过时 | 步 7 强制停下 |
| 冻结前反复改换算脚本 | 快照与结果不一致 | 冻结后改脚本必须 bump 版本并重算 |
| `FAILED` 记录被静默丢弃 | 选择性报告，严重学术问题 | R9：必须计数并写入局限 |
| 手工改 `inferred_flags` 不留痕 | 无法复核 | R7 + `flags_derivation.csv` |
| 分区口径变更后未重算 | 与"分期不一致"事故同源 | 触发 stale 回滚 |

---

**维护者**：HBM-Meta-Agent（S4）　**关联**：`unit_conversion_playbook.md`、`wan2014_formulas.md`、`icrp89_urine_reference.md`、`{skill_root}/STEP4-TODO.md`
