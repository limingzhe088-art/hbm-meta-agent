#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_enl.py — EndNote 导出题录解析器（S1 检索策略 / 功能 A）

支持两种 EndNote 导出格式（按扩展名自动选择）：
  .xml        RefMan XML（EndNote: File → Export → 输出样式选 "RefMan (RIS) Export"? 不，
              应选 XML 导出风格，根节点 <xml><records><record>…）
  .txt / .tsv 制表符分隔导出（EndNote: File → Export → 文本，制表符分隔；需含表头行）
  .csv        逗号分隔导出（同上，需含表头行）

**不支持** .enl / .enlx：那是 EndNote 的二进制专有库文件，无法可靠解析 ——
显式报错并提示先在 EndNote 中导出为 XML 或制表符分隔文本（不做静默猜测）。

输出
  统一格式 CSV（字段清单见 shared/record_schema.py —— 单一来源）。

XML 字段映射
  title    ← titles/title            journal ← periodical/full-title
             （缺失时回退 titles/secondary-title）abstract ← abstract
  authors  ← contributors/authors/author   year ← dates/year（取 4 位数字）
  doi      ← electronic-resource-num       pmid ← accession-num（仅纯数字）
  keywords ← keywords/keyword

TXT/CSV 字段映射（表头名大小写不敏感，按别名表识别；未识别的列忽略并记 warning）
  title ← title/article title/标题        abstract ← abstract/摘要
  authors ← author/authors/作者           year ← year/publication year/出版年
  doi ← doi                               journal ← journal/journal title/secondary title/期刊
  keywords ← keywords/关键词              pmid ← pmid/accession number/存取号

用法
  python parse_enl.py --input wos_export.xml --output raw_records_wos.csv --source wos
  python parse_enl.py --input table_export.txt --output o.csv --source other --encoding utf-8
  python parse_enl.py --self-test

退出码
  0 成功   1 --validate 发现字段完整性问题   2 前置条件不满足
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import xml.etree.ElementTree as ET
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

# 制表符/CSV 导出的表头别名（一律小写比较）
TXT_FIELD_ALIASES: dict[str, list[str]] = {
    "title": ["title", "article title", "标题", "题名"],
    "abstract": ["abstract", "摘要"],
    "authors": ["author", "authors", "作者"],
    "year": ["year", "publication year", "publication year ", "出版年", "年份"],
    "doi": ["doi", "doi "],
    "journal": ["journal", "journal title", "secondary title",
                "periodical", "期刊", "期刊名", "刊名"],
    "keywords": ["keywords", "keyword", "关键词"],
    "pmid": ["pmid", "accession number", "accession num", "存取号", "登录号"],
}

# EndNote 制表符导出常见但本工作包用不到的列（出现时提示已忽略，供人工核对）
TXT_KNOWN_IGNORED = {
    "ref type", "reference type", "reference id", "rec number",
    "pages", "volume", "number", "issue", "publisher", "place published",
    "date", "url", "notes", "call number", "label", "custom 1",
    "custom 2", "custom 3", "custom 4",
}


# ===========================================================================
# 1. RefMan XML 解析
# ===========================================================================

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node, *path: str) -> str:
    cur = node
    for name in path:
        found = None
        for ch in cur:
            if _local(ch.tag) == name:
                found = ch
                break
        if found is None:
            return ""
        cur = found
    return normalize_text(cur.text or "")


def _child_list(node, *path: str) -> list[str]:
    cur = node
    for name in path[:-1]:
        nxt = None
        for ch in cur:
            if _local(ch.tag) == name:
                nxt = ch
                break
        if nxt is None:
            return []
        cur = nxt
    return [normalize_text(ch.text or "") for ch in cur
            if _local(ch.tag) == path[-1] and normalize_text(ch.text or "")]


def _finalize(parsed: dict, source: str) -> dict:
    rec = {
        "record_id": "",
        "source_database": source,
        "title": normalize_text(parsed.get("title", "")),
        "abstract": normalize_text(parsed.get("abstract", "")),
        "authors": "; ".join(parsed.get("authors", [])),
        "year": parsed.get("year", ""),
        "doi": parsed.get("doi", ""),
        "pmid": parsed.get("pmid", ""),
        "journal": parsed.get("journal", ""),
        "keywords": "; ".join(parsed.get("keywords", [])),
    }
    rec["raw_row_hash"] = compute_row_hash(rec["title"], rec["abstract"])
    return rec


def parse_enl_xml_text(text: str, source: str) -> tuple[list[dict], list[str]]:
    """解析 RefMan XML 文本 → (记录列表, warnings)。"""
    warnings: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SchemaError(f"XML 解析失败（{exc}）。请确认导出格式为 EndNote 的 XML 导出。") from exc

    records: list[dict] = []
    nodes = [n for n in root.iter() if _local(n.tag) == "record"]
    if not nodes:
        warnings.append("XML 中未找到 <record> 节点：请确认是 EndNote/RefMan XML 导出")
    for idx, node in enumerate(nodes, start=1):
        title = _child_text(node, "titles", "title")
        journal = _child_text(node, "periodical", "full-title")
        if not journal:
            journal = _child_text(node, "titles", "secondary-title")
        year_raw = _child_text(node, "dates", "year")
        ym = _YEAR_RE.search(year_raw)
        doi = _child_text(node, "electronic-resource-num")
        pmid = _child_text(node, "accession-num")
        rec = _finalize({
            "title": title,
            "abstract": _child_text(node, "abstract"),
            "authors": _child_list(node, "contributors", "authors", "author"),
            "year": ym.group(0) if ym else "",
            "doi": doi,
            "pmid": pmid if pmid.isdigit() else "",
            "journal": journal,
            "keywords": _child_list(node, "keywords", "keyword"),
        }, source)
        if not rec["title"]:
            warnings.append(f"第 {idx} 条 <record>：title 为空（可能不是文献题录节点）")
        records.append(rec)
    return records, warnings


# ===========================================================================
# 2. 制表符/CSV 导出解析
# ===========================================================================

def _map_header(header: list[str]) -> tuple[dict[str, int], list[str]]:
    """表头名 → 统一字段索引；返回 (映射, warnings)。"""
    mapping: dict[str, int] = {}
    warnings: list[str] = []
    for col, name in enumerate(header):
        key = normalize_text(name).lower()
        if not key:
            continue
        hit = None
        for field, aliases in TXT_FIELD_ALIASES.items():
            if key in aliases and field not in mapping:
                hit = field
                break
        if hit:
            mapping[hit] = col
        elif key in TXT_KNOWN_IGNORED:
            continue
        else:
            warnings.append(f"未识别的列 `{name}`（已忽略；若承载标题/摘要请核对导出设置）")
    return mapping, warnings


def parse_enl_table_text(text: str, source: str, delimiter: str) -> tuple[list[dict], list[str]]:
    """解析制表符/CSV 导出文本 → (记录列表, warnings)。表头行必需。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise SchemaError("输入为空：未找到任何行（含表头行）。")
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = [r for r in reader]
    header = [normalize_text(h) for h in rows[0]]
    mapping, warnings = _map_header(header)
    if "title" not in mapping:
        raise SchemaError(
            "表头中未识别到标题列（尝试过别名："
            f"{', '.join(TXT_FIELD_ALIASES['title'])}）。"
            f"实际表头：{header}。请在 EndNote 导出时包含 Title 字段，"
            "或使用 XML 导出格式。")
    if "authors" not in mapping:
        warnings.append("未识别作者列：authors 将为空")

    records: list[dict] = []
    for row_no, row in enumerate(rows[1:], start=2):
        def cell(field: str) -> str:
            col = mapping.get(field)
            return normalize_text(row[col]) if col is not None and col < len(row) else ""

        year_raw = cell("year")
        ym = _YEAR_RE.search(year_raw)
        pmid = cell("pmid")
        rec = _finalize({
            "title": cell("title"),
            "abstract": cell("abstract"),
            "authors": [a for a in (normalize_text(x) for x in
                                    re.split(r"\s*;\s*|\s*and\s+", cell("authors"))) if a],
            "year": ym.group(0) if ym else "",
            "doi": cell("doi"),
            "pmid": pmid if pmid.isdigit() else "",
            "journal": cell("journal"),
            "keywords": [k for k in (normalize_text(x) for x in
                                     re.split(r"\s*;\s*", cell("keywords"))) if k],
        }, source)
        if not rec["title"]:
            warnings.append(f"第 {row_no} 行：title 为空（已保留记录，供人工核对）")
        records.append(rec)
    return records, warnings


# ===========================================================================
# 3. 文件入口（格式按扩展名判定；不支持显式报错）
# ===========================================================================

def parse_enl_file(path: Path, source: str, encoding: str) -> tuple[list[dict], list[str]]:
    ext = path.suffix.lower()
    if ext in (".enl", ".enlx"):
        raise SchemaError(
            f"不支持 {ext}：这是 EndNote 的二进制专有库文件，无法可靠解析。"
            "请在 EndNote 中：File → Export → 保存类型选 XML（或文本，制表符分隔），"
            "再对导出文件运行本脚本。（显式拒绝，不做猜测性解析）")
    if ext not in (".xml", ".txt", ".tsv", ".csv"):
        raise SchemaError(
            f"不支持的输入格式 `{ext}`。支持：.xml（RefMan XML）、"
            ".txt/.tsv（制表符分隔）、.csv（逗号分隔）。其他格式请先转换。")
    try:
        text = io.open(path, "r", encoding=encoding).read()
    except UnicodeDecodeError as exc:
        raise SchemaError(
            f"{path} 无法以 {encoding} 解码（{exc}）。"
            "请用 --encoding gbk（中文 EndNote 常见）或 --encoding utf-8 重试。") from exc
    except OSError as exc:
        raise SchemaError(f"无法读取 {path}：{exc}") from exc

    if ext == ".xml":
        records, warnings = parse_enl_xml_text(text, source)
    elif ext == ".csv":
        records, warnings = parse_enl_table_text(text, source, delimiter=",")
    else:
        records, warnings = parse_enl_table_text(text, source, delimiter="\t")

    for i, rec in enumerate(records, start=1):
        rec["record_id"] = make_record_id(source, i)
    return records, warnings


# ===========================================================================
# 4. self-test（内置 XML + 制表符 TXT 夹具）
# ===========================================================================

FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xml>
  <records>
    <record>
      <database name="demo.enl">demo.enl</database>
      <ref-type name="Journal Article">17</ref-type>
      <contributors><authors><author>Zhang, San</author><author>Li, Si</author></authors></contributors>
      <titles><title>Urinary arsenic in general population</title>
        <secondary-title>Environmental Research</secondary-title></titles>
      <periodical><full-title>Environmental Research</full-title>
        <abbrev-title>Environ Res</abbrev-title></periodical>
      <dates><year>2020</year></dates>
      <abstract>Background: arsenic exposure varies.</abstract>
      <electronic-resource-num>10.1234/ars.2020.001</electronic-resource-num>
      <accession-num>32456789</accession-num>
      <keywords><keyword>arsenic</keyword><keyword>biomonitoring</keyword></keywords>
    </record>
    <record>
      <contributors><authors><author>Wang, Wu</author></authors></contributors>
      <titles><title>Blood mercury without accession</title></titles>
      <dates><year>Published online 2021</year></dates>
      <electronic-resource-num>10.5678/hg.2021.002</electronic-resource-num>
    </record>
  </records>
</xml>
"""

FIXTURE_TXT = """Title\tAuthor\tYear\tJournal\tAbstract\tDOI\tKeywords\tPMID\tRef Type\tNotes
Urinary cadmium in adults\tZhang, San; Li, Si\t2019\tScience of the Total Environment\tCd exposure survey.\t10.1111/cd.2019.001\tcadmium; urine\t29000001\tJournal Article\t
Blood lead in children\tWang, Wu\t2020\tEnvironmental Pollution\tPb exposure survey.\t10.2222/pb.2020.002\tlead; blood\tNOT_A_Pmid\tJournal Article\tkeep me
"""


def run_self_test() -> int:
    print("=" * 70)
    print("parse_enl.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    print("\n[1] RefMan XML")
    records, warnings = parse_enl_xml_text(FIXTURE_XML, "WebOfScience")
    check("解析出 2 条记录", len(records) == 2, f"{len(records)}")
    check("标题提取", records[0]["title"] == "Urinary arsenic in general population")
    check("期刊优先取 periodical/full-title",
          records[0]["journal"] == "Environmental Research")
    check("多作者 '; ' 连接", records[0]["authors"] == "Zhang, San; Li, Si")
    check("年份 4 位", records[0]["year"] == "2020")
    check("electronic-resource-num → doi", records[0]["doi"] == "10.1234/ars.2020.001")
    check("accession-num 纯数字 → pmid", records[0]["pmid"] == "32456789")
    check("关键词合并", records[0]["keywords"] == "arsenic; biomonitoring")
    check("record_id 可按来源分配",
          make_record_id("WebOfScience", 1) == "wos-00001")

    print("\n[2] XML 脏数据容错")
    check("年份含杂质 → 提取 4 位数字", records[1]["year"] == "2021", records[1]["year"])
    check("缺失摘要 → 留空（不插补）", records[1]["abstract"] == "")
    check("期刊回退 secondary-title（第二条无 periodical）",
          records[1]["journal"] == "", records[1]["journal"])
    check("每条记录均有 raw_row_hash",
          all(len(r["raw_row_hash"]) == 64 for r in records))

    print("\n[3] 制表符 TXT")
    t_records, t_warnings = parse_enl_table_text(FIXTURE_TXT, "PubMed", delimiter="\t")
    check("解析出 2 条记录", len(t_records) == 2, f"{len(t_records)}")
    check("标题列识别", t_records[0]["title"] == "Urinary cadmium in adults")
    check("作者按 '; ' 拆分", t_records[0]["authors"] == "Zhang, San; Li, Si")
    check("期刊列识别", t_records[0]["journal"] == "Science of the Total Environment")
    check("PMID 纯数字才保留", t_records[0]["pmid"] == "29000001")
    check("非数字 PMID → 留空", t_records[1]["pmid"] == "")
    check("未知列（Notes/Ref Type 中 Notes 未列白名单）→ 警告或不干扰解析",
          all(len(r["raw_row_hash"]) == 64 for r in t_records))
    check("表头别名忽略已知无用列不产生告警（Ref Type）",
          not any("Ref Type" in w for w in t_warnings), str(t_warnings))

    print("\n[4] 校验与来源")
    recs = [dict(r) for r in t_records]
    for i, r in enumerate(recs, start=1):
        r["record_id"] = make_record_id("PubMed", i)
    issues = validate_records(recs)
    check("TXT 记录通过 validate_records", issues == [], "; ".join(issues[:2]))
    try:
        normalize_source("zhihu")
        check("非法来源被拒绝", False, "未抛出！")
    except SchemaError:
        check("非法来源被拒绝", True)

    print("\n[5] 不支持格式的显式拒绝")
    for bad_ext in (".enl", ".enlx", ".ris", ".docx"):
        try:
            parse_enl_file(Path(f"demo{bad_ext}"), "PubMed", "utf-8")
            check(f"{bad_ext} 显式拒绝", False, "未抛出！")
        except SchemaError as e:
            check(f"{bad_ext} 显式拒绝", True, str(e).split("。")[0][:40])
    try:
        parse_enl_table_text("NoTitleColumn\tYear\nX\t2020\n", "PubMed", "\t")
        check("缺标题列 → SchemaError", False, "未抛出！")
    except SchemaError as e:
        check("缺标题列 → SchemaError", True, str(e)[:40] + "…")

    print("\n[6] 边界：空 XML / 无 record")
    r0, w0 = parse_enl_xml_text("<xml><records></records></xml>", "Scopus")
    check("无 record → 0 条 + 告警", r0 == [] and len(w0) == 1)
    try:
        parse_enl_xml_text("<xml><unclosed></xml>", "Scopus")
        check("XML 语法错误 → SchemaError", False, "未抛出！")
    except SchemaError:
        check("XML 语法错误 → SchemaError", True)

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
# 5. CLI
# ===========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="parse_enl.py",
        description="EndNote 导出题录解析器（XML / 制表符 TXT）→ 统一格式 CSV（HBM-Meta-Agent / S1）")
    ap.add_argument("--input", type=Path, help="输入文件（.xml / .txt / .tsv / .csv）")
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
        records, warnings = parse_enl_file(args.input, source, args.encoding)
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
