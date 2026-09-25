# 单位换算操作手册（Unit Conversion Playbook）

> **用途**：S4 阶段把来源各异、口径不一的浓度数据，统一到 `project.yaml → unit_policy` 指定的目标单位。
> **配套脚本**：`scripts/convert_units.py`
> **保真要求**：本文件定义的换算系数与路径编码必须与脚本实现一一对应；改动需版本变更。

---

## 1. 三级标准化流程（不可跳步）

这是本工作包对"标准化"的完整定义。**顺序不可颠倒，每一步都要留痕。**

```
第 ① 级  统一暴露指标（analyte / species / 基质）
   ├─ analyte：确认是同一物质（As / Cd / PFOA / …）
   ├─ analyte_species：总砷 or 无机砷 or 形态之和？不可混池
   └─ 基质：血 → 全血/血清/血浆不可混；尿 → 校正口径必须明确
        ↓ 只有 ① 完成，才谈得上比较
第 ② 级  统一浓度单位（质量浓度 + 尿校正）
   ├─ 质量单位：ng/mL → μg/L → mg/L 归一
   ├─ 摩尔单位：nmol/L → μg/L（需摩尔质量）
   └─ 尿校正：μg/L ↔ μg/g Cr（见 icrp89_urine_reference.md）
        ↓ 只有 ② 完成，不同研究才"可比"
第 ③ 级  统一为几何均值（GM）
   ├─ 已报告 GM → 直接取用
   ├─ 报告 AM+SD → 用对数正态关系转 GM（§5.1）
   └─ 报告中位数/IQR/极值 → 用 Wan 2014 公式（见 wan2014_formulas.md）
```

**违反顺序的典型错误**：先算 GM 再换单位 → 若换算是线性缩放，GM 会同步缩放（结果看似正确），但**只要涉及非线性步骤（如对数正态参数转换与单位换算混在一起）就会错**。因此统一坚持"先单位后统计量"。

**目标单位**（由 `project.yaml → unit_policy` 决定，默认）：
| 基质 | 目标单位 |
|---|---|
| 尿 | `ug/g Cr` |
| 血 / 脐血 | `ug/L` |
| 母乳 | 由配置决定（常用 `ug/L` 或 `ug/g fat`） |
| 指甲 / 头发 | 由配置决定（常用 `ug/g`） |

---

## 2. 质量单位换算表

| 单位 | 等价换算 | 备注 |
|---|---|---|
| `μg/L` = `ug/L` | 基准 | 1 μg/L = 1 ng/mL |
| `ng/mL` | `= μg/L` | 数值不变 |
| `mg/L` | `= 1000 μg/L` | 数值 ×1000 |
| `μg/dL` | `= 10 μg/L` | 数值 ×10 |
| `ng/L` | `= 0.001 μg/L` | 数值 ÷1000 |
| `pg/mL` | `= 0.001 μg/L` | 数值 ÷1000 |
| `μg/g` | 与 `μg/g Cr` **不同**！ | 前者是组织干重/湿重基准，后者是肌酐基准；不可互换 |
| `μg/g Cr` | `= μg/g creatinine` | 尿校正单位 |
| `mg/g Cr` | `= 1000 μg/g Cr` | 数值 ×1000 |

**字符归一化**：`μ` / `µ` / `u` 视为同一微符号；`μG` / `ug` 大小写不敏感。归一化**不改变单位语义**，只解决抄录差异（`unit_original` 仍保留原文）。

**⚠️ 高频陷阱**：`μg/g`（干重基准）常被误当作 `μg/g Cr`。判定线索：原文若提到"肌酐"或单位写全 `μg/g creatinine` 才是校正值；只说"μg/g"通常是组织基准。

---

## 3. 摩尔单位换算（nmol/L → μg/L）

```
浓度(μg/L) = 浓度(nmol/L) × 摩尔质量(g/mol) ÷ 1000
```

**常用摩尔质量表**（`g/mol`，按元素或最常报告的形态）

| 分析物 | 报告形态 | 摩尔质量 | 说明 |
|---|---|---|---|
| As | 元素砷 | 74.92 | 多数研究报"总砷"，用元素质量 |
| As | 三氧化二砷 As₂O₃ | 197.84 | 罕见；报告为"As₂O₃"时必须换算为 As 当量 |
| Cd | 元素镉 | 112.41 | |
| Cr | 元素铬 | 52.00 | |
| Pb | 元素铅 | 207.20 | |
| Hg | 元素汞 | 200.59 | 总汞 |
| MeHg | 甲基汞 | 215.63 | 注意与总汞区分 |
| Ni | 元素镍 | 58.69 | |
| Ti | 元素钛 | 47.87 | |
| V | 元素钒 | 50.94 | |
| PFOA | C₈HF₁₅O₂ | 414.07 | 换暴露物时在此扩展 |
| PFOS | C₈HF₁₇O₃S | 500.13 | 同上 |

**硬约束**：
1. 摩尔换算**必须**指定形态；`As₂O₃` 等氧化物必须先转为元素当量再入表。
2. 摩尔换算会**引入形态假设**，必须在 `inferred_flags` 登记 `molar_conversion`（并写入 `conversion_params` 的 `MW`）。
3. 若原文用了不同的形态基准（如报 `As₂O₃` 而他人报 `As`），**优先统一到元素当量**，并在 `notes` 说明。

---

## 4. 尿校正换算

### 4.1 正换算：`μg/L` → `μg/g Cr`

```
浓度(μg/g Cr) = 浓度(μg/L) ÷ CC(g/L)
```
或等价式（更易追溯排泄量来源）：
```
浓度(μg/g Cr) = 浓度(μg/L) × V(L/day) ÷ CE(g/day)
```

`CC` / `V` / `CE` 的取值按 `population_group` 与 `sex` 从 `icrp89_urine_reference.md` 决策树取。

### 4.2 逆换算：`μg/g Cr` → `μg/L`

```
浓度(μg/L) = 浓度(μg/g Cr) × CC(g/L)
```

**使用场景**：为了与"未校正"研究的记录做比较，或按 `project.yaml` 指定保留 `μg/L`。逆换算会登记 `unit_converted_volume`。

### 4.3 不得换算的情形

| 情形 | 处理 |
|---|---|
| 原文用**比重校正** | 保留原值，`adjusted = SpecificGravity`，**单独分层**，不套 ICRP |
| 原文**已肌酐校正** | 不换算，`conversion_path = reported_creatinine_corrected` |
| 基质是血/脐血 | 本表**不适用** |
| 原文单位是 `μg/g`（组织干重） | 不可换算为 `μg/g Cr` |

---

## 5. 统计量 → GM

### 5.1 `AM + SD` → `GM + GSD`（对数正态假设）

```
CV  = SD / AM
σ   = sqrt( ln(1 + CV²) )
GM  = AM / exp(σ² / 2)
GSD = exp(σ)
```

**前提**：`AM > 0`、`SD ≥ 0`、浓度近似对数正态。
**失效**：`AM ≤ 0` 或 `CV` 极大（> 2）时估算不稳 → `WARN` + 交人工。

### 5.2 `GM + GSD`（原文直接报告）

直接取用。`conversion_path = reported_GM`，**不登记任何推断标记**（这是唯一"零推断"的情形）。

### 5.3 `Median + IQR/Range` → GM

两条路径（详见 `wan2014_formulas.md` §6）：
- 直接：`Median_as_GM`（对数正态下中位数 = GM）
- 经 Wan：`Wan_S*→GM`

### 5.4 路径选择必须全项目统一

| 选择 | 由谁定 | 写在哪 |
|---|---|---|
| 中位数是否直接当 GM | GATE-0 冻结 | `project.yaml → standardization.median_to_gm_strategy` |
| Wan 用减号还是加号变体 | 同上 | `project.yaml → standardization.wan_variant` |
| 尿校正用 CC 直除还是 CE/V | 同上 | `project.yaml → standardization.creatinine_path_mode` |

---

## 6. ★ `inferred_flags` 自动登记逻辑（从 `conversion_path` 反推）

**设计目标**：登记过程**可审计**——任何人看到 `conversion_path` 就能推出应登记哪些标记，且脚本的推导规则是确定性的。

### 6.1 `conversion_path` 的编码规范

采用**分段式、符号可解析**的编码：

```
<来源统计量>→<步骤1>→<步骤2>→…→<目标量>
```

| 段类型 | 取值示例 |
|---|---|
| 来源统计量 | `AM_SD` / `GM_GSD` / `GM_only` / `Median_IQR` / `Median_Range` / `Median_only` / `Min_Max` / `P25_P75` |
| 单位步骤 | `ng/mL→ug/L` / `nmol/L→ug/L` / `ug/L→ICRP89_Adults→ug/g Cr` / `ug/L→ICRP89_Pregnant_CEV→ug/g Cr` |
| 统计量步骤 | `Wan_S1` / `Wan_S2` / `Wan_S3` / `Wan_S4` / `Wan_S5` / `Wan_S6` / `Wan_S7` / `AMSD_to_LN` / `Median_as_GM` |
| 终止段 | `GM` |
| 特殊情况 | `reported_GM` / `reported_creatinine_corrected` / `specific_gravity_reported` / `uncorrected_kept` |

**完整示例**
```
AM_SD→AMSD_to_LN→GM
Median_IQR→Wan_S4→ug/L→ICRP89_Adults→ug/g Cr→GM
Median_IQR→Median_as_GM→ug/L→ICRP89_Pregnant→ug/g Cr
nmol/L→ug/L→ug/L→ICRP89_Adults→ug/g Cr→GM
reported_GM
```

### 6.2 反推规则表（脚本实现的权威规则）

| `conversion_path` 片段命中 | 自动登记的标记 | 理由 |
|---|---|---|
| `Wan_S1` 或 `Wan_S5`（中心值为均值/范围） | `gm_from_mean_sd` | 由均值+范围估 SD 再转 GM |
| `Wan_S2`（中心值为中位数 + 范围） | `gm_from_range` | 由极值范围估算 |
| `Wan_S3`（无 n + 范围） | `gm_from_range`; `no_n_fallback` | 极值范围 + n 缺失 |
| `Wan_S4` 或 `Wan_S7`（中位数 + IQR + n） | `gm_from_iqr` | 由四分位距估算 |
| `Wan_S6`（无 n + IQR） | `gm_from_median`; `gm_from_iqr`; `no_n_fallback` | 四分位距 + n 缺失；中心值为中位数 |
| `AMSD_to_LN` 或 `AM_SD→…→GM` | `gm_from_mean_sd` | 算术量转对数正态参数 |
| `Median_as_GM` 或 `gm_from_median` 步骤 | `gm_from_median` | 中位数直接作为 GM |
| `sd_from_iqr` 步骤 | `sd_from_iqr` | IQR 反算 SD |
| `ICRP89` 或 `unit_converted` + 目标为 `ug/g Cr` | `unit_converted_creatinine` | 用参考值换算尿校正 |
| 目标为 `ug/L` 且来源为 `ug/g Cr` | `unit_converted_volume` | 反向换算 |
| `nmol/L→ug/L` 或含 `MW=` | `molar_conversion` | 摩尔换算引入形态假设 |
| `time_represented` 步骤 | `time_represented` | 多年区间取代表年（由 S3 记录，S4 继承） |
| `blood_matrix_assumed` | `blood_matrix_assumed` | 血基质假定（由 S3 继承） |
| `species_total_assumed` | `species_total_assumed` | 形态假定（由 S3 继承） |
| `specific_gravity_reported` | （无） | 保留原口径，但进校正方式分层 |
| `uncorrected_kept` | （无） | 同上 |
| `reported_GM` | **（无）** | 零推断；进"仅直接报告 GM"子集 |

### 6.3 ★ 唯一来源规则（Single Source of Truth）

> **`inferred_flags` 的权威来源是 `conversion_path → derive_flags()` 的反推结果。**

| 角色 | 允许做什么 | 禁止做什么 |
|---|---|---|
| `derive_flags(conversion_path)` | **权威来源**：脚本写主表 `inferred_flags` 字段时只能用它 | — |
| `convert_record()` 的中间 `res.flags` | 仅供 `--self-test` 的"路径与标记一致性"断言 | ❌ **不得直接写入主表** |
| 人工（GATE-4） | 增删标记，但必须留痕于 `notes` + `conversion_params` | ❌ 无痕修改 |

**执行机制**：
1. 脚本 `main()` 写主表时调用 `derive_flags(res.path)`，把结果写入 `inferred_flags`
2. 同时断言 `set(res.flags) == set(derive_flags(res.path))`；不一致 → 记录 `FLAG_MISMATCH` 到 `wan_message`，并使退出码非零
3. 反推表 `flags_derivation.csv` 从同一来源生成，保证主表与审计表**同源**

**为什么必须如此**：若主表用 `res.flags`、审计表用 `derive_flags()`，两者可能静默分叉，审计链断裂且无法察觉。唯一来源规则使"主表字段"与"审计凭证"在构造上不可能不一致。

**实证价值**：该断言在开发期已捕获一处真实缺陷——规则表 `Wan_S6` 项曾漏记 `gm_from_median`（S6 的中心值即中位数）。若无此断言，该缺失会静默流入所有 S6 记录。

### 6.4 可审计性要求（硬性）

1. **确定性**：同一 `conversion_path` 必然产出同一标记集合；两次运行结果一致。
2. **可反推**：脚本必须输出 `flags_derivation.csv`，列为
   `record_id | conversion_path | 登记标记 | 命中规则 | 依据字段`。
3. **双向可校验**：人工在 GATE-4 增删的标记，若无法用规则表解释 → 报 `WARN unverifiable_flag`。
4. **禁止手工编辑 `inferred_flags` 而不留痕**：手工修改必须同时写入 `notes` 与 `conversion_params`。
5. **标记字典封闭**：只允许 `data-contract.md` §6 的 **20 项** + 本文件补充说明的 3 项脚本内部标记（`exploratory_reconstruction`、`inconsistent_param`、`bw_unused`）。
   ★ **单一来源**：`shared/inferred_flags.py`。各脚本从此模块导入，**不得自建副本**
   （曾因 5 处各写一份、扩充 16→19→20 时漏同步 2 处，导致闭集校验形同虚设；
   现由 `python shared/inferred_flags.py --self-test` 的「使用处覆盖核对」把关）。
6. **零推断的特殊性**：`reported_GM` 的记录 `inferred_flags` 必须为**空**；若非空则为数据错误。

### 6.4 需要回写 `shared/data-contract.md` 的三个新标记

| 标记 | 含义 | 触发场景 |
|---|---|---|
| `no_n_fallback` | 因缺少样本量而回退到不需 n 的公式（S3/S6） | 精度下降 |
| `molar_conversion` | 摩尔单位换算，引入形态/摩尔质量假设 | `nmol/L → μg/L` |
| `coarse_age` | 使用粗年龄段参数（如 `Minors` 统一值） | 原文只写"儿童" |

> **已完成（2026-07-19）**：三个标记（`no_n_fallback`、`molar_conversion`、`coarse_age`）已补入 `data-contract.md` §6，
> 加上 `cordblood_edi_skipped` 共 4 项；字典现为 **20 项**文档化标记 + 3 项脚本内部标记。
> **单一来源**：`shared/inferred_flags.py`（各脚本从此导入，不再自建副本）。

---

## 7. 换算痕迹表（`conversion_trace.csv`）

每条记录一行，字段见 `templates/conversion_trace.csv`。最小必含：

| 字段 | 含义 |
|---|---|
| `record_id` | 记录标识 |
| `stat_type` | 来源统计量类型 |
| `value_in` / `unit_in` | 输入值与原单位 |
| `path` | `conversion_path` 完整编码 |
| `params` | `conversion_params`（含所用公式、参数、变体） |
| `value_out` / `unit_out` | 输出值与目标单位 |
| `gm_out` / `gsd_out` | 最终 GM / GSD |
| `flags` | 自动登记的 `inferred_flags` |
| `status` | `OK` / `SKIP` / `FAILED` / `WARN` |
| `message` | 失败或告警原因 |

---

## 8. 常见错误清单

| 错误 | 后果 | 防范 |
|---|---|---|
| `ng/mL` 与 `μg/L` 未识别为等值 | 数值不变但被标为已换算 | 单位归一表 |
| `μg/g` 当 `μg/g Cr` | 量级与语义双错 | §2 陷阱提示 |
| 先转 GM 再换尿校正 | 非线性步骤错序 | 三级流程强制 |
| 对 `GM_GSD` 记录套 Wan | 重复转换，GM 被压低 | `wan_convert.py` 硬拒绝 |
| 成人默认男性参数 | 尿砷系统性低估约 10% | §3 决策树 |
| 混用 `CC` 直除与 `CE/V` | 同表内不自洽 | 全项目统一路径 |
| 摩尔换算不记录形态 | 后人无法复核 | 强制 `MW` 留痕 |
| `inferred_flags` 手工乱填 | 审计链断裂 | §6.3 双向校验 |

---

**维护者**：HBM-Meta-Agent（S4）　**关联**：`wan2014_formulas.md`、`icrp89_urine_reference.md`、`standardization_workflow.md`、`scripts/convert_units.py`
