# 脱敏案例：中国一般人群内暴露砷时空分布（1980–2024）

> **这是教学案例，不是数据集。**
> 用于演示 HBM-Meta-Agent 的 S3 → S4 → S5 → S6 端到端流程。

---

## 📋 脱敏说明（必读）

本案例**保留**了以下内容，因为它们承载教学价值：

| 保留项 | 为何保留 |
|---|---|
| **数据库名**（PubMed / Web of Science / CNKI / Wanfang） | 不同库的语法差异是 S1 的核心知识 |
| **检索式语法**（`TS=` / `TKA=` / `SU=` / `NOT` 陷阱） | 可复现的检索构造方法 |
| **排除词四类分组**（动物 / 细胞机制 / 职业 / 临床） | S1 的核心可复用资产 |
| **命中数量级**（5714 / 546 / 4649 / 519 / 135 / 384） | ★ **见下方专门说明** |
| **表头结构**（72 列契约） | 数据契约的具体形态 |
| **报告骨架**（审计对照表 / 返修清单） | S6 的过程文档标准 |

**已全部移除**：
- ❌ 具体研究**标题**（一律 `EXAMPLE-Study-NNN`）
- ❌ 具体**作者**（一律 `EXAMPLE-Author-X`）
- ❌ 具体 **DOI**（一律 `10.xxxx/EXAMPLE-NNN` 或 `NO_DOI`）
- ❌ **省份级真实数值**（浓度值为**合成值**：量级合理，但与任何真实研究无关）
- ❌ PMID、期刊卷期页、作者-年份引用形态（有自动检查，见 `make_example_table.py`）

### 关于命中数（保留的专门说明）

> **命中数用于检索充分性诊断，保留以便教学。**
> 具体研究标题、作者、DOI、省份级真实数值已全部移除。
>
> 理由：命中数（各库检出量、去重数、各阶段排除数）是 **S1「检索充分性诊断」的核心证据**——
> 练习者需要据此判断"检索式是否过宽/过窄、排除词是否过度杀伤"。
> 这些数字**不含个人信息**，任何做同类内暴露 meta 分析的人都能复现同量级结果。
> 删掉它们会让 S1 的教学价值归零。
>
> 命中数与研究数量、参与者规模**无关**：它只反映检索策略的广度与筛选强度。

---

## 🚀 快速跑通（5 条命令）

前置：Python ≥ 3.11，且已安装依赖。

```bash
# 0) 安装依赖（在技能包根目录执行）
pip install -r requirements.txt

# 1) 建工作目录（把案例复制出去，避免污染技能包）
mkdir -p /tmp/hbm-demo && cd /tmp/hbm-demo
mkdir -p 03-extraction 04-standardization 05-pooling-edi 06-qc-audit
cp -r <技能包路径>/examples/arsenic-china-1980-2024/project.yaml .
cp    <技能包路径>/examples/arsenic-china-1980-2024/03-extraction/master_table_EXAMPLE.csv 03-extraction/
export SKILL=<技能包路径>

# 2) S3 → 校验契约（应 PASS）
python $SKILL/skills/S3-extraction-standardization/scripts/validate_table.py \
    --input 03-extraction/master_table_EXAMPLE.csv --config project.yaml

# 3) S4 → 单位换算（含快照指纹与标准化报告）
python $SKILL/skills/S4-unit-statistic-conversion/scripts/convert_units.py \
    --input 03-extraction/master_table_EXAMPLE.csv --config project.yaml \
    --out 04-standardization/standardized.csv \
    --trace 04-standardization/conversion_trace.csv \
    --snapshot-registry _state/snapshots.csv

# 4) S4 → 统计量换算（Wan 2014 S1–S7）+ 标记反推（链式兼容：保留上一步的单位换算段）
python $SKILL/skills/S4-unit-statistic-conversion/scripts/wan_convert.py \
    --input 04-standardization/standardized.csv --config project.yaml \
    --out 05-pooling-edi/pooled_input.csv \
    --trace 04-standardization/wan_trace.csv \
    --flags-derivation 04-standardization/flags_derivation.csv

# 5) S5 → 加权合并 + EDI
python $SKILL/skills/S5-weighted-pooling-edi/scripts/weighted_gm.py \
    --input 05-pooling-edi/pooled_input.csv --config project.yaml \
    --dims sample_type,period,region \
    --out 05-pooling-edi/pooled_results.csv

python $SKILL/skills/S5-weighted-pooling-edi/scripts/edi_calculation.py \
    --input 05-pooling-edi/pooled_input.csv --config project.yaml \
    --out 05-pooling-edi/edi_results.csv

# 6) S6 → 数值审计（从主表真正重算）/ 去重筛查 / 敏感性分析
python $SKILL/skills/S6-qc-audit-revision/scripts/audit_numbers.py \
    --input 06-qc-audit/number_audit_EXAMPLE.md \
    --master-table 03-extraction/master_table_EXAMPLE.csv \
    --config project.yaml --out 06-qc-audit/audit_report.md

python $SKILL/skills/S6-qc-audit-revision/scripts/dedup_screen.py \
    --input 03-extraction/master_table_EXAMPLE.csv --config project.yaml \
    --out 06-qc-audit/dedup_report.md

python $SKILL/skills/S6-qc-audit-revision/scripts/sensitivity.py \
    --input 05-pooling-edi/pooled_input.csv --config project.yaml --matrix Urine \
    --out 06-qc-audit/sensitivity.md

# 7) 确认环境正确（应全绿）
python $SKILL/tests/ci_local.py        # 本地 CI 预演，应 5/5 通过
```

### 各步的预期退出码（★ 非零是**设计内的状态报告**，不是失败）

| 步骤 | 退出码 | 含义 |
|---|---|---|
| S3 契约校验 | **0** | `PASS`：0 ERROR / 2 WARN（比重校正保留原单位、verified 提示） |
| S4 `convert_units` | **1** | `FAILED=1`：R00010 只有中位数、无离散度 → **本就不可合并**（教学点） |
| S4 `wan_convert` | **1** | 同上；且 `FLAG_MISMATCH` 应为 **0** |
| S5 `weighted_gm` | **1** | 6 个分层单元带告警（单记录权重占比 > 30% / 记录数 < 3） |
| S5 `edi_calculation` | **1** | 3 条带告警（脐血跳过、血路探索性重建、Minors 参数不自洽） |
| S6 `audit_numbers` | **1** | 稿件数字为**合成值**，与主表重算值不一致 → `MISMATCH`/`NO_SOURCE`（演示四状态） |
| S6 `dedup_screen` | **1** | 1 个同城簇待人工裁决 |
| S6 `sensitivity` | **1** | 4 个分层占比 < 20% → 结论降级（教学点） |

> **注意**：S6 数值审计的输入 `06-qc-audit/number_audit_EXAMPLE.md` 里的"稿件数字"
> 是**故意造的合成值**，目的是演示 `OK` / `MISMATCH` / `NO_SOURCE` / `STALE_MANUSCRIPT`
> 四种状态。**不要期待它们与主表吻合**——吻合就演示不出审计价值了。

> **路径提示**：`$SKILL` 指技能包根目录。若直接在本目录内运行，改用相对路径
> （如 `../../skills/...`）。

---

## 📁 目录内容

| 路径 | 内容 |
|---|---|
| `README.md` | 本文件（跑通指南 + 脱敏说明） |
| `project.yaml` | ★ **可直接复制的完整配置模板**（含口径说明与改动后果） |
| `01-search/search_strategy_EXAMPLE.md` | 检索式（脱敏）+ **排除词银行四类分组** + 命中数说明 |
| `03-extraction/master_table_EXAMPLE.csv` | 主表样例：**完整 72 列表头** + 12 条脱敏记录 |
| `03-extraction/README_EXAMPLE.md` | 表头字段的分组说明与"该覆盖哪些分叉" |
| `06-qc-audit/number_audit_EXAMPLE.md` | 数值审计对照表样例（含四状态与连带修改） |
| `11-revision/review_progress_EXAMPLE.md` | 返修清单样例（状态机 + 数值台账） |
| `make_example_table.py` | 生成主表样例（**含脱敏规则自检**；改样例请改此脚本，勿手写 CSV） |

---

## 🎯 这份样例演示了什么

`03-extraction/master_table_EXAMPLE.csv` 的 12 条记录**刻意覆盖了全部分叉**：

| 分叉 | 示例记录 | 演示要点 |
|---|---|---|
| 直接报告 GM/GSD（零推断） | R00001 | `conversion_path=reported_GM`，`inferred_flags` 为空 |
| AM+SD → 对数正态转换 | R00002 | `AM_SD→AMSD_to_LN→GM`；含 `coarse_age` |
| 中位数直接作 GM | R00003 | `Median_as_GM`（`median_to_gm_strategy=as_gm`） |
| **Wan S4 估算** | R00011 | `via_wan` 策略下走 `Wan_S4`，产出 GSD |
| **Wan S3 缺 n 回退** | R00012 | `no_n_fallback` 标记 + 精度下降提示 |
| 比重校正（不套 ICRP） | R00004 | `adjusted=SpecificGravity`，进"校正方式"分层 |
| 全血 | R00005 | `blood_matrix=WholeBlood` |
| **血基质存疑（serum）** | R00006 | `flag_blood_matrix=TRUE` + `blood_matrix_assumed` |
| 脐血 | R00007 | EDI 不计算（方案 A，`cordblood_edi_skipped`） |
| **摩尔单位换算** | R00008 | `molar_conversion`（MW 假设须留痕） |
| **疑似职业暴露** | R00009 | `flag_occupational=TRUE` + `Pending`（AI 不得自行排除） |
| **不可合并（只有中位数）** | R00010 | `Median_only→FAILED`，须计入局限说明 |

**四道人工闸门的示例**也都体现在记录里：`inclusion_decision`（GATE-2）、
`verified` / `verified_by`（GATE-3）、`adjusted` / `unit_final` / `conversion_path`（GATE-4）、
`inferred_flags`（GATE-4 复核 + GATE-5 敏感性分层）。

---

## ⚠️ 注意

1. **不要用本案例的数字做任何科学研究**——浓度值是合成的。
2. **不要用本案例验证自己的脚本输出**——请用 `tests/verify_consistency.py` 与各脚本的 `--self-test`。
3. 修改样例请改 `make_example_table.py` 后跑 `--write`，**不要手写 CSV**
   （手写模板在开发期连续出错三次：列错位、数值与代码不一致；
   见 `STEP4-TODO.md` C2 与 `agent/routing.md` §2.6）。
4. 本案例**不含 S1/S2 的完整实现示例**——这两个子技能目前只有目录骨架，
   见 `STEP4-TODO.md` **F5 · 实现 S1 / S2**。

---

**相关**：`README.md`（技能包总览）｜`shared/data-contract.md`（表结构权威定义）｜`STEP4-TODO.md`（已知缺口与增强计划）
