#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_search_terms.py — 从 project.yaml 生成各数据库检索式草稿（S1 检索策略 / 功能 B）

定位
  产出的是**草稿**（DRAFT）：检索式的最终形态、执行检索、记录命中数全部由人工完成，
  人工确认检索充分性后才能过 GATE-1（见 shared/quality-gates.md）。
  本脚本不联网执行任何检索。

输入（project.yaml → search 区块 —— **条件必需**，使用本脚本时缺失即硬错误）
  search:
    databases: [...]            # 允许：PubMed / WebOfScience / Scopus / Embase / CNKI / Wanfang
    year_from: 1980             # 发表年窗口（注意：与 period_scheme 的采样年分期是两回事）
    year_to: 2024
    analytes:                   # 每项：{name, synonyms, [mesh], [synonyms_zh]}
      - {name: "arsenic", mesh: ["Arsenic"], synonyms: [...], synonyms_zh: [...]}
    matrices:                   # 每项：{name, synonyms, [mesh], [synonyms_zh]}
    exposure_terms: [...]       # 生物监测/内暴露等检索词
    population_keywords: [...]  # 人群限定词
    exclusion_terms:            # 可选；四组：animal / cell / occupational / clinical
      animal: [...]
      cell: [...]
      occupational: [...]       # ⚠️ NOT occupational 会连带排除混合人群研究（见输出警告）
      clinical: [...]

输出
  search_strategy_draft.md —— 各数据库检索式草稿 + 检索词表 + GATE-1 人工检查清单。
  中文库（CNKI/万方）需要 synonyms_zh；未提供时该库输出"待人工补充中文检索词"
  占位（不编造中文词）。

用法
  python generate_search_terms.py --config project.yaml --output search_strategy_draft.md
  python generate_search_terms.py --self-test

退出码
  0 成功   2 配置错误（缺 --config / project.yaml 缺失 / 缺 search 区块 / 取值非法）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# shared/ —— 唯一配置入口（禁止本脚本自带口径默认值）
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from project_config import ConfigError, load_config  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/project_config.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. search 区块校验（显式拒绝，不做兜底）
# ===========================================================================

REQUIRED_SEARCH_KEYS = ["databases", "analytes", "matrices", "population_keywords",
                        "exposure_terms", "year_from", "year_to"]

KNOWN_DATABASES = ["PubMed", "WebOfScience", "Scopus", "Embase", "CNKI", "Wanfang"]

CHINESE_DATABASES = {"CNKI", "Wanfang"}

EXCLUSION_GROUPS = ["animal", "cell", "occupational", "clinical"]

# 排除词银行的"过度杀伤"预警（方法学固定文案，非口径）
OVERKILL_WARNINGS = {
    "occupational": "⚠️ NOT occupational 连带排除『职业与非职业混合调查』研究 ——"
                    "这类研究可能仅报告了可用的非职业亚组。是否使用职业排除词、"
                    "排除后改为人工逐条裁决，请在 GATE-1 明确记录。",
    "animal": "⚠️ 动物排除词可能杀伤含人体亚组的文献（如标题同时提及鼠与人群），"
              "命中量异常偏小时优先放宽本组。",
    "cell": "⚠️ 细胞机制排除词对流行病学研究命中极少；若全文筛阶段发现大量机制研究"
            "混入，再回补本组。",
    "clinical": "⚠️ 临床排除词可能杀伤登记队列/病房招募的一般人群研究，谨慎使用。",
}


def _as_str_list(value, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"search.{where} 必须是非空列表。")
    out = []
    for item in value:
        s = str(item).strip()
        if not s:
            raise ConfigError(f"search.{where} 含空字符串项。")
        out.append(s)
    return out


def _validate_term_bank(bank, where: str) -> list[dict]:
    """analytes / matrices 词表：每项 {name, synonyms, [mesh], [synonyms_zh]}。"""
    if not isinstance(bank, list) or not bank:
        raise ConfigError(f"search.{where} 必须是非空列表。")
    out: list[dict] = []
    for i, item in enumerate(bank):
        where_i = f"{where}[{i}]"
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ConfigError(f"search.{where_i} 必须是含 name 的映射。")
        entry = {
            "name": str(item["name"]).strip(),
            "synonyms": _as_str_list(item.get("synonyms"), f"{where_i}.synonyms"),
            "mesh": _as_str_list(item["mesh"], f"{where_i}.mesh")
                    if item.get("mesh") else [],
            "synonyms_zh": _as_str_list(item["synonyms_zh"], f"{where_i}.synonyms_zh")
                           if item.get("synonyms_zh") else [],
        }
        out.append(entry)
    return out


def validate_search(search: dict) -> dict:
    """校验 search 区块。任一问题 → ConfigError（硬错误，不做兜底）。"""
    if not isinstance(search, dict):
        raise ConfigError("search 区块必须是映射。")
    missing = [k for k in REQUIRED_SEARCH_KEYS if k not in search]
    if missing:
        raise ConfigError(
            f"project.yaml 缺少 search 区块的必需键：{', '.join(missing)}。"
            "模板见 templates/project.yaml 的 search 区块。")

    databases = _as_str_list(search["databases"], "databases")
    unknown = [d for d in databases if d not in KNOWN_DATABASES]
    if unknown:
        raise ConfigError(
            f"search.databases 含未知数据库 {unknown}。"
            f"当前支持：{', '.join(KNOWN_DATABASES)}。")

    year_from, year_to = search["year_from"], search["year_to"]
    if not isinstance(year_from, int) or not isinstance(year_to, int):
        raise ConfigError("search.year_from / year_to 必须是整数年份。")
    if year_from > year_to:
        raise ConfigError(f"search.year_from ({year_from}) > year_to ({year_to})。")

    excl = search.get("exclusion_terms") or {}
    if not isinstance(excl, dict):
        raise ConfigError("search.exclusion_terms 必须是映射。")
    bad_groups = [g for g in excl if g not in EXCLUSION_GROUPS]
    if bad_groups:
        raise ConfigError(
            f"search.exclusion_terms 含未知分组 {bad_groups}。"
            f"允许分组：{', '.join(EXCLUSION_GROUPS)}。")

    return {
        "databases": databases,
        "year_from": year_from,
        "year_to": year_to,
        "analytes": _validate_term_bank(search["analytes"], "analytes"),
        "matrices": _validate_term_bank(search["matrices"], "matrices"),
        "population_keywords": _as_str_list(search["population_keywords"],
                                            "population_keywords"),
        "exposure_terms": _as_str_list(search["exposure_terms"], "exposure_terms"),
        "exclusion_terms": {g: _as_str_list(v, f"exclusion_terms.{g}")
                            for g, v in excl.items()},
    }


# ===========================================================================
# 2. 查询构造器（每个数据库一个；返回 (查询字符串草稿, notes)）
# ===========================================================================

def _or_join(terms: list[str]) -> str:
    """通用 OR 块：多词短语加引号（WoS/Scopus/Embase 语法）。"""
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)


def _or_pubmed(terms: list[str]) -> str:
    """PubMed 自由词块：全部加 [tiab] 限定（多词短语加引号）。"""
    return " OR ".join(f'"{t}"[tiab]' for t in terms)


def _analyte_block(spec: dict, style: str) -> list[str]:
    """style: pubmed / thesaurus / plain —— 返回该区块的 OR 单元列表。"""
    units: list[str] = []
    if style == "pubmed" and spec.get("mesh"):
        units.append(" OR ".join(f'"{m}"[MeSH Terms]' for m in spec["mesh"]))
    if style == "thesaurus" and spec.get("mesh"):
        units.append(" OR ".join(f"'{m}'/exp" for m in spec["mesh"]))
    if spec.get("synonyms"):
        if style == "pubmed":
            units.append(_or_pubmed(spec["synonyms"]))
        else:
            units.append(_or_join(spec["synonyms"]))
    return units


def _block_and_join(blocks: list[str], joiner: str = " AND ") -> str:
    return joiner.join(f"({b})" for b in blocks if b)


def build_pubmed(spec: dict) -> tuple[str, list[str]]:
    notes = []
    analyte = _or_join([u for b in spec["analytes"]
                        for u in _analyte_block(b, "pubmed")])
    matrix = _or_join([u for b in spec["matrices"]
                       for u in _analyte_block(b, "pubmed")])
    exposure = _or_pubmed(spec["exposure_terms"])
    population = _or_pubmed(spec["population_keywords"])
    parts = [f"({analyte})", f"({matrix})", f"({exposure})", f"({population})"]
    excl = spec["exclusion_terms"]
    if excl:
        not_terms = _or_join([t for g in excl.values() for t in g])
        parts.append(f"NOT ({not_terms})")
        if "occupational" in excl:
            notes.append(OVERKILL_WARNINGS["occupational"])
    yf, yt = spec["year_from"], spec["year_to"]
    parts.append(f'("{yf}/01/01"[PDAT] : "{yt}/12/31"[PDAT])')
    return " AND ".join(parts), notes


def build_wos(spec: dict) -> tuple[str, list[str]]:
    notes = []
    all_terms = _or_join([t for b in spec["analytes"] + spec["matrices"]
                          for t in (b["synonyms"])])
    analyte = _or_join([t for b in spec["analytes"] for t in b["synonyms"]])
    matrix = _or_join([t for b in spec["matrices"] for t in b["synonyms"]])
    exposure = _or_join(spec["exposure_terms"])
    population = _or_join(spec["population_keywords"])
    core = (f'TS=({analyte} OR {exposure}) AND TS=({matrix}) '
            f'AND TS=({population})')
    _ = all_terms  # 保留变量说明：TS= 不区分主题字段，无 MeSH 概念
    excl = spec["exclusion_terms"]
    if excl:
        not_terms = _or_join([t for g in excl.values() for t in g])
        core += f' NOT TS=({not_terms})'
        if "occupational" in excl:
            notes.append(OVERKILL_WARNINGS["occupational"])
    core += f' AND PY=({spec["year_from"]}-{spec["year_to"]})'
    notes.append("WoS 无 MeSH；如需主题扩展，请在 TS= 中人工补充拼写变体与词根截断（如 arsenic*）。")
    return core, notes


def build_scopus(spec: dict) -> tuple[str, list[str]]:
    notes = []
    analyte = _or_join([t for b in spec["analytes"] for t in b["synonyms"]])
    matrix = _or_join([t for b in spec["matrices"] for t in b["synonyms"]])
    exposure = _or_join(spec["exposure_terms"])
    population = _or_join(spec["population_keywords"])
    core = (f'TITLE-ABS-KEY(({analyte} OR {exposure}) AND ({matrix}) '
            f'AND ({population}))')
    excl = spec["exclusion_terms"]
    if excl:
        not_terms = _or_join([t for g in excl.values() for t in g])
        core += f' AND NOT TITLE-ABS-KEY({not_terms})'
        if "occupational" in excl:
            notes.append(OVERKILL_WARNINGS["occupational"])
    core += (f' AND PUBYEAR > {spec["year_from"] - 1} '
             f'AND PUBYEAR < {spec["year_to"] + 1}')
    notes.append("Scopus 用 AND NOT；索引覆盖比 Embase 宽但无 Emtree，"
                 "可人工补关键词变体。")
    return core, notes


def build_embase(spec: dict) -> tuple[str, list[str]]:
    notes = []
    analyte_units = [u for b in spec["analytes"]
                     for u in _analyte_block(b, "thesaurus")]
    analyte_units += [_or_join(b["synonyms"]) for b in spec["analytes"]
                      if b["synonyms"]]
    matrix_units = [u for b in spec["matrices"]
                    for u in _analyte_block(b, "thesaurus")]
    matrix_units += [_or_join(b["synonyms"]) for b in spec["matrices"]
                     if b["synonyms"]]
    exposure = _or_join(spec["exposure_terms"])
    population = _or_join(spec["population_keywords"])
    core = (_block_and_join([" OR ".join(analyte_units), " OR ".join(matrix_units),
                             exposure, population]))
    excl = spec["exclusion_terms"]
    if excl:
        not_terms = _or_join([t for g in excl.values() for t in g])
        core += f' NOT ({not_terms})'
        if "occupational" in excl:
            notes.append(OVERKILL_WARNINGS["occupational"])
    core += f' AND [{spec["year_from"]}-{spec["year_to"]}]'
    notes.append("Embase 用 Emtree 叙词（'x'/exp）；上方 MeSH 词已按 Emtree 主词草拟，"
                 "执行前请在 Emtree 中人工核对。")
    return core, notes


def _zh_terms(spec: dict, key: str) -> list[str]:
    out: list[str] = []
    for b in spec[key]:
        out.extend(b["synonyms_zh"])
        out.append(b["name"] if _has_cjk(b["name"]) else "")
    return [t for t in out if t]


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def build_cnki(spec: dict) -> tuple[str, list[str]]:
    notes = []
    zh_analytes = _zh_terms(spec, "analytes")
    zh_context = (_zh_terms(spec, "matrices") + list(spec["exposure_terms"])
                  + list(spec["population_keywords"]))
    if not zh_analytes or not zh_context:
        placeholder = ("【待人工补充中文检索词：search.analytes/matrices 未提供 synonyms_zh，"
                       "本脚本不编造中文检索词】")
        return placeholder, notes
    if spec["exclusion_terms"]:
        zh_excl = [t for g in spec["exclusion_terms"].values() for t in g if _has_cjk(t)]
        not_part = f"-({'+'.join(zh_excl)})" if zh_excl else ""
    else:
        not_part = ""
    query = (f"SU=({'+'.join(zh_analytes)})*({'+'.join(zh_context)}){not_part}")
    notes.append("CNKI 专业检索语法：+ 为或、* 为与、- 为非；年份范围在检索界面设置，"
                 "并在检索记录中如实登记。")
    return query, notes


def build_wanfang(spec: dict) -> tuple[str, list[str]]:
    notes = []
    zh_analytes = _zh_terms(spec, "analytes")
    zh_context = (_zh_terms(spec, "matrices") + list(spec["exposure_terms"])
                  + list(spec["population_keywords"]))
    if not zh_analytes or not zh_context:
        placeholder = ("【待人工补充中文检索词：search.analytes/matrices 未提供 synonyms_zh，"
                       "本脚本不编造中文检索词】")
        return placeholder, notes
    query = (f"主题:({'+'.join(zh_analytes)}) AND 主题:({'+'.join(zh_context)})")
    if spec["exclusion_terms"]:
        zh_excl = [t for g in spec["exclusion_terms"].values() for t in g if _has_cjk(t)]
        if zh_excl:
            query += f" NOT 主题:({'+'.join(zh_excl)})"
    notes.append("万方专业检索以『主题:』字段检索；年份范围在检索界面设置，"
                 "并在检索记录中如实登记。")
    return query, notes


BUILDERS = {
    "PubMed": build_pubmed,
    "WebOfScience": build_wos,
    "Scopus": build_scopus,
    "Embase": build_embase,
    "CNKI": build_cnki,
    "Wanfang": build_wanfang,
}


# ===========================================================================
# 3. 草稿渲染
# ===========================================================================

def render_draft(spec: dict, cfg_summary: str) -> str:
    """渲染 search_strategy_draft.md（草稿水印 + 词表 + 各库检索式 + GATE-1 清单）。"""
    now = _dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    lines: list[str] = []
    lines.append("# 检索策略草稿（DRAFT — 未经人工确认，不得直接执行后留存为正式检索记录）")
    lines.append("")
    lines.append(f"- 生成时间：{now}")
    lines.append(f"- 项目配置：{cfg_summary}")
    lines.append(f"- 检索库：{', '.join(spec['databases'])}")
    lines.append(f"- 发表年窗口：{spec['year_from']}–{spec['year_to']}"
                 "（注意：这是**发表年**过滤；主表分析用**采样年**"
                 "（period_scheme），两者不可混用 —— 契约 §3.2）")
    lines.append("")
    lines.append("> 本文件是**草稿**：检索式的最终形态、实际执行、命中数记录由人工完成；"
                 "人工确认检索充分性后方可过 GATE-1。AI 不代填检索记录中的命中数。")
    lines.append("")

    lines.append("## 1. 检索词表（来自 project.yaml → search）")
    lines.append("")
    lines.append("### 暴露物")
    lines.append("")
    lines.append("| 名称 | 同义词 | MeSH/Emtree | 中文同义词 |")
    lines.append("|---|---|---|---|")
    for b in spec["analytes"]:
        lines.append(f"| {b['name']} | {', '.join(b['synonyms'])} | "
                     f"{', '.join(b['mesh']) or '—'} | "
                     f"{', '.join(b['synonyms_zh']) or '—'} |")
    lines.append("")
    lines.append("### 基质")
    lines.append("")
    lines.append("| 名称 | 同义词 | MeSH/Emtree | 中文同义词 |")
    lines.append("|---|---|---|---|")
    for b in spec["matrices"]:
        lines.append(f"| {b['name']} | {', '.join(b['synonyms'])} | "
                     f"{', '.join(b['mesh']) or '—'} | "
                     f"{', '.join(b['synonyms_zh']) or '—'} |")
    lines.append("")
    lines.append(f"### 暴露评估词：{'；'.join(spec['exposure_terms'])}")
    lines.append("")
    lines.append(f"### 人群限定词：{'；'.join(spec['population_keywords'])}")
    lines.append("")

    lines.append("### 排除词银行（可选区块；未配置的组不参与草稿）")
    lines.append("")
    if spec["exclusion_terms"]:
        for group in EXCLUSION_GROUPS:
            terms = spec["exclusion_terms"].get(group)
            if not terms:
                continue
            lines.append(f"- **{group}**：{'；'.join(terms)}")
            lines.append(f"  - {OVERKILL_WARNINGS[group]}")
    else:
        lines.append("未配置排除词（search.exclusion_terms 缺失或为空）——"
                     "是否使用排除词由人工在 GATE-1 决定。")
    lines.append("")

    lines.append("## 2. 各数据库检索式草稿")
    lines.append("")
    for db in spec["databases"]:
        query, notes = BUILDERS[db](spec)
        lines.append(f"### {db}")
        lines.append("")
        lines.append("```")
        for chunk in _wrap_query(query):
            lines.append(chunk)
        lines.append("```")
        if notes:
            lines.append("")
            for n in notes:
                lines.append(f"> {n}")
        lines.append("")

    lines.append("## 3. GATE-1 人工检查清单（执行检索时逐项完成）")
    lines.append("")
    lines.append("- [ ] 逐库**人工核改**草稿检索式（同义词增删、字段限定、语法试错）")
    lines.append("- [ ] 逐库执行检索，把**最终检索式原文 + 检索日期 + 命中数**"
                 "写入检索记录（`source_summary.csv` / `01-search/检索记录.csv`）——"
                 "AI 不代填")
    lines.append("- [ ] 逐库导出题录（格式建议见 references/database_syntax.md），"
                 "用 parse_ris / parse_enl / parse_bibtex 解析为统一 CSV")
    lines.append("- [ ] 用 merge_sources.py 合并各库题录并核对导出数 vs 命中数")
    lines.append("- [ ] 检查排除词是否过度杀伤（命中量级诊断："
                 "过少 → 放宽基质/人群词；异常巨大 → 补排除词）")
    lines.append("- [ ] 决定是否增补数据库（Scopus / Embase / 学位论文库）")
    lines.append("- [ ] 在检索记录中登记未预先注册的诚实声明（或 PROSPERO 编号）")
    lines.append("- [ ] 以上完成后，由**人**在 `_state/gates.json` 把 GATE-1 置为"
                 " `passed`（AI 无权开启闸门）")
    lines.append("")
    return "\n".join(lines)


def _wrap_query(query: str, width: int = 100) -> list[str]:
    """长查询按行折行（只对 ' AND '/' NOT ' 边界折，不破坏词项）。"""
    out: list[str] = []
    cur = ""
    for token in query.replace(" NOT ", "\nNOT ").replace(" AND ", "\nAND ").split("\n"):
        if not cur:
            cur = token
        elif len(cur) + len(token) + 5 <= width:
            joiner = " NOT " if token.startswith("NOT ") else " AND "
            cur += joiner + token
        else:
            out.append(cur)
            cur = token
    if cur:
        out.append(cur)
    return out


# ===========================================================================
# 4. self-test
# ===========================================================================

FIXTURE_YAML = """
project:
  name: "Demo internal exposure project"
  analyte: "As"
  country: "China"
  matrix_scope: ["Urine", "Blood"]
  config_version: "1.0.0"

period_scheme:
  boundaries:
    - {label: "1980-2000", start: 1980, end: 2000}
  freeze_date: "2026-07-19"

region_map:
  Southwest: ["云南省"]

unit_policy:
  Urine: {target: "ug/g Cr"}
  Blood: {target: "ug/L"}

standardization:
  median_to_gm_strategy: "as_gm"
  wan_variant: "minus"
  creatinine_path_mode: "CC_direct"

search:
  databases: ["PubMed", "WebOfScience", "Scopus", "Embase", "CNKI"]
  year_from: 1980
  year_to: 2024
  analytes:
    - name: "arsenic"
      mesh: ["Arsenic"]
      synonyms: ["arsenic", "inorganic arsenic"]
      synonyms_zh: ["砷", "总砷"]
  matrices:
    - name: "urine"
      mesh: ["Urine"]
      synonyms: ["urine", "urinary"]
      synonyms_zh: ["尿", "尿液"]
  exposure_terms: ["human biomonitoring", "internal exposure"]
  population_keywords: ["general population", "adults"]
  exclusion_terms:
    animal: ["mice", "rats"]
    occupational: ["workers", "occupational exposure"]
"""

FIXTURE_YAML_NO_ZH = FIXTURE_YAML.replace(
    '      synonyms_zh: ["砷", "总砷"]\n', "").replace(
    '      synonyms_zh: ["尿", "尿液"]\n', "")


def _write_fixture(tmp, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text.lstrip(), encoding="utf-8")
    return p


def run_self_test() -> int:
    print("=" * 70)
    print("generate_search_terms.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_search_"))

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    cfg = load_config(_write_fixture(tmp, "ok.yaml", FIXTURE_YAML),
                      require=["search"])
    spec = validate_search(cfg.raw["search"])

    print("\n[1] search 区块校验")
    check("5 个数据库全部可识别", spec["databases"] == KNOWN_DATABASES[:4] + ["CNKI"],
          str(spec["databases"]))
    check("analytes 词表含 synonyms 与 synonyms_zh",
          spec["analytes"][0]["synonyms"] == ["arsenic", "inorganic arsenic"]
          and spec["analytes"][0]["synonyms_zh"] == ["砷", "总砷"])
    check("exclusion_terms 分组合法",
          set(spec["exclusion_terms"]) == {"animal", "occupational"})

    print("\n[2] PubMed 构造")
    q_pm, n_pm = build_pubmed(spec)
    check("含 MeSH Terms", '"Arsenic"[MeSH Terms]' in q_pm)
    check("含 [tiab] 同义词", "arsenic" in q_pm and "[tiab]" in q_pm)
    check("含 NOT 排除块", "NOT (mice" in q_pm)
    check("含年份过滤（PDAT）", "1980/01/01" in q_pm and "2024/12/31" in q_pm)
    check("职业排除触发过度杀伤预警",
          any("过度杀伤" in n or "NOT occupational" in n or "连带排除" in n
              for n in n_pm), " | ".join(n_pm)[:60])

    print("\n[3] WoS / Scopus / Embase 构造")
    q_wos, n_wos = build_wos(spec)
    check("WoS 用 TS= 与 PY=", "TS=(" in q_wos and "PY=(1980-2024)" in q_wos)
    check("WoS 提示无 MeSH", any("MeSH" in n for n in n_wos))
    q_sc, n_sc = build_scopus(spec)
    check("Scopus 用 TITLE-ABS-KEY 与 PUBYEAR",
          "TITLE-ABS-KEY(" in q_sc and "PUBYEAR > 1979" in q_sc
          and "PUBYEAR < 2025" in q_sc)
    q_em, n_em = build_embase(spec)
    check("Embase 用 Emtree /exp 与年份区间",
          "'Arsenic'/exp" in q_em and "[1980-2024]" in q_em)

    print("\n[4] 中文库与 synonyms_zh")
    q_cn, n_cn = build_cnki(spec)
    check("CNKI 使用中文词（SU=）", "SU=(" in q_cn and "砷" in q_cn and "尿" in q_cn,
          q_cn[:40])
    q_cn2, n_cn2 = build_cnki(validate_search(
        load_config(_write_fixture(tmp, "nozh.yaml", FIXTURE_YAML_NO_ZH),
                    require=["search"]).raw["search"]))
    check("无 synonyms_zh → 占位符（不编造中文词）", "待人工补充中文检索词" in q_cn2)

    print("\n[5] 非法配置显式拒绝")
    bad_db = FIXTURE_YAML.replace('databases: ["PubMed", "WebOfScience", "Scopus", "Embase", "CNKI"]',
                                  'databases ["PubMed", "GoogleScholar"]')
    try:
        validate_search(load_config(_write_fixture(tmp, "baddb.yaml",
                                                   FIXTURE_YAML.replace(
                                                       'databases: ["PubMed", "WebOfScience", "Scopus", "Embase", "CNKI"]',
                                                       'databases: ["PubMed", "GoogleScholar"]')),
                                   require=["search"]).raw["search"])
        check("未知数据库 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("未知数据库 → ConfigError", True, str(e)[:36] + "…")
    try:
        bad_year = FIXTURE_YAML.replace("year_from: 1980", "year_from: 2030")
        validate_search(load_config(_write_fixture(tmp, "badyear.yaml", bad_year),
                                    require=["search"]).raw["search"])
        check("year_from > year_to → ConfigError", False, "未抛出！")
    except ConfigError:
        check("year_from > year_to → ConfigError", True)
    try:
        load_config(_write_fixture(tmp, "nosearch.yaml",
                                   FIXTURE_YAML.split("search:")[0]),
                    require=["search"])
        check("缺 search 区块 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("缺 search 区块 → ConfigError", True, str(e)[:36] + "…")
    try:
        bad_group = FIXTURE_YAML.replace("    animal: [\"mice\", \"rats\"]",
                                         "    politics: [\"election\"]")
        validate_search(load_config(_write_fixture(tmp, "badgrp.yaml", bad_group),
                                    require=["search"]).raw["search"])
        check("排除词未知分组 → ConfigError", False, "未抛出！")
    except ConfigError:
        check("排除词未知分组 → ConfigError", True)

    print("\n[6] 草稿渲染与确定性")
    md1 = render_draft(spec, cfg.summary())
    md2 = render_draft(spec, cfg.summary())
    body1 = "\n".join(ln for ln in md1.splitlines() if "生成时间" not in ln)
    body2 = "\n".join(ln for ln in md2.splitlines() if "生成时间" not in ln)
    check("同输入两次渲染一致（除时间戳）", body1 == body2)
    check("含 DRAFT 水印", "DRAFT" in md1)
    check("含职业排除过度杀伤预警", "连带排除" in md1)
    check("含 GATE-1 人工清单", "GATE-1 人工检查清单" in md1)
    check("含'AI 不代填命中数'声明", "AI 不代填" in md1)
    check("含采样年 vs 发表年提醒", "采样年" in md1)
    check("各数据库章节齐全",
          all(f"### {db}" in md1 for db in spec["databases"]))

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


# ===========================================================================
# CLI
# ===========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="generate_search_terms.py",
        description="从 project.yaml → search 生成各数据库检索式草稿（HBM-Meta-Agent / S1）")
    ap.add_argument("--config", type=Path, help="project.yaml 路径")
    ap.add_argument("--output", type=Path, help="草稿输出路径（search_strategy_draft.md）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.config:
        print("错误：缺少 --config（--self-test 可单独运行）。", file=sys.stderr)
        return 2
    if not args.output:
        print("错误：缺少 --output。", file=sys.stderr)
        return 2

    try:
        cfg = load_config(args.config, require=["search"])
        spec = validate_search(cfg.raw["search"])
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    md = render_draft(spec, cfg.summary())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import io
    io.open(args.output, "w", encoding="utf-8", newline="").write(md)
    print(f"检索式草稿已生成 → {args.output}")
    print("提醒：这是 DRAFT。请人工核改并逐库执行检索，把检索式原文/日期/命中数"
          "写入检索记录后再过 GATE-1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
