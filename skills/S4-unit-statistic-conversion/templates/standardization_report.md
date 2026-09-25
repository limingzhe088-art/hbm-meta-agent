# 标准化报告（Standardization Report）

> **阶段**：S4（unit-statistic-conversion）　**产出闸门**：GATE-4
> **模板用途**：GATE-4 待裁决包的正文；同时作为快照冻结的依据文件。
> **填写要求**：数字必须可由 `conversion_trace.csv` 与 `flags_derivation.csv` 复现。

---

## 0. 快照指纹

> 由脚本自动填入；人工不得修改。第四步实现自动生成（见 `{skill_root}/STEP4-TODO.md` A4）。

```yaml
snapshot:
  source_table: {{source_table}}
  sha256: {{sha256}}
  generated_at: {{generated_at}}
  config_version: {{config_version}}
  contract_version: "1.0.0"
  record_count: {{record_count}}
  study_count: {{study_count}}
  generator: skills/S4-unit-statistic-conversion/scripts/
  git_commit: {{git_commit}}
```

---

## 1. 配置与口径（GATE-0 冻结值，此处仅复核）

| 项 | 值 | 来源 |
|---|---|---|
| `analyte` | {{analyte}} | `project.yaml` |
| `matrix_scope` | {{matrix_scope}} | `project.yaml` |
| 尿目标单位 | `{{urine_target_unit}}` | `unit_policy` |
| 血 / 脐血目标单位 | `{{blood_target_unit}}` | `unit_policy` |
| 分期方案 | {{period_scheme_name}} | `period_scheme`（`freeze_date: {{freeze_date}}`） |
| 中位数转 GM 策略 | {{median_to_gm_strategy}} | `standardization` |
| Wan 变体 | {{wan_variant}} | `standardization` |
| 尿校正路径 | {{creatinine_path_mode}} | `standardization` |
| 成人性别不明参数 | `Adults`（CE=1.35, V=1.4, CC=0.964） | `urine_reference` |

**⚠️ 若上表任一项与 GATE-0 冻结值不一致 → 停止，不得继续。**

---

## 2. 输入概况

| 项 | 数量 | 占比 |
|---|---|---|
| 输入记录总数 | {{n_in}} | 100% |
| 按基质：尿 / 血 / 脐血 / 其他 | {{n_urine}} / {{n_blood}} / {{n_cord}} / {{n_other}} | |
| 按 `verified=TRUE` | {{n_verified}} | {{pct_verified}} |

### 2.1 按 `stat_type` 分布（决定换算路径）

| `stat_type` | 记录数 | 占比 | 换算路径 |
|---|---|---|---|
| `GM_GSD` | {{n_gm_gsd}} | | 直接取用（零推断） |
| `GM_only` | {{n_gm_only}} | | 直接取用（零推断） |
| `AM_SD` | {{n_am_sd}} | | `AM_SD→AMSD_to_LN→GM` |
| `Median_IQR` | {{n_med_iqr}} | | `Median_as_GM` 或 `Wan_S4/S6/S7` |
| `Median_Range` | {{n_med_range}} | | `Wan_S2` 或 `Wan_S3` |
| `Min_Max` | {{n_min_max}} | | `Wan_S1` 或 `Wan_S3` |
| `Median_only` | {{n_med_only}} | | **FAILED**（不可合并） |
| 其他 | {{n_other_stat}} | | 人工判断 |

### 2.2 按 `unit_original` 分布（人工必须逐类确认）

| `unit_original`（原文写法） | 记录数 | 归一化后 | 换算路径 | 是否已确认 |
|---|---|---|---|---|
| {{unit1}} | {{n1}} | | | ☐ |
| {{unit2}} | {{n2}} | | | ☐ |
| … | | | | |

> **人工确认点**：是否存在未预期或无法归类的单位？若有 → 停止，回 GATE-3。

---

## 3. 换算结果

### 3.1 状态统计

| 状态 | 记录数 | 占比 | 处置 |
|---|---|---|---|
| `OK` | {{n_ok}} | | 进入合并池 |
| `SKIP` | {{n_skip}} | | 进入合并池，`flags` 为空 |
| `WARN` | {{n_warn}} | | 进入合并池，进敏感性候选 |
| `FAILED` | {{n_failed}} | | ★ **不得进入合并**（必须计入局限说明） |

### 3.2 尿校正状态分布

| `adjusted` | 记录数 | 占比 | 敏感性分析可行性 |
|---|---|---|---|
| `Creatinine`（原文已校正） | {{n_creat}} | | |
| `ReferenceConversion`（ICRP 89 换算） | {{n_refconv}} | | |
| `SpecificGravity`（比重校正） | {{n_sg}} | | |
| `No`（未校正保留） | {{n_no}} | | |
| `NotApplicable`（血/脐血） | {{n_na}} | | |

> **判断**：各层样本量是否足够支持敏感性分析？最小层样本量 = {{min_stratum_n}}。
> 经验阈值：单层 < 30 条记录时，敏感性分析结论只能作定性参考。

### 3.3 `conversion_path` 分布（前 10 条）

| `conversion_path` | 记录数 | 说明 |
|---|---|---|
| {{path1}} | {{np1}} | |
| {{path2}} | {{np2}} | |
| … | | |

---

## 4. `inferred_flags` 登记汇总（可审计）

### 4.1 标记分布

| 标记 | 触发规则 | 记录数 | 占比 |
|---|---|---|---|
| `gm_from_mean_sd` | 路径含 `AMSD_to_LN` / `Wan_S1` / `Wan_S5` | {{f1}} | |
| `gm_from_median` | 路径含 `Median_as_GM` / `Wan_S2` / `S4` / `S6` / `S7` | {{f2}} | |
| `gm_from_iqr` | 路径含 `Wan_S4` / `S5` / `S6` / `S7` | {{f3}} | |
| `gm_from_range` | 路径含 `Wan_S1` / `S2` / `S3` | {{f4}} | |
| `unit_converted_creatinine` | 路径含 `ICRP89` | {{f5}} | |
| `unit_converted_volume` | 来源 `ug/g Cr` → 目标 `ug/L` | {{f6}} | |
| `molar_conversion` | 路径含 `nmol/L` 等 | {{f7}} | |
| `no_n_fallback` | 走了 S3 / S6 | {{f8}} | |
| `coarse_age` | 用了 `Minors` 粗年龄段参数 | {{f9}} | |
| 其他（继承自 S3） | `time_represented` / `blood_matrix_assumed` / `species_total_assumed` | {{f10}} | |
| **合计（去重后记录数）** | | {{n_flagged}} | |

### 4.2 零推断子集完整性检查

| 检查项 | 结果 |
|---|---|
| `reported_GM` 记录数 | {{n_reported_gm}} |
| 这些记录中 `flags` 为空的条数 | {{n_reported_clean}} |
| **是否 100% 为空** | ☐ 是 ☐ 否（若有非空 → ERROR，必须排查） |

> 该子集是"仅直接报告 GM"敏感性分析的基础。若非空，子集定义被污染。

### 4.3 双向校验（人工复核结果）

| 项 | 数量 |
|---|---|
| 脚本自动登记的标记条目 | {{n_auto}} |
| 人工新增/修改的标记条目 | {{n_manual}} |
| 无法用规则表解释的标记（`unverifiable_flag`） | {{n_unverifiable}} |

> 人工修改必须同时写入 `notes` 与 `conversion_params`；否则审计链断裂。

---

## 5. 异常清单（必须逐条处理）

### 5.1 `FAILED` 记录

| # | `record_id` | `stat_type` | 失败原因 | 处置建议 | 人工裁决 |
|---|---|---|---|---|---|
| 1 | | | 只有中位数无离散度 | 不可合并，进局限说明 | ☐ |
| 2 | | | 分位矛盾 | 回 GATE-3 复核 | ☐ |
| 3 | | | 单位无法识别 | 回 GATE-3 复核 | ☐ |
| 4 | | | `η ≤ 0` / 中心值 ≤ 0 | 交人工判断 | ☐ |

### 5.2 `WARN` 记录

| # | `record_id` | 告警类型 | 内容 | 是否作为敏感性剔除候选 |
|---|---|---|---|---|
| 1 | | `no_n_fallback` | 缺 n，回退 S3/S6 | ☐ |
| 2 | | `coarse_age` | 用 Minors 粗参数 | ☐ |
| 3 | | `small_n` | n < 25，Wan 误差大 | ☐ |
| 4 | | `inconsistent_param` | 参数表不自洽（原项目沿用） | ☐ |

### 5.3 量级合理性检查

| 基质 | 已知常见区间 | 超界记录数 | 处理 |
|---|---|---|---|
| 尿（`ug/g Cr`） | 约 1–100 | {{n_oob_urine}} | 逐条回原文核单位 |
| 血 / 脐血（`ug/L`） | 约 0.1–10 | {{n_oob_blood}} | 同上 |

> 超界不等于错误，但**必须逐条核过**（可能是高暴露区、职业人群、或单位错）。

---

## 6. S3 交付契约符合性

| S3 的承诺 | 实际 | 是否达标 |
|---|---|---|
| `stat_type` 与 `initial_*` 互相对位 | {{r1}} | ☐ |
| `unit_original` 100% 非空 | {{r2}} | ☐ |
| `sample_size` 100% 非空且 > 0 | {{r3}} | ☐ |
| S4 字段在 S3 阶段留空 | {{r4}} | ☐ |
| `notes` 记录所有不确定项 | {{r5}} | ☐ |

**若任一项不达标**：不得自行修正，回报 S3 走 GATE-3 重开流程（触发 stale 回滚）。

---

## 7. 分层可行性预判（供 S5 / GATE-5）

| 潜在分层 | 记录数 | 是否建议执行 |
|---|---|---|
| 按尿校正方式 | {{s1}} | ☐ |
| 按 GM 来源（直接报告 vs 估算） | {{s2}} | ☐ |
| 按血基质（全血 vs 血清/血浆） | {{s3}} | ☐ |
| 按形态（总砷 vs 形态） | {{s4}} | ☐ |
| 按 `no_n_fallback` | {{s5}} | ☐ |

---

## 8. GATE-4 出口自检

- [ ] `project.yaml` 存在且必需区块完整（**无兜底**）
- [ ] 所有记录 `conversion_path` 非空（`FAILED` 亦须有失败路径）
- [ ] 所有记录 `unit_final` 等于 `unit_policy` 对应目标单位
- [ ] `flags_derivation.csv` 已产出，标记反推可追溯
- [ ] `reported_GM` 记录 `flags` 100% 为空
- [ ] `validate_table.py` 无 `ERROR`
- [ ] `FAILED` / `WARN` 清单已逐条处理
- [ ] 量级合理性检查已完成
- [ ] 快照指纹已生成
- [ ] **人工批准冻结**

| 签署 | 内容 |
|---|---|
| 执行（AI） | |
| 复核（人） | |
| 日期 | |
| 结论 | ☐ 通过（批准冻结，生成 `snapshot_tag`） ☐ 未通过（原因：______） |

**批准后的 `snapshot_tag`**：`{{snapshot_tag}}`

---

**维护者**：HBM-Meta-Agent（S4）　**关联**：`references/standardization_workflow.md`、`templates/conversion_trace.csv`
