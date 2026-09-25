# 去重与纳入标准筛查报告

> 模板来源：`skills/S6-qc-audit-revision/references/dedup_cohort_protocol.md`
> 生成脚本：`scripts/dedup_screen.py`（配合 S2 的 `flag_scan.py`）
> **本报告仅列疑似问题；是否排除/去重需人工在 GATE-2 / GATE-5 逐条裁决。**

---

## 0. 版本信息

| 项 | 内容 |
|---|---|
| 报告版本 | 第 {{version}} 版 |
| 完成日期 | {{date}} |
| 数据快照 | `{{snapshot_tag}}` |
| 记录数 / 研究数 | {{n_records}} / {{n_studies}} |
| 样本量之和（记录级求和） | {{total_sample_size}} |

---

## 〇、本轮新增核实

> 相对上一轮的新发现（若无则写"无"）。

1. 采样年份完整性：{{year_completeness}}
2. 血基质存疑：{{blood_matrix_check}}
3. 形态覆盖：{{species_check}}
4. 疑似违反纳入标准的最终精化清单：{{refinement}}

---

## 一、同一人群重复计数风险

> 按 (城市, 基质) 聚簇，≥ {{min_studies}} 篇进人工复核。

### 簇 {{i}}：{{city}} \| {{matrix}}{{cohort_flag}}

- 研究数：{{n_studies_in_cluster}}　记录数：{{n_records_in_cluster}}　样本量之和：{{cluster_ss}}
- 疑似队列：{{cohort_name_or_unknown}}

| 研究编号 | 第一作者 | 年份 | 样本量 | 标题（截断） |
|---|---|---|---|---|
| | | | | |

**待人工裁决**：
☐ 同一队列（请填 `cohort_id`：______）　☐ 不同人群　☐ 需查原文

**若为同一队列的处置建议**：{{suggestion}}

（按此格式重复列出每个簇）

### 其他同城市多研究簇（≥4 篇）

| 城市 | 基质 | 研究数 | 备注 |
|---|---|---|---|
| | | | |

---

## 二、疑似违反纳入标准：职业暴露人群

> 纳入标准通常明确"非职业暴露人群"。

- 命中记录数：{{n_occ_records}}
- 唯一研究数：{{n_occ_studies}}

| 编号 | 命中关键词 | 影响记录数 | 待裁决 |
|---|---|---|---|
| | 职业砷接触工人 / 冶炼厂 / 金矿 / 焦化厂 … | | ☐ Include ☐ Exclude |
| | **职业与非职业混合**（若仅报告非职业亚组则可保留） | | ☐ 需查原文 |

---

## 三、疑似违反纳入标准：特定疾病/病区人群

- 命中记录数：{{n_dis_records}}
- 唯一研究数：{{n_dis_studies}}

| 编号 | 命中关键词 | 影响记录数 | 待裁决 |
|---|---|---|---|
| | 慢性中毒患者 / 病区 / 糖尿病 / 病例对照 … | | ☐ Include ☐ Exclude ☐ 需确认浓度是否来自对照组 |

---

## 四、其他边界问题

### 4.1 血基质存疑

| 项 | 内容 |
|---|---|
| 记录数（标题提示 serum/plasma/血清/血浆） | {{n_serum}} |
| 唯一研究数 | {{n_serum_studies}} |
| 处理 | 打 `flag_blood_matrix`；与全血**分层**，不默认合并 |

### 4.2 形态覆盖

| 项 | 内容 |
|---|---|
| 提示做过形态分析的记录 | {{n_species}} |
| 其余 | 总砷 |
| 可比性声明 | {{species_statement}} |

### 4.3 分期口径

| 项 | 内容 |
|---|---|
| 现行分期方案 | {{period_scheme}} |
| 冻结日 | {{freeze_date}} |
| 若存在两套方案的影响 | {{period_impact}} |

---

## 五、去重策略影响估计

| 策略 | 记录数（前→后） | 样本量之和（前→后） | 降幅 |
|---|---|---|---|
| `S1_keep_all_fix_wording` | | | 0% |
| `S2_keep_largest` | | | |
| `S3_merge_within_cohort` | | | |

**推荐**：以 `S1`（保留全部 + 修正措辞）为主分析，`S2` 作为敏感性分析。
理由：`S2` 会改变主分析结果；降级为敏感性可同时满足"不虚高"与"结论稳健性"。

---

## 六、计数措辞检查

| 命中 | 上下文 | 建议 |
|---|---|---|
| `360,000 participants` | … | 改为 `participant records` |

**必须写入图注/表注**：
> Sample sizes are record-level sums; some participants contributed to more than one record or more than one matrix.

**拟采用的正文表述**：
> {{proposed_wording}}

---

## 七、建议处理顺序

1. 人裁决各簇是否为同一队列（填 `cohort_id`）
2. 选定去重策略（S1/S2/S3）并记录于 GATE-5
3. 修正摘要与正文的计数措辞
4. 如需重算，触发 S5 重跑并同步数值审计

---

## 八、门禁

- [ ] 所有簇已有裁决（`cohort_id` 或"不同人群"）
- [ ] 职业/疾病人群清单已逐条裁决
- [ ] 去重策略已选定并记录
- [ ] 计数措辞已修正
- [ ] 局限说明已包含"未去重"或去重方法
- [ ] 快照指纹已记录

| 签署 | 内容 |
|---|---|
| 执行（AI） | |
| 裁决（人） | |
| 日期 | |

---

**维护者**：HBM-Meta-Agent（S6）
