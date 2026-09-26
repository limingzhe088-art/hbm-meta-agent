#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_ris.py — RIS 题录解析器（S1 检索策略 / 功能 A）

支持的导出
  PubMed / Web of Science / Scopus / Embase / CNKI / 万方 等数据库的 RIS 导出
  （`TY  - ` 起始、`ER  - ` 结束的标签行格式）。

输出
  统一格式 CSV（字段清单见 shared/record_schema.py —— 单一来源）：
  record_id, source_database, title, abstract, authors, year, doi, pmid,
  journal, keywords, raw_row_hash

字段映射
  title    ← TI/T1        abstract  ← AB/N2      authors   ← AU/A1（"; " 连接）
  year     ← PY/Y1（取前 4 位数字）
  doi      ← DO/DI        journal   ← JO/JF/JA/T2（按此优先级取第一个非空）
  keywords ← KW/ID/K1     pmid      ← AN/UT（仅当可从中提取出纯数字 PMID）
  非上述标签 → 记入 warnings，不中断（各库均有自有扩展标签）

标签约定
  多行值（换行的摘要/标题）按续行合并；多个 AU/KW 标签追加为列表。
  未出现 TY 就遇到 ER、或文件在 ER 前结束 → 记入 warnings，不中断。

用法
  python parse_ris.py --input pubmed_set.ris --output raw_records_pubmed.csv --source pubmed
  python parse_ris.py --input x.ris --output o.csv --source wos --encoding utf-8
  python parse_ris.py --self-test

退出码
  0 成功（warnings 不影响退出码，仅提示）
  1 --validate 校验发现字段完整性问题
  2 前置条件不满足（文件缺失 / 编码错误 / 来源非法 / 表头损坏）
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


# ===========================================================================
# 1. RIS 标签映射
# ===========================================================================

TITLE_TAGS = ("TI", "T1")
ABSTRACT_TAGS = ("AB", "N2")
AUTHOR_TAGS = ("AU", "A1")
YEAR_TAGS = ("PY", "Y1")
DOI_TAGS = ("DO", "DI")
JOURNAL_TAGS = ("JO", "JF", "JA", "T2")   # 优先级从高到低
KEYWORD_TAGS = ("KW", "ID", "K1")
PMID_TAGS = ("AN", "UT")

_LINE_RE = re.compile(r"^([A-Z0-9]{2})\s*-\s?(.*)$")
_YEAR_RE = re.compile(r"\d{4}")
_DIGITS_RE = re.compile(r"\d+")


def _extract_pmid(value: str) -> str:
    """从 AN/UT 值提取纯数字 PMID；提取不到返回空串（不猜）。"""
    v = normalize_text(value)
    if not v:
        return ""
    if v.isdigit():
        return v
    upper = v.upper()
    if "PMID" in upper or "MEDLINE" in upper:
        digits = _DIGITS_RE.findall(v)
        if digits:
            return digits[-1]
    return ""


def parse_ris_text(text: str, source: str) -> tuple[list[dict], list[str]]:
    """
    解析 RIS 文本 → (记录列表, warnings)。
    标签映射与续行规则见模块 docstring；warnings 只报告，不中断。
    """
    warnings: list[str] = []
    records: list[dict] = []
    current: dict | None = None
    last_tag: str | None = None
    seen_tags: set[str] = set()

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if m:
            tag, value = m.group(1), m.group(2).strip()
            if tag == "TY":
                if current is not None:
                    warnings.append(
                        f"第 {line_no} 行：上一条记录未以 ER 结束就出现新 TY（已自动截断）")
                current = {f: "" for f in
                           ("title", "abstract", "authors", "year", "doi",
                            "pmid", "journal", "keywords")}
                current["_title_tags"] = []
                current["_abstract_tags"] = []
                current["_authors"] = []
                current["_keywords"] = []
            elif tag == "ER":
                if current is None:
                    warnings.append(f"第 {line_no} 行：ER 出现在 TY 之前（已忽略）")
                else:
                    records.append(_finalize(current, source))
                    current = None
                last_tag = None
                continue
            elif current is None:
                warnings.append(f"第 {line_no} 行：标签 {tag} 出现在 TY 之前（已忽略）")
                last_tag = None
                continue
            else:
                if tag not in seen_tags:
                    seen_tags.add(tag)
                    if not any(tag in group for group in (
                            TITLE_TAGS, ABSTRACT_TAGS, AUTHOR_TAGS, YEAR_TAGS,
                            DOI_TAGS, JOURNAL_TAGS, KEYWORD_TAGS, PMID_TAGS, ("TY", "ER"))):
                        warnings.append(
                            f"第 {line_no} 行：非标准标签 {tag}（值已忽略；"
                            "如该标签承载题目/摘要等关键信息请检查导出设置）")

            if current is None:
                last_tag = None
                continue
            if tag in TITLE_TAGS:
                current["_title_tags"].append(value)
            elif tag in ABSTRACT_TAGS:
                current["_abstract_tags"].append(value)
            elif tag in AUTHOR_TAGS:
                if value:
                    current["_authors"].append(value)
            elif tag in KEYWORD_TAGS:
                if value:
                    current["_keywords"].append(value)
            elif tag in YEAR_TAGS and not current["year"]:
                ym = _YEAR_RE.search(value)
                if ym:
                    current["year"] = ym.group(0)
            elif tag in DOI_TAGS and not current["doi"]:
                current["doi"] = value
            elif tag in JOURNAL_TAGS and not current["journal"]:
                current["journal"] = value
            elif tag in PMID_TAGS and not current["pmid"]:
                current["pmid"] = _extract_pmid(value)
            last_tag = tag
        else:
            # 续行：并入上一个标签值（摘要换行是最常见场景）
            if current is not None and last_tag in ABSTRACT_TAGS and current["_abstract_tags"]:
                current["_abstract_tags"][-1] += " " + line.strip()
            elif current is not None and last_tag in TITLE_TAGS and current["_title_tags"]:
                current["_title_tags"][-1] += " " + line.strip()
            else:
                warnings.append(f"第 {line_no} 行：无法归属的续行（已忽略）")

    if current is not None:
        warnings.append("文件在 ER 之前结束：最后一条记录已按完整记录输出")
        records.append(_finalize(current, source))

    return records, warnings


def _finalize(current: dict, source: str) -> dict:
    title = normalize_text(" ".join(t for t in current["_title_tags"] if t))
    abstract = normalize_text(" ".join(t for t in current["_abstract_tags"] if t))
    rec = {
        "record_id": make_record_id(source, 0),   # 序号在 main 中统一重排
        "source_database": source,
        "title": title,
        "abstract": abstract,
        "authors": "; ".join(current["_authors"]),
        "year": current["year"],
        "doi": current["doi"],
        "pmid": current["pmid"],
        "journal": current["journal"],
        "keywords": "; ".join(current["_keywords"]),
    }
    rec["raw_row_hash"] = compute_row_hash(rec["title"], rec["abstract"])
    return rec


def parse_ris_file(path: Path, source: str, encoding: str) -> tuple[list[dict], list[str]]:
    try:
        text = io.open(path, "r", encoding=encoding).read()
    except UnicodeDecodeError as exc:
        raise SchemaError(
            f"{path} 无法以 {encoding} 解码（{exc}）。"
            "数据库导出常见 GBK/ANSI 编码，请用 --encoding gbk 重试。") from exc
    except OSError as exc:
        raise SchemaError(f"无法读取 {path}：{exc}") from exc
    records, warnings = parse_ris_text(text, source)
    # record_id 统一重排（解析过程中占位为 0）
    for i, rec in enumerate(records, start=1):
        rec["record_id"] = make_record_id(source, i)
    return records, warnings


# ===========================================================================
# 2. self-test（内置小型 RIS 夹具）
# ===========================================================================

FIXTURE_RIS = """TY  - JOUR
AU  - Zhang, San
AU  - Li, Si
TI  - Urinary arsenic in general population
   of southwest China
AB  - Background: arsenic exposure varies by region.
AB  - Methods: we measured urinary arsenic.
PY  - 2020/03/01
DO  - 10.1234/ars.2020.001
AN  - 32456789
JO  - Environmental Research
KW  - arsenic
KW  - biomonitoring
ER  -

TY  - JOUR
T1  - Blood arsenic in parturients
N2  - Abstract only, no AB tag used.
Y1  - 2019/07/12
DI  - 10.5678/blood.2019.002
UT  - MEDLINE:31055111
T2  - Chemosphere
ID  - pregnancy
ER  -

TY  - JOUR
TI  - Record without year or doi
PY  - not-a-year
ZZ  - custom vendor tag
ER  -
"""


def run_self_test() -> int:
    print("=" * 70)
    print("parse_ris.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    records, warnings = parse_ris_text(FIXTURE_RIS, "PubMed")
    for i, rec in enumerate(records, start=1):
        rec["record_id"] = make_record_id("PubMed", i)

    print("\n[1] 记录数与基本字段")
    check("解析出 3 条记录", len(records) == 3, f"{len(records)}")
    check("record_id 按 pubmed-00001 起编号",
          records[0]["record_id"] == "pubmed-00001", records[0]["record_id"])
    check("source_database 已写入", records[0]["source_database"] == "PubMed")
    check("标题正确", records[0]["title"] == "Urinary arsenic in general population of southwest China",
          records[0]["title"])
    check("多作者以 '; ' 连接", records[0]["authors"] == "Zhang, San; Li, Si",
          records[0]["authors"])
    check("年份取 4 位数字", records[0]["year"] == "2020", records[0]["year"])
    check("DOI 提取", records[0]["doi"] == "10.1234/ars.2020.001")
    check("期刊来自 JO", records[0]["journal"] == "Environmental Research")
    check("多关键词合并", records[0]["keywords"] == "arsenic; biomonitoring",
          records[0]["keywords"])

    print("\n[2] 标签变体与 PMID 提取")
    check("T1 备用标题标签生效", records[1]["title"] == "Blood arsenic in parturients",
          records[1]["title"])
    check("N2 备用摘要标签生效", records[1]["abstract"].startswith("Abstract only"),
          records[1]["abstract"][:30])
    check("Y1 中的年份被提取", records[1]["year"] == "2019", records[1]["year"])
    check("DI 备用 DOI 标签生效", records[1]["doi"] == "10.5678/blood.2019.002")
    check("UT 的 MEDLINE:NNN 提取 PMID", records[1]["pmid"] == "31055111",
          records[1]["pmid"])
    check("T2 作为期刊名生效", records[1]["journal"] == "Chemosphere")
    check("ID 标签并入关键词", records[1]["keywords"] == "pregnancy", records[1]["keywords"])

    print("\n[3] 脏数据容错")
    check("年份非法 → 留空（不猜）", records[2]["year"] == "", records[2]["year"])
    check("非标准标签 ZZ 进 warnings",
          any("ZZ" in w for w in warnings), str(warnings))
    check("warnings 不含致命错误信息", all("无法读取" not in w for w in warnings))
    check("每条记录均有 raw_row_hash",
          all(len(r["raw_row_hash"]) == 64 for r in records))

    print("\n[4] 行哈希与 schema 一致性")
    expect = compute_row_hash(records[0]["title"], records[0]["abstract"])
    check("raw_row_hash 与单一来源算法一致", records[0]["raw_row_hash"] == expect)
    issues = validate_records(records)
    check("全部记录通过 validate_records", issues == [], "; ".join(issues[:2]))

    print("\n[5] 来源校验")
    try:
        normalize_source("zhihu")
        check("非法来源被拒绝", False, "未抛出！")
    except SchemaError:
        check("非法来源被拒绝", True)
    check("wos 别名可用", normalize_source("wos") == "WebOfScience")

    print("\n[6] 边界：空输入 / 未闭合记录")
    recs2, warn2 = parse_ris_text("", "Scopus")
    check("空输入 → 0 条记录", recs2 == [])
    recs3, warn3 = parse_ris_text("TY  - JOUR\nTI  - Never closed\n", "Scopus")
    check("缺 ER → 仍输出 1 条记录并告警",
          len(recs3) == 1 and any("ER" in w for w in warn3),
          str(warn3))

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
        prog="parse_ris.py",
        description="RIS 题录解析器 → 统一格式 CSV（HBM-Meta-Agent / S1）")
    ap.add_argument("--input", type=Path, help="RIS 输入文件（.ris / .txt）")
    ap.add_argument("--output", type=Path, help="统一格式 CSV 输出路径")
    ap.add_argument("--source", help="检索库来源（pubmed/wos/scopus/embase/cnki/wanfang/other）")
    ap.add_argument("--encoding", default="utf-8-sig",
                    help="输入文件编码（默认 utf-8-sig；GBK 导出用 --encoding gbk）")
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
        records, warnings = parse_ris_file(args.input, source, args.encoding)
    except SchemaError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    write_records_csv(args.output, records)
    print(f"已解析 {len(records)} 条记录（来源 {source}）→ {args.output}")
    if warnings:
        print(f"⚠️ warnings 共 {len(warnings)} 条（不中断，建议复核导出设置）：")
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
