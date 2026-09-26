# 数据库检索语法差异表 + 导出格式建议

> **配套**：`{skill_root}/search-strategy.md`、`generate_search_terms.py` 的构造器实现
> **维护**：数据库界面常改版；执行检索前请以数据库当前帮助文档为准，差异记入检索记录。

---

## 1. 六库语法速查

### PubMed（NLM）

| 项 | 语法 |
|---|---|
| 主题词 | `"Arsenic"[MeSH Terms]`（默认自动扩展下位词；`[MeSH Terms:noexp]` 关闭扩展） |
| 自由词 | `"urinary arsenic"[tiab]`（`[tiab]` = 标题/摘要；`[Title]` 仅标题） |
| 布尔 | `AND` `OR` `NOT`（大写） |
| 年份 | `("1980/01/01"[PDAT] : "2024/12/31"[PDAT])` |
| 陷阱 | ① `NOT` 排除 MeSH 主词时会连带排除其整个下位词树；② 无引号的多词短语会被逐词 AND |

### Web of Science

| 项 | 语法 |
|---|---|
| 主题词 | `TS=(arsenic OR "urinary arsenic")`（TS = 标题+摘要+关键词） |
| 词根 | `arsenic*`（截词）；无叙词系统，MeSH 概念需人工转成自由词组 |
| 布尔 | `AND` `OR` `NOT`（在检索式内用 `NOT`） |
| 年份 | `PY=(1980-2024)` |
| 陷阱 | 短语必须加半角双引号；`NEAR/n` 算子可提高精度（人工决定是否用） |

### Scopus（Elsevier）

| 项 | 语法 |
|---|---|
| 主题词 | `TITLE-ABS-KEY(arsenic OR "inorganic arsenic")` |
| 布尔 | `AND` `OR` `AND NOT`（注意是 AND NOT） |
| 年份 | `PUBYEAR > 1979 AND PUBYEAR < 2025`（或 `PUBYEAR = 2020`） |
| 陷阱 | 通配符 `*` 在词尾截断；`AND NOT` 放错位置会排掉整个结果块 |

### Embase（Elsevier，Emtree 叙词）

| 项 | 语法 |
|---|---|
| 叙词 | `'arsenic'/exp`（扩展下位词）；`'arsenic'/de`（仅主词）；`'arsenic'/exp/mj`（主要主题词） |
| 自由词 | `'urinary arsenic':ti,ab,kw` |
| 布尔 | `AND` `OR` `NOT` |
| 年份 | `AND [1980-2024]` |
| 陷阱 | Emtree ≠ MeSH：MeSH 词需在 Emtree 中人工映射（`generate_search_terms.py` 只按同名草拟）；药物/疾病词比 MeSH 细得多 |

### CNKI（中国知网）

| 项 | 语法 |
|---|---|
| 主题 | 专业检索 `SU=('砷'+'总砷')*('尿'+'生物监测')-('小鼠')`（`+` 或 `*` 与 `-` 非） |
| 字段 | `SU=` 主题、`TI=` 篇名、`KY=` 关键词、`AB=` 摘要 |
| 年份 | 界面设置（发表时间控件），不进检索式字符串 |
| 陷阱 | ① `+*-()` 必须半角；② 硕博论文库、会议库需单独勾选；③ 命中数要在"专业检索"结果页读取 |

### 万方数据

| 项 | 语法 |
|---|---|
| 主题 | `主题:(砷) AND 主题:(尿)`；或经典检索界面逐词下拉"主题" |
| 字段 | `主题:`、`题名:`、`关键词:`、`摘要:` |
| 年份 | 界面设置 |
| 陷阱 | ① `主题:` 后的复合词组加半角引号；② 学位论文是万方强项，检索时可单选学位库补漏 |

---

## 2. 检索式草稿的人工核改清单（逐库）

- [ ] 同义词增删：删除与课题无关的宽义同义词，补漏目标词的拼写变体（`foetal/fetal` 等）
- [ ] 词根截断：在无叙词的库（WoS/Scopus/CNKI/万方）补 `*` 截词
- [ ] 叙词映射：Embase 的 Emtree 主词逐个人工核对（脚本只按同名草拟）
- [ ] 字段限定：是否收窄到 `[Title]`（低命中库）或保持 `[tiab]` / `TS=`
- [ ] NOT 块：逐组评估过度杀伤风险（见 `references/exclusion_word_bank.md`）
- [ ] 年份窗口：与 protocol 的发表年窗口一致（注意与主表采样年分期是两回事）
- [ ] 试运行：把最终检索式连同命中数记入检索记录（`source_summary.csv`）

---

## 3. 导出格式建议（哪个库用什么导出）

| 库 | 建议导出 | 导出字段 | 解析脚本 |
|---|---|---|---|
| PubMed | RIS（或 PubMed 格式） | 全部题录字段 + 摘要 | `parse_ris.py --source pubmed` |
| Web of Science | 制表符分隔 或 RefMan XML | 作者、标题、摘要、DOI、PMID… | `parse_enl.py --source wos`（TXT）或 `parse_ris.py` |
| Scopus | RIS | 引文信息 + 摘要 | `parse_ris.py --source scopus` |
| Embase | RIS | 引文信息 + 摘要 | `parse_ris.py --source embase` |
| CNKI | RefWorks / EndNote 导出（XML） | 题录 + 摘要（逐条勾选"摘要"） | `parse_enl.py --source cnki` |
| 万方 | RIS（EndNote 格式） | 题录 + 摘要 | `parse_ris.py --source wanfang` |

**导出注意事项**
1. 导出时必须包含**摘要**字段 —— 题摘筛（S2）依赖摘要；只导题名会迫使人工逐条回库。
2. 大结果集分批导出（多数库单次上限 500–1000 条），分批文件交给同一解析脚本后合并
   （`merge_sources.py` 会校验 record_id 冲突）。
3. 编码：中文库常见 GBK/ANSI 导出，用 `--encoding gbk`；西文库默认 `utf-8-sig`。
4. 导出原始文件（`.ris/.xml/.bib`）存入 `01-search/` 的只读原始区（routing.md §2.2：
   原始数据对所有子 Agent 只读），解析输出另存。
5. `.enl/.enlx` 是 EndNote 二进制库文件，**不能**作为解析输入 —— 先在 EndNote 中
   File → Export 为 XML 或制表符文本。
