# 检索策略方法学 — 从 project.yaml 到可复现的检索记录

> **配套**：`{skill_root}/SKILL.md`、`shared/data-contract.md` §5（project.yaml 规范）、`shared/quality-gates.md` GATE-1
> **原则**：检索式由配置生成草稿，由人工核改执行；命中数与检索式原文由人工记录，AI 不代填。

---

## 1. 检索式的构成：四维词表 + 排除词银行

一次内暴露浓度 Meta 分析的检索式 = **四个必需维度的 AND + 排除词银行的 NOT**：

```
(暴露物词块) AND (基质词块) AND (暴露评估词块) AND (人群限定词块)
  NOT (动物) NOT (细胞机制) NOT (职业)* NOT (临床)
  AND 年份过滤
```

*职业排除词带星号：`NOT occupational` 会连带排除"职业与非职业混合调查"研究 —— 这类研究
可能仅报告了可用的非职业亚组（GATE-2 需要它们）。是否使用、用后是否改为人工逐条裁决，
必须在 GATE-1 记录。各组的过度杀伤预警见 `references/exclusion_word_bank.md`。

词表来源：`project.yaml → search` 区块（`analytes` / `matrices` / `exposure_terms` /
`population_keywords` / `exclusion_terms`）。换暴露物 = 只改词表，不改脚本（通用性设计，
见根 `SKILL.md` §2）。

---

## 2. 各数据库语法差异（构造草稿时的已知差异）

细节与示例见 `references/database_syntax.md`。速查：

| 库 | 主题检索 | 叙词 | 布尔 | 年份 | 陷阱 |
|---|---|---|---|---|---|
| PubMed | `[tiab]` | `[MeSH Terms]` + `Mesh` 自动扩展 | `AND/OR/NOT` | `[PDAT]` | NOT 排除 MeSH 主词向下扩展的全部分支 |
| Web of Science | `TS=` | 无（人工补词根 `*`） | `AND/OR/NOT` | `PY=(a-b)` | 引号短语必须成对 |
| Scopus | `TITLE-ABS-KEY()` | 无 | `AND OR AND NOT` | `PUBYEAR` | 通配符 `*` 最多截断若干字符 |
| Embase | `:ti,ab,kw` | `'词'/exp`（Emtree） | `AND/OR/NOT` | `[a-b]` | Emtree 与 MeSH 概念不同，需人工映射 |
| CNKI | `SU=` | 无通用叙词 | `+ * -` | 界面设置 | 专业检索里 `+*-=()` 必须半角 |
| 万方 | `主题:` | 无 | `AND OR NOT` | 界面设置 | 中文短语加半角引号 |

**中文库的中文检索词**：`search.analytes[].synonyms_zh` / `search.matrices[].synonyms_zh`
必须人工提供；未提供时 `generate_search_terms.py` 输出"待人工补充中文检索词"占位 ——
脚本不编造中文词。

---

## 3. 可复现性要求（检索记录的最低字段）

每个数据库必须记录（对应 `source_summary.csv` / `01-search/检索记录.csv`）：

| 字段 | 要求 |
|---|---|
| `source_database` | 库名（枚举见 `shared/record_schema.py`） |
| `search_string` | **最终执行**的检索式原文（不是草稿！含人工改动后的全部字符） |
| `search_date` | 执行日期（`YYYY-MM-DD`；库内容随时间变化，无日期的检索不可复现） |
| `hit_count` | 系统返回的命中数（整数） |
| `exported_count` | 实际导出的题录数（由 `merge_sources.py` 从合并结果实算） |
| 附加 | 导出格式、字段集、筛选/去重选项（如有）、检索式版本号（改版必记） |

**对账**：`hit_count`（系统命中）与 `exported_count`（实际导出）的差必须能解释
（导出上限、按年份切片导出、库端去重选项等）。对不上的差值写进 `note`，不得静默。

---

## 4. PRISMA 2020 对 identification 阶段的要求

PRISMA 2020 流程图第一栏（Identification）需要以下数字，全部来自检索记录：

- 各库记录数（`hit_count`）；
- 各库去重前合计（`exported_count` 合计）；
- 去重后记录数（**S2 阶段产出**，`merge_sources.py` 只输出"疑似重复组数"供参考，
  去重裁决由人工完成）；
- 其他来源（揽回引文、注册库、网站等）—— 如有，人工补充登记。

**诚实声明**：未预先注册（PROSPERO 等）时，稿件必须声明；若已注册，登记编号。
这些决定都在 GATE-1/GATE-5 由人做出。

---

## 5. 命中量级诊断（GATE-1 的人工判断依据）

AI 可以提供以下诊断（`merge_sources.py` 输出 + 人工执行观察）：

| 现象 | 可能原因 | 建议（人工决定） |
|---|---|---|
| 命中数 < 数十 | 词表过窄 / 基质词过于特异 | 放宽基质或暴露评估词；去掉部分排除词 |
| 命中数 > 50000 | 排除词不足 / 暴露物词过泛 | 补排除词（先动物/细胞，后职业）；加人群限定 |
| 某库命中数与其索引规模明显不符 | 检索式语法错误 / 字段限定错误 | 核对 `references/database_syntax.md` 的语法表 |
| 职业排除后锐减 | `NOT occupational` 过度杀伤 | 改为人工逐条裁决（GATE-2 边界人群清单兜底） |

---

## 6. 与 GATE-1 的衔接

1. AI 产出：检索式草稿（`generate_search_terms.py`）+ 统一题录 + `source_summary.csv`；
2. 人工产出：核改后的最终检索式、执行记录、命中数、导出文件；
3. 人工裁决项（GATE-1，见 `shared/quality-gates.md`）：
   是否增补数据库；排除词是否过度排除；未预先注册声明；命中量级是否支持后续工作量；
4. 人开启 GATE-1 → 题录进入 S2（AI 预处理 + 人工闸门筛选）。

> **红线**：`_state/gates.json` 里 GATE-1 的 `passed` 只能由人写入。
