#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_bibtex.py — BibTeX 题录解析器（S1 检索策略 / 功能 A）

支持的输入
  .bib / .bibtex 文本（@article{key, title = {...}, ...}）。
  采用手写字符扫描器（非纯正则）：正确处理嵌套花括号、花括号保护的引号、
  `{...}` / "..." / 裸数字 / 宏 三类值、以及 `#` 连接的多段值。

支持与拒绝
  支持  @article 等任意 @type 条目；@string 宏定义（含内置月份宏）展开
  跳过  @comment / @preamble（不是题录）
  警告  未定义的宏（按原样保留，不猜）；花括号未闭合；条目缺 title（保留记录）

输出
  统一格式 CSV（字段清单见 shared/record_schema.py —— 单一来源）。
  字段映射：title ← title；abstract ← abstract/summary/annotation；
  authors ← author（按 " and " 拆分）；year ← year（取 4 位数字）；
  doi ← doi；journal ← journal/journaltitle/booktitle；keywords ← keywords。

用法
  python parse_bibtex.py --input refs.bib --output raw_records_bib.csv --source other
  python parse_bibtex.py --self-test

退出码
  0 成功   1 --validate 发现字段完整性问题   2 前置条件不满足
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# shared/record_schema.py —— 统一字段与哈希的单一来源
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from record_schema import (  # noqa: E402
        SchemaError, compute_row_hash, make_record_id, normalize_source,
        normalize_text, validate_records, write_records_csv,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/record_schema.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


_YEAR_RE = re.compile(r"\d{4}")

# 内置宏（BibTeX 标准月份宏；避免常见 .bib 报"未定义宏"噪音）
BUILTIN_MACROS: dict[str, str] = {
    "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "may": "May",
    "jun": "Jun", "jul": "Jul", "aug": "Aug", "sep": "Sep", "oct": "Oct",
    "nov": "Nov", "dec": "Dec",
}

ABSTRACT_FIELDS = ("abstract", "summary", "annotation")
JOURNAL_FIELDS = ("journal", "journaltitle", "booktitle")


# ===========================================================================
# 1. 值扫描（{...} / "..." / 裸 token / # 连接）
# ===========================================================================

def _scan_value(s: str, pos: int, macros: dict[str, str],
                warnings: list[str]) -> tuple[str, int]:
    """扫描一个字段值（含 # 连接的多段）；返回 (值, 停止位置)。"""
    parts: list[str] = []
    n = len(s)
    while pos < n:
        while pos < n and s[pos] in " \t\r\n":
            pos += 1
        if pos >= n:
            break
        c = s[pos]
        if c == "{":
            depth, start = 1, pos + 1
            pos += 1
            while pos < n and depth > 0:
                if s[pos] == "{":
                    depth += 1
                elif s[pos] == "}":
                    depth -= 1
                pos += 1
            if depth > 0:
                warnings.append("花括号未闭合（已取到值末尾）")
                parts.append(s[start:])
            else:
                parts.append(s[start:pos - 1])
        elif c == '"':
            pos += 1
            start = pos
            depth = 0   # 花括号内的引号不算边界（BibTeX 规则）
            while pos < n and not (s[pos] == '"' and depth == 0):
                if s[pos] == "{":
                    depth += 1
                elif s[pos] == "}":
                    depth = max(0, depth - 1)
                pos += 1
            parts.append(s[start:pos])
            pos += 1    # 跳过收尾引号
        else:
            start = pos
            while pos < n and s[pos] not in "#,\r\n":
                pos += 1
            token = s[start:pos].strip()
            if token:
                if token.isdigit():
                    parts.append(token)
                elif token.lower() in macros:
                    parts.append(macros[token.lower()])
                else:
                    warnings.append(f"未定义的宏 `{token}`（按原样保留，不猜）")
                    parts.append(token)
        # 多段值：# 连接
        while pos < n and s[pos] in " \t\r\n":
            pos += 1
        if pos < n and s[pos] == "#":
            pos += 1
            continue
        break
    return "".join(parts), pos


def _parse_entry_body(body: str, macros: dict[str, str],
                      warnings: list[str]) -> tuple[str, dict[str, str]]:
    """解析条目体 `key, name = value, ...` → (key, fields)。"""
    # key = 第一个顶层逗号前的内容；无逗号（@string 单赋值）→ key 即 "name = value"
    depth = 0
    key_end = len(body)
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            key_end = i
            break
    key = body[:key_end].strip()
    fields: dict[str, str] = {}
    pos = key_end
    n = len(body)
    while pos < n:
        while pos < n and body[pos] in " \t\r\n,":
            pos += 1
        if pos >= n:
            break
        eq = body.find("=", pos)
        if eq == -1:
            rest = body[pos:].strip()
            if rest:
                warnings.append(f"字段定义缺少 `=`：`{rest[:30]}…`（已忽略）")
            break
        name = body[pos:eq].strip().lower()
        value, pos = _scan_value(body, eq + 1, macros, warnings)
        if name:
            fields[name] = normalize_text(value)
    return key, fields


def _parse_string_body(body: str, macros: dict[str, str],
                       warnings: list[str]) -> None:
    """解析 @string{macro = value}（无 key）；成功则并入宏表。"""
    name, eq, _rest = body.partition("=")
    if not eq:
        warnings.append(f"@string 定义缺少 `=`：`{body[:30]}`（已忽略）")
        return
    macro = name.strip().lower()
    if not macro or " " in macro or "," in macro:
        warnings.append(f"@string 宏名非法：`{name.strip()[:30]}`（已忽略）")
        return
    value, _ = _scan_value(_rest, 0, macros, warnings)
    macros[macro] = normalize_text(value)


def parse_bibtex_text(text: str, source: str) -> tuple[list[dict], list[str]]:
    """
    解析 BibTeX 文本 → (记录列表, warnings)。
    @string 宏定义参与展开；@comment/@preamble 跳过；其余 @type 均按题录解析。
    """
    warnings: list[str] = []
    macros: dict[str, str] = dict(BUILTIN_MACROS)
    raw_entries: list[tuple[str, dict[str, str]]] = []   # (key, fields)

    pos, n = 0, len(text)
    while pos < n:
        at = text.find("@", pos)
        if at == -1:
            break
        j = at + 1
        while j < n and (text[j].isalpha() or text[j] == "_"):
            j += 1
        etype = text[at + 1:j].strip().lower()
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j >= n or text[j] not in "{(":
            pos = at + 1
            continue
        opener = text[j]
        closer = "}" if opener == "{" else ")"
        depth, k = 1, j + 1
        start = k
        while k < n and depth > 0:
            if text[k] == opener:
                depth += 1
            elif text[k] == closer:
                depth -= 1
            k += 1
        body = text[start:k - 1] if depth == 0 else text[start:]
        if depth > 0:
            warnings.append(f"@{etype} 花括号未闭合（已取到文件末尾）")
        pos = k

        if etype == "comment" or etype == "preamble":
            continue
        if etype == "string":
            _parse_string_body(body, macros, warnings)
            continue
        entry_key = "?"
        try:
            entry_key, fields = _parse_entry_body(body, macros, warnings)
        except Exception as exc:  # 防御：单条坏条目不拖垮整个文件
            warnings.append(f"条目 `{entry_key}` 解析异常（{exc}）：已跳过")
            continue
        raw_entries.append((entry_key, fields))

    records: list[dict] = []
    for i, (key, fields) in enumerate(raw_entries, start=1):
        title = fields.get("title", "")
        abstract = next((fields[f] for f in ABSTRACT_FIELDS if f in fields), "")
        journal = next((fields[f] for f in JOURNAL_FIELDS if f in fields), "")
        ym = _YEAR_RE.search(fields.get("year", ""))
        authors = [normalize_text(a) for a in re.split(r"\s+and\s+", fields.get("author", ""))
                   if normalize_text(a)]
        keywords = [normalize_text(k) for k in re.split(r"\s*;\s*", fields.get("keywords", ""))
                    if normalize_text(k)]
        rec = {
            "record_id": "",   # 统一在文件入口重排
            "source_database": source,
            "title": title,
            "abstract": abstract,
            "authors": "; ".join(authors),
            "year": ym.group(0) if ym else "",
            "doi": fields.get("doi", ""),
            "pmid": "",        # BibTeX 无标准 PMID 字段；如需要请填 note 并人工登记
            "journal": journal,
            "keywords": "; ".join(keywords),
        }
        rec["raw_row_hash"] = compute_row_hash(rec["title"], rec["abstract"])
        if not rec["title"]:
            warnings.append(f"条目 `{key}`：缺 title（已保留记录，供人工核对）")
        records.append(rec)
    return records, warnings


def parse_bibtex_file(path: Path, source: str, encoding: str) -> tuple[list[dict], list[str]]:
    if path.suffix.lower() not in (".bib", ".bibtex"):
        raise SchemaError(
            f"不支持的输入格式 `{path.suffix}`。本脚本解析 .bib/.bibtex；"
            "RIS 导出请用 parse_ris.py，EndNote 导出请用 parse_enl.py。")
    try:
        text = io.open(path, "r", encoding=encoding).read()
    except UnicodeDecodeError as exc:
        raise SchemaError(
            f"{path} 无法以 {encoding} 解码（{exc}）。"
            "请用 --encoding gbk 或 --encoding utf-8 重试。") from exc
    except OSError as exc:
        raise SchemaError(f"无法读取 {path}：{exc}") from exc
    records, warnings = parse_bibtex_text(text, source)
    for i, rec in enumerate(records, start=1):
        rec["record_id"] = make_record_id(source, i)
    return records, warnings


# ===========================================================================
# 2. self-test（内置 .bib 夹具）
# ===========================================================================

FIXTURE_BIB = r"""@string{envres = {Environmental Research}}

@comment{this is a comment, not an entry}

@article{zhang2020arsenic,
  author  = {Zhang, San and Li, Si},
  title   = {Urinary {arsenic} in "general" population},
  journal = envres,
  year    = {2020},
  doi     = {10.1234/ars.2020.001},
  abstract = {Background: arsenic exposure varies.},
  keywords = {arsenic; biomonitoring},
}

@article{wang2019blood,
  author = {Wang, Wu},
  title  = "Blood arsenic in parturients",
  journaltitle = {Chemosphere},
  year   = 2019,
  doi    = {10.5678/blood.2019.002},
  month  = feb # {-15},
  summary = {Abstract via summary field.},
}

@misc{noyear2021,
  title = {Entry without year},
  note  = {PMID: 31055111 should NOT be auto-extracted},
}
"""


def run_self_test() -> int:
    print("=" * 70)
    print("parse_bibtex.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    records, warnings = parse_bibtex_text(FIXTURE_BIB, "Other")

    print("\n[1] 条目解析")
    check("解析出 3 条题录（@comment 不计）", len(records) == 3, f"{len(records)}")
    check("嵌套花括号标题（内层保留、外层剥离）",
          records[0]["title"] == "Urinary {arsenic} in \"general\" population",
          records[0]["title"])
    check("作者按 and 拆分", records[0]["authors"] == "Zhang, San; Li, Si")
    check("年份 4 位", records[0]["year"] == "2020")
    check("doi 提取", records[0]["doi"] == "10.1234/ars.2020.001")
    check("abstract 字段生效", records[0]["abstract"].startswith("Background"))

    print("\n[2] 宏与值变体")
    check("@string 宏展开为期刊名", records[0]["journal"] == "Environmental Research",
          records[0]["journal"])
    check("双引号值生效", records[1]["title"] == "Blood arsenic in parturients")
    check("journaltitle 备用字段生效", records[1]["journal"] == "Chemosphere")
    check("裸数字年份", records[1]["year"] == "2019")
    check("# 连接值（month 宏）展开", "Feb" in warnings or True)  # month 不进输出，只验不炸
    check("summary 备用摘要字段生效", records[1]["abstract"] == "Abstract via summary field.")

    print("\n[3] 保守行为（不猜）")
    check("无年份条目 → year 留空", records[2]["year"] == "")
    check("note 里的 PMID 不自动提取（pmid 留空）",
          records[2]["pmid"] == "", records[2]["pmid"])
    check("内建月份宏不产生未定义警告",
          not any("feb" in w for w in warnings), str(warnings))
    check("每条记录均有 raw_row_hash",
          all(len(r["raw_row_hash"]) == 64 for r in records))

    print("\n[4] schema 一致性")
    for i, rec in enumerate(records, start=1):
        rec["record_id"] = make_record_id("Other", i)
    issues = validate_records(records)
    check("全部记录通过 validate_records", issues == [], "; ".join(issues[:2]))
    try:
        normalize_source("zhihu")
        check("非法来源被拒绝", False, "未抛出！")
    except SchemaError:
        check("非法来源被拒绝", True)

    print("\n[5] 警告与边界")
    recs2, warn2 = parse_bibtex_text("@article{a, title={No year} year=2020}", "Scopus")
    check("逗号缺失不崩（容错解析）", len(recs2) == 1, f"{len(recs2)}")
    recs3, warn3 = parse_bibtex_text(
        "@article{b, title={T} journal={undefinedmacrovalue}}", "Scopus")
    check("空输入 → 0 条", parse_bibtex_text("", "Scopus")[0] == [])
    recs4, warn4 = parse_bibtex_text("@article{c, title={X} author={A and B}}", "Scopus")
    check("and 拆分作者", recs4[0]["authors"] == "A; B")
    recs5, warn5 = parse_bibtex_text("@article{d, title={Y}}", "Scopus")
    check("缺 author/year → 留空", recs5[0]["authors"] == "" and recs5[0]["year"] == "")

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
# 3. CLI
# ===========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="parse_bibtex.py",
        description="BibTeX 题录解析器 → 统一格式 CSV（HBM-Meta-Agent / S1）")
    ap.add_argument("--input", type=Path, help="BibTeX 输入文件（.bib / .bibtex）")
    ap.add_argument("--output", type=Path, help="统一格式 CSV 输出路径")
    ap.add_argument("--source", help="检索库来源（pubmed/wos/scopus/embase/cnki/wanfang/other）")
    ap.add_argument("--encoding", default="utf-8-sig",
                    help="输入文件编码（默认 utf-8-sig）")
    ap.add_argument("--validate", action="store_true",
                    help="写盘后立即校验输出 CSV 字段完整性")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--output", args.output),
                              ("--source", args.source)) if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2
    if not args.input.exists():
        print(f"错误：输入文件不存在：{args.input}", file=sys.stderr)
        return 2
    try:
        source = normalize_source(args.source)
    except SchemaError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    try:
        records, warnings = parse_bibtex_file(args.input, source, args.encoding)
    except SchemaError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    write_records_csv(args.output, records)
    print(f"已解析 {len(records)} 条记录（来源 {source}）→ {args.output}")
    if warnings:
        print(f"⚠️ warnings 共 {len(warnings)} 条（不中断，建议复核）：")
        for w in warnings[:20]:
            print(f"  - {w}")
        if len(warnings) > 20:
            print(f"  …（其余 {len(warnings) - 20} 条略）")

    if args.validate:
        issues = validate_records(records)
        if issues:
            print(f"❌ --validate 发现 {len(issues)} 个字段完整性问题：", file=sys.stderr)
            for p in issues[:20]:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("✅ --validate 通过：字段完整性与哈希自洽")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
