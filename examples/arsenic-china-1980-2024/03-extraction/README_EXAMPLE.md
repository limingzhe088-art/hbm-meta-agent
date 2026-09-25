# 提取表说明（脱敏样例）

> 本目录的 `master_table_EXAMPLE.csv` 是 **S3 的产出格式样例**（也是 S4 的输入）。
> 本文件说明表头分组与"样例该覆盖哪些分叉"。

---

## 1. ★ 三类表不要混用（本项目踩过的坑）

| 表 | 谁产出 | 标志列 | 用途 |
|---|---|---|---|
| **S3 原始提取表** ← 本样例 | S3 | `initial_*` / `unit_original` / `stat_type` | S4 的**输入** |
| **S4 标准化表** | S4 | `gm_summary` / `unit_final` / `conversion_path` | S5 的输入 |
| **S5 合并结果** | S5 | `stratum` / `gm` / `gsd` | 报告与制图 |

把 S4/S5 的输出当 S4 的输入，会构成"**用输出再跑一遍**"的循环错误。
`convert_units.py` 已加**输入前检**：`initial_*` 全空即报错退出码 2。

> 模板命名约定：`*_OUTPUT_example.csv` = 输出示例；不带的 = 输入模板。
> 本文件 `master_table_EXAMPLE.csv` 是**输入**（S3→S4），故不带 `_OUTPUT`。

---

## 2. 表头分组（72 列契约）

完整定义见 `shared/data-contract.md` §3。分组速览：

| 组 | 列数 | 关键列 |
|---|---|---|
| 来源标识 | 9 | `record_id` `study_no` `cohort_id` `title` `author` `doi` `t_publication` |
| 时空标识 | 10 | `time`（★ **采样年，不是发表年**）`province` `region` |
| 人群标识 | 13 | `population` `population_group` `gender` `male_n` `female_n` |
| **边界风险标记** | 6 | `flag_occupational` `flag_disease` `inclusion_decision`（★ **仅人工可写**） |
| 基质与检测 | 7 | `sample_type` `blood_matrix` `flag_blood_matrix` `analyte_species` |
| **原文统计量留痕** | 14 | `stat_type` `initial_gm` `initial_median` `unit_original`（★ **原样抄录**）`sample_size` |
| **标准化输出** | 8 | `adjusted` `unit_final` `gm_summary` `conversion_path` `inferred_flags` |
| 审计元数据 | 5 | `extracted_by` `verified` `verified_by` `snapshot_tag` `row_hash` |

---

## 3. 样例覆盖的分叉（教学要点）

12 条记录**刻意覆盖**全部分支，便于对照学习：

| 记录 | `stat_type` | 演示要点 |
|---|---|---|
| R00001 | `GM_GSD` | 直接报告 → `reported_GM`，**零推断**（`inferred_flags` 为空） |
| R00002 | `AM_SD` | 对数正态转换 `AMSD_to_LN`；含 `coarse_age` |
| R00003 | `Median_IQR` | `as_gm` 策略：中位数直接作 GM |
| R00004 | `GM_GSD` | **比重校正**（不套 ICRP）→ 进"校正方式"分层 |
| R00005 | `AM_SD` | 全血 `blood_matrix=WholeBlood` |
| R00006 | `Median_IQR` | **标题提示 serum** → `flag_blood_matrix=TRUE` + `Pending` |
| R00007 | `GM_only` | 脐血 → EDI 不计算（方案 A） |
| R00008 | `GM_only` | **摩尔单位换算** → `molar_conversion`（MW 留痕） |
| R00009 | `GM_GSD` | **疑似职业暴露** → `flag_occupational=TRUE` + `Pending` |
| R00010 | `Median_only` | **不可合并** → `FAILED`，须计入局限说明 |
| R00011 | `Median_IQR` | **Wan S4**（`via_wan` 策略）→ 产出 GSD |
| R00012 | `Median_Range` | **Wan S3 缺 n 回退** → `no_n_fallback` |

**四道人工闸门的示例**：`inclusion_decision`（GATE-2）｜`verified`/`verified_by`（GATE-3）｜
`adjusted`/`unit_final`/`conversion_path`（GATE-4）｜`inferred_flags`（GATE-4 复核 + GATE-5 分层）。

---

## 4. 校验本样例

```bash
# 契约校验（应通过）
python ../../skills/S3-extraction-standardization/scripts/validate_table.py \
    --input master_table_EXAMPLE.csv --config ../project.yaml

# 本样例的生成脚本自带脱敏规则自检
python ../make_example_table.py --self-test
```

---

## 5. ⚠️ 修改样例的方式

**不要手写 CSV**。请改 `../make_example_table.py` 后跑 `--write`：

```bash
python ../make_example_table.py --write       # 重新生成
python ../make_example_table.py --self-test   # 校验列对齐 + 脱敏规则 + 分叉覆盖
```

**原因**：手写 CSV 在开发期连续出错三次——
`extraction_template.csv` 漏一个值致整行左移 1 列、另有两行多 1 列、
`edi_results.csv` 的数值与代码不一致（见 `STEP4-TODO.md` C2 与 `agent/routing.md` §2.6）。

---

## 6. 脱敏规则（本样例必须满足）

| 项 | 规则 |
|---|---|
| `title` | `EXAMPLE-Study-NNN` |
| `author` | `EXAMPLE-Author-X` |
| `doi` | `10.xxxx/EXAMPLE-NNN` 或 `NO_DOI` |
| 浓度值 | **合成值**（量级合理，与任何真实研究无关） |
| 禁止出现 | 真实 DOI 形态 / PMID / 期刊卷期页 / 作者-年份引用形态 |

自检会自动核对以上规则（见 `make_example_table.py` 的 `[3]` 项）。
