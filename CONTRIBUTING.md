# 贡献指南（Contributing）

感谢你考虑为本项目贡献。本项目面向**做人体内暴露浓度 meta 分析的研究者**，
因此对正确性与可追溯性的要求高于一般开源项目。

---

## 0. 先读这三份文件

| 文件 | 为何必读 |
|---|---|
| `shared/data-contract.md` | **主表字段的唯一权威定义**。改字段 = 改契约，须走版本流程 |
| `agent/routing.md` | 6 个子 Agent 的路由表与**权限原则**；§2.6 是**源码改动纪律** |
| `shared/quality-gates.md` | 六道人工闸门。**AI 不得开启闸门**——这条是设计基石，不是建议 |

---

## 1. 贡献前：确认改动属于哪一类

| 改动类型 | 举例 | 需要 |
|---|---|---|
| **新增暴露物支持**（PFOA / 抗生素…） | 加摩尔质量、单位换算、领域陷阱 | 通常**只改 `project.yaml` + 领域词汇表**，不改脚本 |
| **新增基质** | 母乳 / 指甲 / 头发 | 改 `data-contract.md` §3.5 枚举 + `unit_policy` |
| **新增标记** | 新的 `inferred_flags` | ★ 见 §4「新增标记的四步」 |
| **修 bug** | 换算/公式/匹配错误 | 必须**先加一条能复现的自检断言**，再修 |
| **新增子技能** | S1 / S2（见 `STEP4-TODO.md` F5） | 需 SKILL.md + ≥1 个含 `--self-test` 的脚本 |
| **文档改进** | 表述、示例、错别字 | 直接提 PR |

---

## 2. ★ 源码改动纪律（必读，违反会被拒）

> 完整版见 **`agent/routing.md` §2.6**。以下是要点：

| # | 规则 |
|---|---|
| 1 | **禁止**用 PowerShell 的 `Get-Content -Raw` + `Set-Content` 往返改写源码文件 |
| 2 | 源码改动一律用**编辑工具**（编辑器 / IDE / `edit` 类工具），**不用 shell 文本替换** |
| 3 | shell 只用于**执行**与**只读检查** |
| 4 | 必须批处理时，用 **Python 全程读写**（`io.open(..., encoding="utf-8")`），不经 shell 管道 |
| 5 | 每次批量改动后**立即**跑语法检查 + 损坏字符探针 + 该文件 `--self-test` |

**违反后果**：源码会被**静默破坏**——自检可能仍通过（若损坏落在字符串里），
错误进入生产。本项目开发期真实发生过：一次 shell 往返把 `check`→`chdck`、
`with`→`gith`、`rows`→`rogs`，损坏 3 个文件，其中一个**无法逐字逆转，只能重建**。

本地可一键检查：

```bash
python tests/ci_local.py     # 含"源码卫生"检查：损坏字符 / 硬编码 / 兜底
```

---

## 3. 提交前必做

```bash
# 安装依赖
pip install -r requirements.txt

# ① 全部自检
python shared/project_config.py --self-test
python shared/inferred_flags.py --self-test
python shared/snapshot.py --self-test
# …其余脚本同理（或直接跑 ②）

# ② 本地 CI 预演（推荐：一步覆盖全部 5 类检查）
python tests/ci_local.py          # 应 5/5 通过

# ③ 结构校验
python tests/verify_structure.py  # 应 6/6 通过

# ④ 跨脚本一致性（改过 inferred_flags 相关代码时必跑）
python tests/verify_consistency.py
```

**PR 会被拒的情况**：
- 任一 `--self-test` 失败
- `tests/ci_local.py` 未 5/5
- 新增/修改的口径**没有走 `project.yaml`**（硬编码口径 = 兜底问题的变体）
- 含硬编码绝对路径（`E:\...` / `/Users/...`）
- 手写 CSV 模板（**必须**由 `gen_templates.py` 生成）

---

## 4. ★ 新增 `inferred_flags` 标记的四步

标记字典曾因"5 处各写一份"漏同步 2 处，现为**单一来源**。新增标记必须：

```bash
# ① 改单一来源（唯一必须手改的地方）
#    shared/inferred_flags.py  →  INFERRED_FLAGS 加一项 + CONTRACT_DOCUMENTED 加名
# ② 同步契约（面向使用者的权威定义）
#    shared/data-contract.md §6
# ③ 同步文档表
#    skills/S3-extraction-standardization/references/field_dictionary.md §8.5
# ④ 验证（会自动核对①②③是否一致）
python shared/inferred_flags.py --self-test
```

第 ④ 步会检查：
- 注册表内部一致性（名称唯一 / 字段齐全 / 计数自洽）
- **使用处覆盖核对**（契约 §6、字段字典 §8.5、反推规则表、两个脚本是否都覆盖）
- **文档项数表述一致性**（凡写「N 项字典」的文档，N 必须等于注册表项数）

> 若你在**脚本里自建一份字典副本**，`verify_consistency.py` 的 C1 会直接判失败。

---

## 5. 新增子技能的规范

目录：`skills/S<n>-<kebab-name>/`，必须含：

```
SKILL.md          # frontmatter + 加载条件 + 输入输出契约 + 流程 + 规则 + 接口
references/       # 方法学说明（可被 SKILL.md 引用）
scripts/          # 可执行脚本，**每个都必须有 --self-test**
templates/        # 输入模板（不带 _OUTPUT）或输出示例（带 _OUTPUT_example）
```

**SKILL.md frontmatter 必须含**：

```yaml
---
name: hbm-<kebab-name>          # 必须等于 "hbm-" + 目录名去掉 "S<n>-" 前缀
description: "…方法学触发词…"     # ≥80 字符；方法学触发，不绑死具体暴露物
license: MIT (code) / CC-BY-4.0 (docs)
compatibility: opencode claude-code dsh
allowed-tools: [Read, Write, Edit, Grep, Glob, Bash, ...]
metadata:
  parent_skill: hbm-meta
  version: "1.0.0"
  stage: <n>
  gate: GATE-<n>                # 无独立闸门时写 null 并说明由哪个闸门承接
---
```

`tests/verify_structure.py` 会自动校验以上各项。

---

## 6. 脚本规范

| 要求 | 说明 |
|---|---|
| **必须有 `--self-test`** | 用内置脱敏夹具，**不依赖网络**、不依赖外部文件 |
| **CLI 三参数** | `--input` / `--config` / `--out`（+ 按需 `--trace` 等） |
| **退出码** | `0` 成功｜`1` 存在差异/告警（**设计内的状态报告**）｜`2` 前置条件不满足 |
| **配置禁用兜底** | `project.yaml` 缺失/解析失败/缺区块 → 抛 `ConfigError` → 退出码 2 |
| **口径零字面量** | 分期 / 地区 / 基质 / 单位 / ICRP 参数 / EDI 参数全来自配置 |
| **标记走单一来源** | `from inferred_flags import …`，**不得自建副本** |
| **输出带快照指纹** | 用 `shared/snapshot.py`（格式见 `data-contract.md` §4） |
| **自检要含反例** | 不只测正向路径，要测**边界与失败**（如"q3<q1 必须 FAILED"） |

---

## 7. CSV 模板：禁止手写

**必须**由生成脚本产出：

```bash
python skills/S5-weighted-pooling-edi/scripts/gen_templates.py --write   # 生成
python skills/S5-weighted-pooling-edi/scripts/gen_templates.py --check   # 校验（CI 会跑）
```

`examples/` 的样例表同理（`examples/arinic-china-1980-2024/make_example_table.py`）。

**原因**：手写模板在开发期连续出错三次（列错位、数值与代码不一致）

---

## 8. 数据与隐私红线（★ 不可协商）

| 红线 | 说明 |
|---|---|
| **不得提交原始研究数据** | `*.xlsx` / `*.sav` / `*.pdf` 等已在 `.gitignore` 中排除 |
| **不得提交可识别信息** | 具体研究标题 / 作者 / DOI / 省份级真实数值 |
| **不得提交凭证** | 账号密码等（`.gitignore` 有防护，但请勿依赖它） |
| **示例必须脱敏** | 见 `examples/*/make_example_table.py` 的脱敏规则与自检 |
| **不得声明"AI 可开启闸门"** | 六道闸门**只由人开启**——这是设计基石 |

> 若发现仓库中已有敏感信息，**请勿在 Issue 中贴出**；直接联系维护者。

---

## 9. 提交信息与 PR

- **Commit**：一句话说清"改了什么 + 为什么"。修 bug 请引用对应自检断言。
- **PR 描述**请包含：
  1. 改动类型（见 §1 表格）
  2. `python tests/ci_local.py` 的输出摘要
  3. 若改了契约/标记：是否已同步契约与文档（§4 四步）
  4. 若修 bug：**新增的那条复现断言**是哪个

---

## 10. 需要帮助的领域

见 `STEP4-TODO.md` 的 **D2 · Future enhancements**：

| 编号 | 内容 |
|---|---|
| **F5** | ★ **实现 S1 / S2**（目前只有目录骨架） |
| F1 | 实现 Wan 2014 的 `plus` 变体（当前显式拒绝） |
| F2 | 脐血 EDI 的独立参数表（方案 B） |
| F3 | `.docx` / `.enl` / `.ris` 解析器 |
| F4 | `weighting.method = inverse_variance` |

---

感谢贡献。**请记住：本工作包的每一处"自动"，都必须有一处"人工裁决"与之配对。**
