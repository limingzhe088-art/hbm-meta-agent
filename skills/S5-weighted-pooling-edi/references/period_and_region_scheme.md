# 分期方案与地区字典（Period & Region Scheme）

> **用途**：定义 S5 的时空分组口径。**核心规则：一切来自 `project.yaml`，代码内零字面量。**
> **教训来源**：本项目曾因"描述性分析脚本用一套分期、EDI 脚本用另一套、手稿混用"被审稿人直接指出（R3-1）。

---

## 1. 铁律：单点定义 + 冻结日

```
project.yaml → period_scheme    ← 唯一来源
                    │
        ┌───────────┼───────────┬───────────┐
        ▼           ▼           ▼           ▼
   weighted_gm   edi_calc    制图脚本   数值审计
```

**三条硬约束**

| # | 约束 | 违反后果 |
|---|---|---|
| 1 | 分期边界**只能**在 `project.yaml` 定义 | 代码里出现 `1980`、`2000` 等字面量 → 口径分叉 |
| 2 | 分期方案有 **`freeze_date`**，冻结后变更须 bump `config_version` | 无冻结日 → 返修时才发现数字全变 |
| 3 | 分期变更 → **所有下游产物标记 `stale` 并重算** | 静默不一致，审稿阶段才暴露 |

**本项目实际发生的错误**（作为反例留档）：
- 描述性分析脚本：`1980-1999 / 2000-2010 / 2011-2020 / 2021-2024`
- EDI 脚本：`1980-2000 / 2001-2010 / 2011-2020 / 2021-2024`
- 差异仅涉及 2000 年的 15 条尿记录，但导致同一指标在两份产出中不同（`32.56` vs `32.58`），手稿混用后被审稿人指出。

---

## 2. `project.yaml → period_scheme` 规范

```yaml
period_scheme:
  name: "four_era"                    # 方案名（用于报告与审计）
  boundaries:
    - {label: "1980-2000", start: 1980, end: 2000}
    - {label: "2001-2010", start: 2001, end: 2010}
    - {label: "2011-2020", start: 2011, end: 2020}
    - {label: "2021-2024", start: 2021, end: 2024}
  freeze_date: "2026-07-19"           # ★ 冻结日；缺失视为 GATE-0 未完成
  boundary_policy: "inclusive"        # 边界含端点：[start, end]
  out_of_range: "exclude"             # 落在所有区间外的记录：exclude / flag
  representative_year_rule: "start"   # 多年区间取代表年的规则（start / midpoint / end）
```

### 2.1 设计原则

| 原则 | 说明 |
|---|---|
| **区间不重叠且完备** | 相邻区间首尾相接（`end` 与下一个 `start` 差 1），不留缝隙 |
| **标签即产出** | `label` 直接用于结果表与图，不另起名字 |
| **边界含端点** | `[start, end]` 闭区间；相邻区间不得共用同一年 |
| **区间外记录显式处置** | `exclude`（默认，且必须计数）或 `flag`（保留并标记） |
| **分期有学科依据** | 应与政策/技术节点对应（如饮用水标准修订、检测方法换代），并在方法学中说明理由 |

### 2.2 分期方案示例（可按研究问题替换）

| 方案 | 分期 | 适用 |
|---|---|---|
| `four_era` | 1980-2000 / 2001-2010 / 2011-2020 / 2021-2024 | 长跨度、政策节点对齐 |
| `decade` | 1980s / 1990s / 2000s / 2010s / 2020s | 粗粒度趋势 |
| `policy_aligned` | 自定义（如按饮用水标准修订年切分） | 政策评估导向 |
| `none` | 不分期，仅逐年 | 记录数充足时 |

**选择要求**：方案必须在 GATE-0 由人工确认，并在方法学中写明**为何这样切分**（不能只写"按十年"）。

---

## 3. 记录数 vs 唯一参与者

### 3.1 ★ 必须区分的三个量

| 量 | 定义 | 计算机字段 |
|---|---|---|
| **记录数** | 主表行数（1 行 = 1 条浓度统计） | `record_count` |
| **研究数** | 唯一 `study_no` 数 | `study_count` |
| **参与者数** | 受试者人数；**同一人可能贡献多条记录** | `total_sample_size`（**记录级求和，非唯一人数**） |

### 3.2 为什么必须区分（本项目实证）

原手稿写"more than 360,000 participants"，审稿人指出：同一出生队列被 35 篇论文报告，同一批孕妇可能被重复计数；且同一人可能同时贡献尿样与血样记录。因此该数字是**记录级求和**，不是唯一人数。

**修正后的措辞**（已采用）：
> "...including more than 360,000 **participant records**... Sample sizes were summed across records; because some participants may have contributed to more than one record or more than one matrix, these totals represent **record-level sums rather than counts of unique individuals**."

### 3.3 措辞规则（写进图注/表注）

| 情形 | 允许的措辞 | 禁止的措辞 |
|---|---|---|
| 记录级求和 | `participant records`、`record-level sums` | ❌ `participants`、`individuals`、`subjects` |
| 已做队列去重 | `unique participants`（须说明去重方法与影响） | ❌ 只说 `participants` 而不说明是否去重 |
| 研究数 | `studies` | ❌ 与记录数混用同一词 |

### 3.4 强制输出

每个分层单元与总表必须同时输出：

```
record_count | study_count | total_sample_size | figure_table_note
```

其中 `figure_table_note` 为固定文案（由配置或模板提供），例如：
> "Sample sizes are record-level sums; some participants contributed to more than one record or matrix."

**强制检查**：任何结果表/图若只有 `n` 而无类型说明 → 校验失败。

---

## 4. 地区字典

### 4.1 `project.yaml → region_map` 规范

```yaml
region_map:
  Southwest: ["四川省","云南省","贵州省","重庆市","西藏自治区"]
  Northwest: ["陕西省","甘肃省","青海省","宁夏回族自治区","新疆维吾尔自治区"]
  North:     ["北京市","天津市","河北省","山西省","内蒙古自治区"]
  Central:   ["河南省","湖北省","湖南省"]
  East:      ["上海市","江苏省","浙江省","安徽省","福建省","江西省","山东省","台湾省"]
  South:     ["广东省","广西壮族自治区","海南省","香港特别行政区","澳门特别行政区"]
  Northeast: ["辽宁省","吉林省","黑龙江省"]
```

### 4.2 规则

| # | 规则 | 说明 |
|---|---|---|
| 1 | 省级名称**必须为规范全称**并全局统一 | 不得混用 `云南` / `云南省` / `Yunnan` |
| 2 | 未归类省份 → `UNKNOWN` | **不得猜测归属**；输出 `UNKNOWN` 计数 |
| 3 | 大区划分依据必须写进方法学 | 中国七分法需说明来源（如国家统计局标准） |
| 4 | 换国家时整表替换 | `region_map` 是"换区域"的唯一切换点 |
| 5 | 省份 → 大区映射必须单值 | 一省不得映射到两个大区 |

### 4.3 排序输出规范

地区排序**按数值降序**（高暴露在前），不得按字典序或地理习惯序，除非研究问题另有要求。

**本项目实证**：原手稿地区排序过时且漏了一个地区（尿砷漏西北），审稿人 R2-3 指出后重算。教训：**排序必须在每次重算后重新生成，不得手工维护**。

---

## 5. 其他分组维度

| 维度 | 字段 | 取值处理 |
|---|---|---|
| 人群 | `population_group` | `Mixed` / `Unknown` **不进入 EDI**（生理参数无法确定），但可进入浓度合并 |
| 性别 | `gender` | `Both`（未分性别）与分性别结果**并列报告**，不相加 |
| 血基质 | `blood_matrix` | `WholeBlood` 与 `Serum`/`Plasma` **分层**，不默认合并 |
| 形态 | `analyte_species` | `Total` 与形态之和**分层** |
| 校正方式 | `adjusted` | `Creatinine`/`SpecificGravity`/`No`/`ReferenceConversion` → 敏感性分析分层 |

---

## 6. 变更控制

| 变更 | 要求 |
|---|---|
| 修改分期边界 | bump `config_version`；重跑 S4→S5→S6；产出新旧对照表；所有产物重新标记 |
| 修改地区字典 | 同上 |
| 修改代表年规则 | 同上（影响 `time` 所属分期） |
| 新增分期方案 | 可作为敏感性分析并行保留；**不得**在同一份结果表内混用两套 |
| 冻结后首次变更 | 必须在报告首页显著标注"分期方案已于 YYYY-MM-DD 变更" |

**新方案进入流程的唯一入口**：`project.yaml`。任何"脚本里加个 if 处理特殊情况"的做法都会立即破坏单点定义，禁止。

---

**维护者**：HBM-Meta-Agent（S5）　**关联**：`weighting_method.md`、`reporting_checklist.md`、`shared/data-contract.md` §5
