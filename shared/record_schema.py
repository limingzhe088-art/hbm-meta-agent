#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
record_schema.py — ★ S1 原始题录（raw record）统一字段与校验的**单一来源**（shared/）

动机
  S1 的四个解析器（parse_ris / parse_enl / parse_bibtex / merge_sources）与
  模板生成器（gen_templates.py）都要写"统一格式 CSV"。若字段清单、来源枚举、
  行哈希算法各自实现一份，就会重蹈 `inferred_flags` 曾在 5 处各写一份的覆辙
  （历史教训：漏同步 2 处，闭集校验形同虚设）。
  本模块把定义收敛为单一来源；各脚本从此导入，不再各自维护副本。

与主表契约的关系
  这里的 `source_database` 是**检索阶段的题录来源**（含 Embase），
  与 `data-contract.md` §3.1 主表列 `source_database` 的枚举（无 Embase）
  是两个层级：题录来源随检索库自由扩展；进入主表（S3 落库）时仍以契约枚举为准。

用法
  from record_schema import (
      RECORD_FIELDS, RAW_SOURCE_ENUM, normalize_source,
      make_record_id, compute_row_hash, validate_records,
  )

自检
  python record_schema.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


CONTRACT_VERSION = "1.0.0"

# ===========================================================================
# 1. 统一字段清单（单一来源；列顺序即输出 CSV 的列顺序）
# ===========================================================================

RECORD_FIELDS: list[str] = [
    "record_id",       # 解析器内分配：{来源缩写}-{序号:05d}，如 pubmed-00001
    "source_database",  # 检索库（RAW_SOURCE_ENUM；规范化写法）
    "title",           # 标题（原文，不翻译）
    "abstract",        # 摘要（原文；缺失留空，不插补）
    "authors",         # 作者列表，"; " 分隔
    "year",            # 发表年份 YYYY（此处是题录元数据；主表 `time` 才是采样年）
    "doi",             # DOI（无则留空）
    "pmid",            # PubMed Identifier（无则留空）
    "journal",         # 期刊名（原文）
    "keywords",        # 关键词，"; " 分隔
    "raw_row_hash",    # sha256(规范化 title + "\n" + 规范化 abstract)，去重键
]

# ===========================================================================
# 1b. 检索来源汇总与检索日志的列清单（单一来源；merge_sources 与模板生成器共用）
# ===========================================================================

# source_summary.csv 列：exported_count 由脚本实算；
# search_string/search_date/hit_count 只能来自人工检索日志（GATE-1）
SUMMARY_FIELDS: list[str] = [
    "source_database", "search_string", "search_date",
    "hit_count", "exported_count", "note",
]

# 人工维护的检索日志 CSV（--search-log）表头
SEARCH_LOG_FIELDS: list[str] = [
    "source_database", "search_string", "search_date", "hit_count",
]

# 检索式/命中数未回填时的标准提示语（AI 不代填）
NOTE_PENDING_FILL = "检索式/命中数待人工回填（GATE-1）"


# ===========================================================================
# 2. 来源枚举与别名（--source 参数的允许值；大小写不敏感）
# ===========================================================================

RAW_SOURCE_ENUM: list[str] = [
    "PubMed", "WebOfScience", "Scopus", "Embase", "CNKI", "Wanfang", "Other",
]

# record_id 前缀（与枚举一一对应，保证跨库合并后 id 不冲突）
SOURCE_PREFIX: dict[str, str] = {
    "PubMed": "pubmed",
    "WebOfScience": "wos",
    "Scopus": "scopus",
    "Embase": "embase",
    "CNKI": "cnki",
    "Wanfang": "wanfang",
    "Other": "other",
}

# --source 别名 → 规范名（键一律小写）
SOURCE_ALIASES: dict[str, str] = {
    "pubmed": "PubMed", "pm": "PubMed",
    "wos": "WebOfScience", "webofscience": "WebOfScience",
    "web of science": "WebOfScience",
    "scopus": "Scopus",
    "embase": "Embase",
    "cnki": "CNKI",
    "wanfang": "Wanfang",
    "other": "Other",
}


class SchemaError(Exception):
    """题录 schema 级硬错误。调用方应捕获并返回退出码 2。"""


def normalize_source(value: str) -> str:
    """--source 别名 → 规范来源名；未知名 → 硬错误（不做兜底）。"""
    key = str(value).strip().lower()
    if key in SOURCE_ALIASES:
        return SOURCE_ALIASES[key]
    raise SchemaError(
        f"未知来源 `{value}`。允许取值（大小写不敏感）："
        f"{', '.join(RAW_SOURCE_ENUM)}。")


# ===========================================================================
# 3. 记录构造与行哈希
# ===========================================================================

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """去首尾空白 + 折叠所有连续空白为单个空格。"""
    return _WS_RE.sub(" ", str(text or "")).strip()


def compute_row_hash(title: str, abstract: str) -> str:
    """
    raw_row_hash 的**唯一定义处**：
      sha256( normalize(title).casefold() + "\\n" + normalize(abstract).casefold() ) 的十六进制。
    规范化（空白折叠 + 小写）使跨库导出的同一篇文献哈希一致；abstract 缺失视为空串。
    """
    normalized = normalize_text(title).casefold() + "\n" + normalize_text(abstract).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_record_id(source: str, seq: int) -> str:
    """`{来源缩写}-{序号:05d}`；来源必须已是规范名。"""
    if source not in SOURCE_PREFIX:
        raise SchemaError(f"来源 `{source}` 不在枚举内，无法分配 record_id。")
    return f"{SOURCE_PREFIX[source]}-{seq:05d}"


def empty_record(source: str) -> dict:
    """按统一字段构造空记录（值为空串，不填 NA/0 —— 契约 §2 空值规则）。"""
    canonical = normalize_source(source) if source not in RAW_SOURCE_ENUM else source
    return {f: "" for f in RECORD_FIELDS} | {"source_database": canonical}


# ===========================================================================
# 4. 输出校验（--validate 模式的实现；解析器共用）
# ===========================================================================

_YEAR_RE = re.compile(r"^\d{4}$")


def validate_header(header: list[str]) -> list[str]:
    """表头必须与 RECORD_FIELDS 完全一致（含顺序）；不做静默重排。"""
    if header != RECORD_FIELDS:
        missing = [f for f in RECORD_FIELDS if f not in header]
        extra = [f for f in header if f not in RECORD_FIELDS]
        problems = []
        if missing:
            problems.append(f"缺字段 {missing}")
        if extra:
            problems.append(f"多出字段 {extra}")
        if not problems:
            problems.append(f"字段顺序不一致：实际 {header}")
        return ["表头与 RECORD_FIELDS 不一致：" + "；".join(problems)]
    return []


def validate_records(rows: list[dict]) -> list[str]:
    """逐行校验字段完整性。返回问题清单（空 = 通过）。"""
    issues: list[str] = []
    seen_ids: set[str] = set()
    # 注意：跨库同文（同标题同哈希、不同 record_id）是**疑似重复**，
    # 交 S2 去重人工裁决，此处不判错 —— 校验只管"格式与自洽"，不管"内容重复"。
    for i, row in enumerate(rows, start=2):  # 第 1 行是表头
        where = f"第 {i} 行"
        rid = normalize_text(row.get("record_id", ""))
        if not rid:
            issues.append(f"{where}：record_id 为空")
        elif rid in seen_ids:
            issues.append(f"{where}：record_id 重复（{rid}）")
        seen_ids.add(rid)

        if not normalize_text(row.get("title", "")):
            issues.append(f"{where}：title 为空（无法进入筛选）")

        year = normalize_text(row.get("year", ""))
        if year and not _YEAR_RE.match(year):
            issues.append(f"{where}：year=`{year}` 非 YYYY 格式")

        src = normalize_text(row.get("source_database", ""))
        if src and src not in RAW_SOURCE_ENUM:
            issues.append(f"{where}：source_database=`{src}` 不在枚举内")

        expected = compute_row_hash(row.get("title", ""), row.get("abstract", ""))
        got = normalize_text(row.get("raw_row_hash", ""))
        if not got:
            issues.append(f"{where}：raw_row_hash 为空")
        elif got != expected:
            issues.append(f"{where}：raw_row_hash 与 title+abstract 重算不一致")
    return issues


def read_records_csv(path: Path, encoding: str = "utf-8-sig") -> list[dict]:
    """读统一格式 CSV → 记录字典列表；表头不符即硬错误。"""
    try:
        with io.open(path, "r", encoding=encoding, newline="") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            problems = validate_header(header)
            if problems:
                raise SchemaError(f"{path}：{problems[0]}")
            return [dict(r) for r in reader]
    except UnicodeDecodeError as exc:
        raise SchemaError(
            f"{path} 无法以 {encoding} 解码（{exc}）。"
            "数据库导出常见 GBK/ANSI 编码，请用 --encoding gbk 重试。") from exc
    except OSError as exc:
        raise SchemaError(f"无法读取 {path}：{exc}") from exc


def write_records_csv(path: Path, rows: list[dict]) -> None:
    """写统一格式 CSV（表头由 RECORD_FIELDS 单一来源给出）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(RECORD_FIELDS)
        for row in rows:
            writer.writerow([row.get(f, "") for f in RECORD_FIELDS])


# ===========================================================================
# 5. self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("record_schema.py --self-test（题录统一字段单一来源）")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    print("\n[1] 字段清单")
    check(f"RECORD_FIELDS 共 {len(RECORD_FIELDS)} 项", len(RECORD_FIELDS) == 11,
          str(RECORD_FIELDS))
    check("字段名唯一", len(RECORD_FIELDS) == len(set(RECORD_FIELDS)))
    check("raw_row_hash 在清单内（去重键必须落盘）", "raw_row_hash" in RECORD_FIELDS)
    check("record_id 在清单内", "record_id" in RECORD_FIELDS)
    check("source_database 在清单内", "source_database" in RECORD_FIELDS)

    print("\n[2] 来源枚举与别名")
    check("枚举 7 项", len(RAW_SOURCE_ENUM) == 7, str(RAW_SOURCE_ENUM))
    check("每个枚举值都有 record_id 前缀",
          set(RAW_SOURCE_ENUM) == set(SOURCE_PREFIX))
    check("pubmed 别名 → PubMed", normalize_source("pubmed") == "PubMed")
    check("wos 别名 → WebOfScience", normalize_source("WoS") == "WebOfScience")
    check("大小写不敏感", normalize_source("  SCOPUS ") == "Scopus")
    try:
        normalize_source("cnki.net")
        check("未知来源 → SchemaError", False, "未抛出！")
    except SchemaError as e:
        check("未知来源 → SchemaError", True, str(e)[:40] + "…")
    check("错误信息列出允许值", "Embase" in str(_err(normalize_source, "nope")))

    print("\n[3] 文本规范化")
    check("折叠连续空白", normalize_text("  a \t b\n\n c ") == "a b c")
    check("None → 空串", normalize_text(None) == "")

    print("\n[4] 行哈希（唯一定义处）")
    h1 = compute_row_hash(" Urinary  Arsenic ", "  Exposure   study ")
    h2 = compute_row_hash("urinary arsenic", "exposure study")
    check("空白与小写差异不改变哈希", h1 == h2)
    check("哈希为 64 位十六进制", re.fullmatch(r"[0-9a-f]{64}", h1) is not None)
    h3 = compute_row_hash("Urinary Arsenic", "")
    check("abstract 缺失 → 哈希仍可计算（与空串一致）",
          h3 == compute_row_hash("Urinary Arsenic", ""))
    check("不同标题 → 不同哈希", h1 != compute_row_hash("Blood Arsenic", "x"))

    print("\n[5] record_id 分配")
    check("PubMed 首条", make_record_id("PubMed", 1) == "pubmed-00001")
    check("序号补零到 5 位", make_record_id("Wanfang", 42) == "wanfang-00042")
    check("跨库前缀不同（合并后 id 不冲突）",
          make_record_id("PubMed", 7) != make_record_id("CNKI", 7))
    try:
        make_record_id("Zhihu", 1)
        check("非法来源 → SchemaError", False, "未抛出！")
    except SchemaError:
        check("非法来源 → SchemaError", True)

    print("\n[6] 表头校验")
    check("与 RECORD_FIELDS 一致 → 无问题", validate_header(list(RECORD_FIELDS)) == [])
    bad = RECORD_FIELDS[:-1]
    check("缺字段被报出", validate_header(bad) and "raw_row_hash" in validate_header(bad)[0],
          validate_header(bad)[:1])
    swapped = RECORD_FIELDS[:]
    swapped[0], swapped[1] = swapped[1], swapped[0]
    check("顺序错误被报出", "顺序" in validate_header(swapped)[0],
          validate_header(swapped)[0][:50])

    print("\n[7] 记录校验（validate_records）")
    ok_row = {f: "" for f in RECORD_FIELDS}
    ok_row.update({
        "record_id": "pubmed-00001", "source_database": "PubMed",
        "title": "Urinary arsenic in China", "abstract": "A study.",
        "year": "2020", "raw_row_hash": compute_row_hash("Urinary arsenic in China", "A study."),
    })
    check("合法记录 → 无问题", validate_records([ok_row]) == [])
    no_title = dict(ok_row, title="")
    check("title 为空被报出", any("title 为空" in p for p in validate_records([no_title])))
    dup = dict(ok_row)
    check("record_id 重复被报出",
          len([p for p in validate_records([ok_row, dup]) if "重复" in p]) == 1)
    bad_year = dict(ok_row, year="2020-05")
    check("year 非 YYYY 被报出", any("YYYY" in p for p in validate_records([bad_year])))
    bad_src = dict(ok_row, source_database="Zhihu")
    check("来源越界被报出", any("枚举" in p for p in validate_records([bad_src])))
    bad_hash = dict(ok_row, raw_row_hash="deadbeef")
    check("哈希不一致被报出",
          any("重算不一致" in p for p in validate_records([bad_hash])))
    no_hash = dict(ok_row, raw_row_hash="")
    check("哈希为空被报出", any("raw_row_hash 为空" in p for p in validate_records([no_hash])))
    check("跨库同文（同哈希不同 id）不判错 —— 疑似重复交 S2 裁决",
          validate_records([ok_row, dict(ok_row, record_id="wos-00001",
                                        source_database="WebOfScience")]) == [])

    print("\n[8] 空记录构造")
    rec = empty_record("Scopus")
    check("11 个字段全在",
          set(rec) == set(RECORD_FIELDS), str(sorted(set(rec) ^ set(RECORD_FIELDS))))
    check("除 source_database 外全为空串",
          all(v == "" for k, v in rec.items() if k != "source_database"))
    check("来源已写入", rec["source_database"] == "Scopus")
    try:
        empty_record("Zhihu")
        check("非法来源 → SchemaError", False, "未抛出！")
    except SchemaError:
        check("非法来源 → SchemaError", True)

    print("\n[9] CSV 读写往返")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_rec_"))
    p = tmp / "roundtrip.csv"
    rows = [ok_row, dict(ok_row, record_id="wos-00001", source_database="WebOfScience",
                         title="Second title")]
    rows[1]["raw_row_hash"] = compute_row_hash("Second title", "")
    write_records_csv(p, rows)
    back = read_records_csv(p)
    check("往返后行数一致", len(back) == 2, f"{len(back)}")
    check("往返后字段一致", back[0] == ok_row)
    check("往返后哈希可复算",
          back[1]["raw_row_hash"] == compute_row_hash("Second title", ""))
    bad_p = tmp / "bad.csv"
    bad_p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    try:
        read_records_csv(bad_p)
        check("表头不符的 CSV → SchemaError", False, "未抛出！")
    except SchemaError as e:
        check("表头不符的 CSV → SchemaError", True, str(e)[:40] + "…")

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def _err(fn, *args) -> str:
    """返回 fn(*args) 抛出的 SchemaError 文本；未抛出则返回空串。"""
    try:
        fn(*args)
        return ""
    except SchemaError as exc:
        return str(exc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="record_schema.py",
        description="S1 原始题录统一字段的单一来源（HBM-Meta-Agent / shared）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fields", action="store_true", help="打印统一字段清单")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if args.fields:
        for f in RECORD_FIELDS:
            print(f)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
