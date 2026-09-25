# 第四步待办清单（脚本通用化改造）

> 状态：**P0 已完成（见 §0）**，P1–P3 待执行
> 本文件记录第三步刻意留下的骨架妥协，以及必须在第四步清偿的技术债。
> **第四步未完成前，不得对外宣称脚本可用于正式分析。**

---

## 0. 第四步优先级（用户确认的顺序）

| 优先级 | 项 | 状态 |
|---|---|---|
| **P0** | `audit_numbers.py` 接入 `--master-table`（真正重算） | ✅ **已完成**（62/62 自检通过，端到端实测 OK/MISMATCH 均正确） |
| **P0** | `citation_crosswalk.py` 后缀消歧（精确命中，不再误报 AMBIGUOUS） | ✅ **已完成**（48/48 自检通过） |
| **P1** | `dedup_screen.py` STRATEGY_S2 改用 `scheme` 分期 | ✅ **已完成**（41/41 自检通过，含"分期边界变化时影响随之变化"断言） |
| **P1** | A1–A6 硬性阻断项（含 `project.yaml` 硬错误、移除全部兜底） | ✅ **已完成**（A1–A5 见下；A6 见 §C） |
| **P2** | `examples/arsenic-china-1980-2024/` 脱敏案例（开源项目必须有可跑通样例） | ✅ **已完成**（8 步端到端实测通过；含脱敏规则自检） |
| **P2** | CI + `requirements.txt` | ✅ **已完成**（`validate.yml` / `requirements.txt` / `.gitignore` / `tests/ci_local.py`） |
| **P3** | README（中英双语）/ LICENSE / CITATION.cff / CONTRIBUTING / CHANGELOG | ✅ **已完成** |

### P2 / P3 完成状态

| 项 | 状态 | 验收证据 |
|---|---|---|
| **P2** 脱敏案例 | ✅ | `examples/arsenic-china-1980-2024/` 含 README（5+ 命令）、检索式 + 排除词银行、72 列表头 + 12 条脱敏记录、审计与返修清单样例、`project.yaml`；`make_example_table.py --self-test` **48/48**；**端到端 8 步实测通过** |
| **P2** CI | ✅ | `tests/ci_local.py` 预演 **5/5**；`verify_structure.py` **6/6**；`verify_consistency.py` **6/6**；注入失败被 **2/2** 拦截 |
| **P3** 发布文件 | ✅ | `README.md`（中英双语）· `LICENSE`（MIT + CC BY 4.0 双许可）· `CITATION.cff` · `CONTRIBUTING.md` · `CHANGELOG.md` |
| 结构校验覆盖面 | ✅ | `TOP_LEVEL_REQUIRED` 扩至 **21 项**（含全部 P3 文件） |

### 链式集成缺陷（P2 期间发现并修复）

| 缺陷 | 后果 | 修复 |
|---|---|---|
| `wan_convert.convert_record` **不保留输入行的单位换算段** | 链式运行（`convert_units` → `wan_convert`）时 `derive_flags` 看不到 `ICRP89` 段 → **7/12 记录 `FLAG_MISMATCH`** | 新增 `merge_prior_path()` / `merge_prior_flags()`；内部实现只输出自己的段，路径由外层统一合并 |
| `derive_flags` 对 `reported_GM` 未早退 | 零估算记录被叠加统计量类标记 | 新增 `UNIT_ONLY_RULES` + `reported_GM` 早退分支 |
| `Median_as_GM` 规则不完整 | `as_gm` 策略下丢失 `gm_from_iqr` / `gm_from_range` | 拆为 `Median_IQR→Median_as_GM` 与 `Median_Range→Median_as_GM` 两条规则 |
| `validate_table` 的 `unit_policy` 传嵌套字典 | 单位口径校验**永远失败**（10 条误报 ERROR） | `read_config` 归一化为「基质 → 目标单位字符串」；加 `unit_policy 归一化` 自检 |
| 比重校正记录的单位被误判 | R00004 误报 ERROR | 改判为 WARN（保留原单位属设计行为，进"校正方式"分层） |
| `convert_units` 只取 `initial_gm` | 仅报告中位数的记录被误判为"缺值" | 回退链 `initial_gm → initial_median → initial_mean`；痕迹表加 `value_field` 列 |

**回归防护**：`wan_convert.py` 新增 `[20] 链式兼容` 断言（5 个场景 × 4 项 = 20 项），
覆盖路径权威性 + 标记一致性 + 无混排 + 标记数。

### A1–A6 完成状态

| 项 | 状态 | 验收证据 |
|---|---|---|
| **A1** 配置缺失 → 硬错误 | ✅ | 7 个脚本在"无 `--config`"与"坏 YAML"下均退出码 2；`shared/project_config.py` 45/45 |
| **A2** 移除硬编码路径 | ✅ | 探针扫描 16 个 `.py` 无命中 |
| **A3** 口径全部来自配置 | ✅ | `wan_variant` / `median_to_gm_strategy` / `creatinine_path_mode` 真正生效（曾"读了无效"，已修）；`plus` 变体显式拒绝 |
| **A4** 输出带快照指纹 | ✅ | `shared/snapshot.py` 44/44；`convert_units.py` 89/89，报告头部含 §4 指纹块 |
| **A5** 反推一致性（验收标准） | ✅ | `tests/verify_consistency.py` **6/6**；`audit_numbers.py` 已迁移到共享 `Snapshot`（93/93） |
| **A6** CI | ✅ | `tests/ci_local.py` 预演 **5/5**；注入断言失败被 **2/2** 拦截（`tests/inject_failure.py`） |

---

## 0.1 P0/P1 已完成项的验收证据

| 项 | 验收 |
|---|---|
| `--master-table` | 端到端：`OK=1 MISMATCH=1`；第 2 行为 `5.99 vs 4.2400（41.27%）→ MISMATCH` |
| 线索模式分层推断 | 最具体优先（时段 > 地区 > 人群）；词边界匹配避免 `east`⊂`Northeast`；脐带血优先于血 |
| 按句切分 | 断言"第二句不继承第一句的 period 线索" |
| 后缀消歧 | `2024a → L08`、`2024b → L09` 精确命中；无后缀 → `AMBIGUOUS`；顺序反转 → 后缀互换（证明必须排序） |
| S2 分期 | 断言"分期边界变化时 S2 影响随之变化"（默认保留 2 vs 细分保留 4） |
| 附带修复 | `Study.year` 取自 `t_publication` → 改为 `time`（采样年），符合契约 §3.2 硬约束 |

---

## A. 硬性阻断项（必须修复，否则脚本不可用于正式分析）

| A1 · `project.yaml` 兜底必须改为硬错误　★ 用户明确要求 | **✅ 已完成** |

| 项 | 内容 |
|---|---|
| 现状 | `validate_table.py` 的 `read_config()` 在 `project.yaml` **缺失或解析失败**时回退到内置 `DEFAULT_CONFIG` |
| 问题 | 用默认值继续会得到"**看起来跑通了，但用的是错误的口径**"的结果——最危险的一类失败 |
| 要求 | `project.yaml` 缺失 → **硬错误，退出码 2**；解析失败 → **硬错误，退出码 2**；**不允许任何兜底** |
| 影响文件 | `skills/S3-extraction-standardization/scripts/validate_table.py`（其余脚本同规则） |
| 验收 | `python validate_table.py --input x.csv --config 不存在的.yaml` → 退出码 2 + 明确错误信息，且**不产出报告** |
| 例外 | `--self-test` 时可使用内置夹具配置（自检不依赖外部配置） |
| 附加要求 | 失败信息必须指明：期望路径、实际查找过的路径、以及"请先完成 GATE-0 生成 project.yaml" |
| **实现** | 新增 `shared/project_config.py` 作为**唯一配置加载入口**（含 `ConfigError`）；逐个脚本切换 |
| **实测验收** | 7 个脚本在**无 `--config`** 与**坏 YAML** 两种情形下均返回**退出码 2** ✅ |

#### A1 切换台账（逐个切换并跑 `--self-test` 确认）

| # | 脚本 | 自检 | 硬错误验收 |
|---|---|---|---|
| 1 | `shared/project_config.py`（新建） | 36/36 | — |
| 2 | `S3/scripts/validate_table.py` | 8/8 | ✅ |
| 3 | `S4/scripts/convert_units.py` | 59/59 | ✅ |
| 4 | `S5/scripts/weighted_gm.py` | 35/35 | ✅ |
| 5 | `S5/scripts/edi_calculation.py` | 54/54 | ✅ |
| 6 | `S6/scripts/audit_numbers.py` | 73/73 | ✅ |
| 7 | `S6/scripts/dedup_screen.py` | 42/42 | ✅ |
| 8 | `S6/scripts/sensitivity.py` | 46/46 | ✅ |
| — | `S5/scripts/gen_templates.py`（随动） | 29/29 | — |
| — | `S4/scripts/wan_convert.py`（不涉口径） | 48/48 | — |
| — | `S6/scripts/verify_dois.py` · `citation_crosswalk.py`（不涉口径） | 44/44 · 48/48 | — |

**约束**：`DEFAULT_*` 常量全部改名 `FIXTURE_*`（仅自检使用）；`project_config.py` 自带断言"代码中不得再出现 `DEFAULT_*` 常量"。

---

#### ⚠️ 操作纪律 → 已提升为永久规则

**源码改动纪律**已移至 **`agent/routing.md` §2.6**（主 Agent 每次进入流程时必读的编排规则），
因为它不是"待办"而是"永久规则"。

事故复盘（供参考）：一次用 PowerShell `Get-Content -Raw` + `Set-Content` 往返批量替换时，
PowerShell 的 UTF-8 往返做了字符替换（`check`→`chdck`、`with`→`gith`、`rows`→`rogs`、
`weighted`→`geighted`、`False`→`Falsd`）并加了 BOM，损坏 3 个文件；
其中 `gen_templates.py` 无法逐字逆转，只能**重建**。

**修复结果**：3 个文件已用 `write` 重写，全项目探针扫描通过，12 个脚本 **522 项断言全绿**。

### A2 · 移除全部硬编码路径

| 项 | 内容 |
|---|---|
| 现状 | 原项目脚本（`audit_numbers.py`、`dedup_screen2.py` 等）内含 `E:\桌面\重金属暴露研究\…` 等绝对路径 |
| 要求 | 所有输入输出路径经 `--input` / `--out` / `project.yaml` 传入；源码中零绝对路径 |
| 验收 | `grep -rn "E:\\\\" scripts/` 与 `grep -rn "重金属暴露" scripts/` 均无命中 |

### A3 · 口径参数全部从 `project.yaml` 读取

| 参数类别 | 现状 | 目标 |
|---|---|---|
| 分期方案（`period_scheme`） | 硬编码在 `period()` 函数（`1980–2024` 四段） | 从配置读取 |
| 地区字典（`region_map`） | 省级名单硬编码 | 从配置读取 |
| 基质枚举（`matrix_scope`） | 硬编码 `Urine`/`Blood`/`CordBlood` | 从配置读取 |
| 单位口径（`unit_policy`） | `DEFAULT_CONFIG` 内联 | 从配置读取 |
| 验证阈值（`verified_ratio_threshold`） | 内联默认 `0.90` | 从配置读取 |
| ICRP 89 参数表 | 待实现（S4） | 从配置读取，配置缺失则硬错误 |
| EDI 参数 | 待实现（S5） | 从配置读取 |

**验收**：把 `project.yaml` 的分期边界从 `1980-2000` 改为 `1980-1999` 后重跑，所有分层结果随之改变（证明无硬编码残留）。

### A4 · 输出必须带快照指纹

| 项 | 内容 |
|---|---|
| 现状 | 报告仅有数据源路径与记录数 |
| 要求 | 每份报告/图表/表格携带 `shared/data-contract.md` §4 定义的完整指纹：`source_table`、`sha256`、`generated_at`（含时区）、`config_version`、`contract_version`、`record_count`、`study_count`、`generator`、`git_commit` |
| 用途 | 支撑"数值审计"判定 `STALE_MANUSCRIPT`（手稿引用的快照哈希与当前不一致即直接判定过时） |
| 验收 | 同一输入重复运行 → 除 `generated_at` 外指纹完全一致；改动主表 1 字节 → `sha256` 变化 |

### A5 · `inferred_flags` 自动登记逻辑必须可审计　★ 用户明确要求

| 项 | 内容 |
|---|---|
| 要求 | **能从 `conversion_path` 反推出该登记哪些标记**；登记过程本身可复现、可复核 |
| 设计约束 | `conversion_path` 采用结构化可解析编码，例如 `AM_SD→Wan_S1→GM`、`ug/L→ICRP89_Adult→ug/g Cr` |
| 反推映射 | 路径含 `Wan_S*` → 对应 `gm_from_mean_sd` / `gm_from_median` / `gm_from_iqr` / `gm_from_range`；含 `ICRP89` 或 `unit_converted` → `unit_converted_creatinine`；含 `time_represented` 步骤 → 对应标记 |
| 审计要求 | 输出一份 `flags_derivation.csv`：`record_id | conversion_path | 登记标记 | 反推规则 | 依据字段`，人工在 GATE-4 逐条复核 |
| 双向校验 | 人工在 GATE-4 手工增删的标记须能反向解释；无法解释的标记报 `WARN` |
| 验收 | 给定任意 `conversion_path`，脚本能确定性输出标记集合，且同一输入两次运行结果一致 |

### A6 · CI 集成 `--self-test`

| 项 | 内容 |
|---|---|
| 要求 | `.github/workflows/validate.yml` 对全部脚本执行 `--self-test`；任一失败则 CI 失败 |
| 附加 | 同时校验 `SKILL.md` frontmatter（`name` 存在、`description` 非空）与目录结构完整性 |
| 验收 | 故意注入一个断言失败 → CI 变红 |

---

## B. 质量增强项（不阻断，但影响可用性）

| # | 项 | 说明 |
|---|---|---|
| B1 | 日志格式统一 | 当前各脚本自由格式；改为统一的 `[LEVEL] rule_id record_id detail` |
| B2 | 错误处理细化 | 区分"文件不存在""格式不支持""权限不足""校验失败"四类，各自明确退出码 |
| B3 | `--dry-run` | 换算与合并支持只报告不写文件 |
| B4 | `--strict` | 把 `WARN` 升级为 `ERROR`（用于投稿前终检） |
| B5 | 大表性能 | `.xlsx` 读取改用 `read_only=True` 全面覆盖（当前 `validate_table.py` 已用） |
| B6 | 编码兼容 | Windows GBK 控制台（已在 `validate_table.py` 用 `sys.stdout.reconfigure` 处理，其余脚本同样处理） |
| B7 | PyYAML 依赖 | 当前缺失时回退默认配置（与 A1 冲突）→ 第四步把 PyYAML 写入 `requirements.txt` 并改为必需 |
| B8 | 报告模板化 | 校验报告/换算报告统一用 `templates/` 下的模板渲染 |

---

## B2. CI 与模板校验（第四步新增，来自开发期实证）

| # | 项 | 说明 |
|---|---|---|
| C1 | CI 集成 `--self-test` | `.github/workflows/validate.yml` 对全部脚本执行 `--self-test`；任一失败则 CI 失败 |
| C2 | **CSV 模板列对齐 + 数值一致性校验** ★ | `gen_templates.py --check` 已实现，需接入 CI。**模板禁止手写**——开发期手写模板连续出错三次：`extraction_template.csv` R00001 漏 `population` 致整行左移 1 列、R00002/R00003 多 1 列、`edi_results.csv` Pregnant 相对差被写成 0（实际 0.0784%） |
| C3 | SKILL.md frontmatter 校验 | `name` 存在、`description` 非空、`metadata.parent_skill` 指向有效技能 |
| C4 | 目录结构完整性校验 | 6 个子技能均含 `SKILL.md` + `references/` + `scripts/` + `templates/` |
| C5 | 故意注入失败验证 CI 变红 | 确认 CI 确实能挡住问题，而非永远绿 |
| C6 | `__pycache__` / `*.pyc` 排除 | 已入 `.gitignore`，需确认 CI 不因缓存产生误判 |

---

## C. 待创建的脚本（第四步）

| 脚本 | 用途 | 来源 | 状态 |
|---|---|---|---|
| `audit_numbers.py` | 数值审计（手稿 vs 快照） | 改写自项目 `_tmp/audit_numbers.py` | 待创建 |
| `dedup_screen.py` | 队列/同城聚簇去重筛查 | 改写自 `_tmp/dedup_screen2.py` | 待创建 |
| `sensitivity.py` | 敏感性分析（校正方式分层 / 仅直接 GM 子集） | 改写自 `_tmp/sensitivity.py` | 待创建 |
| `verify_dois.py` | DOI / Crossref 核验 | 改写自 `_tmp/verify_dois.py` | 待创建 |
| `citation_crosswalk.py` | 正文引用 ↔ 文献库对照 | 新增 | 待创建 |
| `weighted_gm.py` | 加权 GM/GSD 分层合并 | **原 R 改写为 Python** | 待创建（S5） |
| `edi_calculation.py` | EDI 计算（尿路 + 血路） | **原 R 改写为 Python** | 待创建（S5） |
| `wan_convert.py` | Wan 2014 S1–S7 换算 | **原 R 改写为 Python** | **第三步已创骨架** |
| `convert_units.py` | 单位换算与尿校正 | 新增 | **第三步已创骨架** |
| `validate_table.py` | 契约校验 | 新增 | **第三步已创骨架** |

---

## D. 算法保真要求（第四步不可更改）

用户明确要求：**原 R 脚本里验证过的数学逻辑必须原样保留，只改语言，不改算法。**

| 算法 | 保真要点 |
|---|---|
| 加权 GM | `GM = exp(Σ wᵢ·ln(GMᵢ) / Σ wᵢ)`，权重 `wᵢ = sample_size`（自然对数与 log10 在加权 GM 中等价，统一用自然对数） |
| 加权 GSD | `GSD = exp( sqrt( Σ wᵢ·(ln GMᵢ − ln GM_w)² / Σ wᵢ ) )` |
| 尿 EDI（肌酐排泄法） | `EDI = GM × CE / (BW × ABS)`；参数：Adults `CE=1.35, BW=60`；Pregnant `CE=1.275, BW=60`；Minors `CE=0.925, BW=20`；`ABS=0.7` |
| 尿 EDI（尿量法） | `EDI = GM × CC_creat × V / (BW × ABS)`；参数：Adults `CC=0.964, V=1.4`；Pregnant `CC=0.638, V=2.0`；Minors `CC=0.815, V=1.05` |
| 血 EDI（**修正后公式**） | `EDI = BAs × Vd / (ABS × τ)`，`Vd=2.0 L/kg`（已按体重标准化，**不再除以体重**），`ABS=0.7`，`τ=1.0 d` |
| 血 EDI 旧公式 | `EDI = BAs × Vd / (BW × ABS × τ)` —— **已被证伪，不得使用**（单位错误，见 `11_修改及参考` R1-1） |
| ICRP 89 参数选择 | 按 `population_group` 与 `sex` 取；无性别信息时用该人群的通用值（见 `icrp89_urine_reference.md`） |
| 分期边界 | 由 `project.yaml` 决定；默认冻结为 `1980-2000 / 2001-2010 / 2011-2020 / 2021-2024` |

**验收**：用原项目的定稿快照跑 Python 版，结果需与 R 版数值一致（允许浮点末位差异）。已知校验点：尿砷时段加权 GM `32.58 / 12.45 / 23.50 / 23.52`；血砷 `2.72 / 1.99 / 1.39`；尿 EDI `2.13 / 0.57 / 0.83 / 0.84`。

---

## D2. Future enhancements（后续增强，不属于 P0–P3）

> 已识别的**功能缺口或可选扩展**，不影响当前可用性，故不排入本轮优先级。
> 每项须在实现时补自检，并与现有断言一致。

### F1 · 实现 Wan 2014 的 `plus` 变体　（当前：显式拒绝）

| 项 | 内容 |
|---|---|
| 现状 | `WAN_VARIANTS_IMPLEMENTED = {"minus"}`；`xi()`/`eta()` 对 `plus` 显式抛 `NotImplementedError`；`project_config` 的 `standardization.wan_variant.allowed = ["minus"]` |
| 为何保持现状 | ① 主流实现（`metamedian`、`estmeansd`）默认用减号变体；② 本项目原 R 脚本用减号，Python 版与之保持一致；③ 未实现却"读了无效"会构成**静默失败**，显式拒绝是最安全姿态 |
| 缺口本质 | `xi()` 目前只返回幅值 `\|2Φ⁻¹(p)\|`。Wan 2014 的 S1–S5 以 `±` 形式给出，两变体在 n 较小时数值分支不同（论文中 B/C/D 参数含 ±） |
| 实现步骤 | 1. 展开论文 B、C、D 参数的 ± 分支（核对 BMC Med Res Methodol 2014;14:135）<br>2. 在 `xi()`/`eta()` 中按 `variant` 选择分支<br>3. **新增对照自检**：n=2/10/100/1000 下两变体的 SD 差异方向与幅度应符合论文<br>4. 把 `"plus"` 加回 `WAN_VARIANTS_IMPLEMENTED`<br>5. 把 `project_config.REQUIRED_STANDARDIZATION_KEYS["wan_variant"]["allowed"]` 改回 `["minus", "plus"]`<br>6. 同步 `templates/project.yaml` 注释与 `wan2014_formulas.md` §3<br>7. 全量重跑自检确认无回归 |
| 验收 | `variant="plus"` 得到与 `minus` **不同**的 SD 且方向符合论文；`project_config` 接受 `plus` |

### F2 · 脐血 EDI 的独立参数表（方案 B）

| 项 | 内容 |
|---|---|
| 现状 | 走**方案 A**：脐血不计算 EDI，标 `cordblood_edi_skipped` 并计数（见 S5/SKILL.md §5.2b） |
| 为何不实现 | 需胎儿/新生儿药代动力学参数（`Vd`、`τ`、经胎盘转运模型、适用胎龄、不确定性），须专门文献与专家审定 |
| 实现步骤 | 1. 收集并审定胎儿/新生儿参数及出处<br>2. `project.yaml → edi.cordblood_route` 新增参数块（含 `source`、适用胎龄、不确定性）<br>3. `edi_calculation.compute_blood_edi` 增加 `cordblood_route` 分支<br>4. 新增自检 + 与方案 A 的对照说明<br>5. 把 `cordblood_edi_skipped` 改为可选 |

### F3 · 真实文档格式解析器

| 项 | 内容 |
|---|---|
| 现状 | `audit_numbers.py` 用占位符/线索双模式处理 `.md`/`.txt`；`.docx` 需先转换；文献库须先导出 CSV |
| 待补 | `.docx` 原生解析（含修订痕迹与批注）；`.enl`/`.ris`/`.bib` 文献库解析 |
| 实现步骤 | 引入 `python-docx` 与 RIS/BibTeX 解析；**保持"AI 只产差异、人裁决"边界不变** |

### F4 · `weighting.method = inverse_variance`

| 项 | 内容 |
|---|---|
| 现状 | 仅实现 `sample_size` 加权；`project.yaml` 可声明 `inverse_variance` 但脚本未实现 |
| 实现步骤 | 实现 `wᵢ = 1/SEᵢ²` 分支（需 `initial_sd` + `sample_size` 算 SE）；对缺失 SE 的记录给出明确排除计数；加自检对照两种权重的结果差异 |
| 注意 | 与 A3 同原则：**声明了但未实现的口径必须显式拒绝**，不得静默回退到 `sample_size` |

---

### F5 · 实现 S1 / S2 两个子技能　★ 发布前须登记（以免误导贡献者）

| 项 | 内容 |
|---|---|
| **当前状态** | S1（`skills/S1-search-strategy/`）与 S2（`skills/S2-screening-quality/`）**只有目录骨架**（含 `references/` `scripts/` `templates/` 三个空目录），**没有 SKILL.md，也没有任何实现** |
| **为何登记** | `tests/verify_structure.py` 把 S1/S2 列入 `PLANNED_SUB_SKILLS` 并标注"规划中不校验"，因此对外会显示 **"结构校验 6/6 通过"**。若不登记，未来的贡献者会以为 6 个子技能都已完整实现 —— **这就是误导** |
| **S1 实现内容** | ① **检索策略生成**：数据库语法差异表（PubMed `NOT` 陷阱 / WoS `TS=` / CNKI `TKA=` 与 `SU=` / 万方）、曝光物×基质×人群×暴露评估四维词表拼装法<br>② **排除词银行**（四类分组）：动物 · 细胞机制 · 职业 · 临床；每组附适用条件与"过度杀伤"预警<br>③ **检索充分性诊断**：命中量级是否落在该领域合理区间；过少→放宽基质，过多→加排除词<br>④ 产出 `检索记录.csv`（库/检索式原文/日期/命中数）→ 直接生成 PRISMA Identification 数字<br>⑤ scripts：`tally_records.py`（命中去重统计，需含 `--self-test`） |
| **S2 实现内容** | ① **PRISMA 2020 两阶段流程** + 数字自洽校验（各阶段数字必须能对上账）<br>② **AHRQ 11 条量表**逐条判"是"的标准 + 分期校准（早期研究检测方法弱不该被机械扣分）<br>③ **三类偏倚正反例库**（选择 / 测量 / 混杂；含尿校正、血基质两个高频坑）<br>④ **三类边界人群疑似清单**：职业暴露 · 疾病/病区人群 · 混合人群（AI 只列证据，人工逐条裁决）<br>⑤ **同队列/同城聚簇清单**（复用 S6 的 `dedup_screen.py` 聚类逻辑）<br>⑥ 产出 `纳入流程图`、`质量评分表`、`疑似排除清单.md`<br>⑦ scripts：`flag_scan.py`（标题级否定词扫描，需含 `--self-test`） |
| **实现后须同步** | 1. 把 S1/S2 从 `tests/verify_structure.py` 的 `PLANNED_SUB_SKILLS` 移入 `IMPLEMENTED_SUB_SKILLS`<br>2. 在 `SKILL.md`（根）§5 触发条件表补 S1/S2 的加载触发词与产出物<br>3. 在 `agent/routing.md` §1 路由表中确认 A1→S1、A2→S2 已就位（已就位，仅需核对）<br>4. 在 CI（`validate.yml` 与 `tests/ci_local.py`）把新增脚本加入 `SELFTEST_TARGETS`<br>5. 在 `examples/` 案例中补 S1/S2 的脱敏示例（可选） |
| **验收** | S1/S2 各有 SKILL.md（含 frontmatter：name / description / metadata.parent_skill=hbm-meta / `metadata.stage`）；各有 ≥1 个含 `--self-test` 的脚本；`verify_structure.py` 在两者移入已实现列表后仍 6/6 通过 |

---

## E. 完成定义（Definition of Done）

第四步视为完成，当且仅当：

- [ ] A1–A6 全部修复并有验收证据
- [ ] C 表全部脚本创建完成，均含 `--input` / `--config` / `--out` / `--self-test`
- [ ] 所有 `--self-test` 通过（CI 绿）
- [ ] `requirements.txt` 完整（`pandas` `numpy` `scipy` `openpyxl` `PyYAML` …），无 R 依赖
- [ ] D 表算法保真验收通过（与 R 版数值一致）
- [ ] B 表中 B1–B4 完成

---

**维护者**：HBM-Meta-Agent　**建立时间**：第三步（S3）完成时　**关联**：`skills/S3-extraction-standardization/SKILL.md` §6
