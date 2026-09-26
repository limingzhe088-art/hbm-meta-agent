#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_sources.py — 合并多个数据库导出的统一格式题录 CSV（S1 检索策略 / 功能 A）

职责边界
  只做**合并与计数**，不做去重：跨库去重是 S2 的首个人工闸门环节
  （AI 只产疑似重复清单，裁决由人做）。本脚本仅输出"疑似重复"计数供参考。

输入
  若干个由 parse_ris / parse_enl / parse_bibtex 产出的统一格式 CSV
  （表头必须与 shared/record_schema.py 的 RECORD_FIELDS 完全一致，
    不一致即硬错误 —— 不做静默重排/补列）。

输出
  1. all_raw_records.csv —— 合并后的全部题录（source_database 保留各库来源）
  2. source_summary.csv —— 每个数据库的检索式 / 检索日期 / 命中数 / 导出数：
       - exported_count 由本脚本从合并结果**实算**（不采信人工填报）
       - search_string / search_date / hit_count **只能来自人工维护的检索日志**
         （--search-log，人工在 GATE-1 确认后填写）；未提供时留空并标注
         "待人工回填（GATE-1）" —— AI 不代填检索式与命中数

用法
  python merge_sources.py \\
      --inputs raw_records_pubmed.csv raw_records_wos.csv \\
      --output all_raw_records.csv --summary source_summary.csv \\
      [--search-log search_log.csv]

  search_log.csv 表头（人工维护）：source_database,search_string,search_date,hit_count

退出码
  0 成功   2 前置条件不满足（文件缺失 / 表头不符 / record_id 冲突 / 检索日志非法）
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# shared/record_schema.py —— 统一字段的单一来源
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from record_schema import (  # noqa: E402
        NOTE_PENDING_FILL, RAW_SOURCE_ENUM, RECORD_FIELDS, SEARCH_LOG_FIELDS,
        SUMMARY_FIELDS, SchemaError, normalize_source, normalize_text,
        read_records_csv, write_records_csv,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/record_schema.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


def load_search_log(path: Path) -> dict[str, dict[str, str]]:
    """读取人工维护的检索日志 → {规范来源名: {search_string, search_date, hit_count}}。"""
    try:
        with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            problems = [f for f in SEARCH_LOG_FIELDS if f not in header]
            if problems:
                raise SchemaError(
                    f"检索日志 {path} 缺少列 {problems}。"
                    f"表头必须为：{','.join(SEARCH_LOG_FIELDS)}。")
            log: dict[str, dict[str, str]] = {}
            for row_no, row in enumerate(reader, start=2):
                src_raw = normalize_text(row.get("source_database", ""))
                if not src_raw:
                    raise SchemaError(f"检索日志 {path} 第 {row_no} 行：source_database 为空")
                src = normalize_source(src_raw)
                hit = normalize_text(row.get("hit_count", ""))
                if hit and not hit.isdigit():
                    raise SchemaError(
                        f"检索日志 {path} 第 {row_no} 行：hit_count=`{hit}` 不是非负整数"
                        "（命中数只能人工填报，且必须为数字）。")
                log[src] = {
                    "search_string": normalize_text(row.get("search_string", "")),
                    "search_date": normalize_text(row.get("search_date", "")),
                    "hit_count": hit,
                }
            return log
    except UnicodeDecodeError as exc:
        raise SchemaError(f"{path} 无法以 utf-8-sig 解码（{exc}）。") from exc
    except OSError as exc:
        raise SchemaError(f"无法读取 {path}：{exc}") from exc


def build_summary(rows: list[dict],
                  search_log: dict[str, dict[str, str]] | None) -> list[dict]:
    """
    生成 source_summary 行。
    - exported_count 从合并结果实算
    - 检索式 / 日期 / 命中数仅取自人工检索日志；缺 → 留空 + 待回填标注
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    for row in rows:
        src = row.get("source_database", "")
        if src not in counts:
            counts[src] = 0
            order.append(src)
        counts[src] += 1

    summary: list[dict] = []
    for src in order:
        entry = (search_log or {}).get(src, {})
        filled = bool(entry.get("search_string")) or bool(entry.get("hit_count"))
        summary.append({
            "source_database": src,
            "search_string": entry.get("search_string", ""),
            "search_date": entry.get("search_date", ""),
            "hit_count": entry.get("hit_count", ""),
            "exported_count": str(counts[src]),
            "note": "" if filled else NOTE_PENDING_FILL,
        })
    return summary


def write_summary_csv(path: Path, summary: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(SUMMARY_FIELDS)
        for row in summary:
            writer.writerow([row.get(f, "") for f in SUMMARY_FIELDS])


def merge(inputs: list[Path]) -> tuple[list[dict], list[str]]:
    """合并多个统一格式 CSV → (合并记录, per-file 导入说明)。"""
    merged: list[dict] = []
    seen_ids: dict[str, str] = {}
    notes: list[str] = []
    for path in inputs:
        if not path.exists():
            raise SchemaError(f"输入文件不存在：{path}")
        records = read_records_csv(path)   # 表头不符 → SchemaError
        new_ids = 0
        for rec in records:
            rid = rec.get("record_id", "")
            if rid in seen_ids:
                raise SchemaError(
                    f"record_id 冲突：`{rid}` 同时出现在 {seen_ids[rid]} 与 {path}。"
                    "record_id 含来源前缀，正常情况下不应冲突；"
                    "请检查是否把同一来源的文件传了两次。")
            seen_ids[rid] = str(path)
            merged.append(rec)
            new_ids += 1
        notes.append(f"{path.name}: {new_ids} 条")
    return merged, notes


def suspect_duplicate_count(rows: list[dict]) -> tuple[int, dict[str, int]]:
    """
    统计疑似重复（同 raw_row_hash 出现 >1 次）。
    只计数与列清单，**不删除、不改 record_id** —— 去重裁决在 S2（GATE-2）。
    """
    by_hash: dict[str, list[str]] = {}
    for row in rows:
        h = row.get("raw_row_hash", "")
        if h:
            by_hash.setdefault(h, []).append(row.get("record_id", ""))
    dup_hash = {h: ids for h, ids in by_hash.items() if len(ids) > 1}
    # 跨库 vs 库内分布（信息用）
    cross = intra = 0
    hash_src: dict[str, set] = {}
    for row in rows:
        hash_src.setdefault(row.get("raw_row_hash", ""), set()).add(
            row.get("source_database", ""))
    for h, srcs in hash_src.items():
        if len(srcs) > 1:
            cross += 1
        elif h in dup_hash:
            intra += 1
    return len(dup_hash), {"cross_source_groups": cross, "intra_source_groups": intra}


# ===========================================================================
# self-test
# ===========================================================================

def _mk_csv(path: Path, rows: list[dict]) -> None:
    write_records_csv(path, rows)


def _sample_row(**kw) -> dict:
    base = {f: "" for f in RECORD_FIELDS}
    base.update({
        "record_id": "pubmed-00001", "source_database": "PubMed",
        "title": "Default title", "year": "2020",
    })
    from record_schema import compute_row_hash
    base["raw_row_hash"] = compute_row_hash(base["title"], base["abstract"])
    base.update(kw)
    if "raw_row_hash" not in kw:
        base["raw_row_hash"] = compute_row_hash(base["title"], base["abstract"])
    return base


def run_self_test() -> int:
    print("=" * 70)
    print("merge_sources.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_merge_"))

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    rows_a = [
        _sample_row(record_id="pubmed-00001", source_database="PubMed",
                    title="Urinary arsenic study one"),
        _sample_row(record_id="pubmed-00002", source_database="PubMed",
                    title="Urinary arsenic study two"),
    ]
    rows_b = [
        _sample_row(record_id="wos-00001", source_database="WebOfScience",
                    title="Blood mercury in parturients"),
        # 与 pubmed-00001 同标题（同哈希）→ 疑似重复（S2 裁决）
        _sample_row(record_id="wos-00002", source_database="WebOfScience",
                    title="Urinary arsenic study one"),
    ]
    fa = tmp / "a.csv"
    fb = tmp / "b.csv"
    _mk_csv(fa, rows_a)
    _mk_csv(fb, rows_b)

    print("\n[1] 合并基本行为")
    merged, notes = merge([fa, fb])
    check("合并后 4 条", len(merged) == 4, f"{len(merged)}")
    check("输入文件顺序保持", merged[0]["record_id"] == "pubmed-00001"
          and merged[2]["record_id"] == "wos-00001")
    check("来源标记保留", merged[2]["source_database"] == "WebOfScience")
    check("per-file 说明生成", len(notes) == 2 and "a.csv" in notes[0])

    print("\n[2] source_summary（exported_count 实算；检索式仅取自人工日志）")
    summary = build_summary(merged, None)
    check("两个来源各一行", len(summary) == 2)
    by_src = {s["source_database"]: s for s in summary}
    check("exported_count 实算 PubMed=2", by_src["PubMed"]["exported_count"] == "2")
    check("exported_count 实算 WoS=2", by_src["WebOfScience"]["exported_count"] == "2")
    check("未提供检索日志 → search_string 留空（AI 不代填）",
          by_src["PubMed"]["search_string"] == "")
    check("未提供检索日志 → 标注待人工回填",
          "待人工回填" in by_src["PubMed"]["note"], by_src["PubMed"]["note"])

    log_rows = [
        {"source_database": "PubMed", "search_string": "(arsenic[tiab]) AND (urine[tiab])",
         "search_date": "2026-07-19", "hit_count": "3120"},
        {"source_database": "WebOfScience", "search_string": "",
         "search_date": "", "hit_count": ""},
    ]
    log_path = tmp / "search_log.csv"
    with io.open(log_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SEARCH_LOG_FIELDS)
        w.writeheader()
        w.writerows(log_rows)
    log = load_search_log(log_path)
    summary2 = build_summary(merged, log)
    by_src2 = {s["source_database"]: s for s in summary2}
    check("检索式/命中数取自人工日志",
          by_src2["PubMed"]["hit_count"] == "3120"
          and "arsenic[tiab]" in by_src2["PubMed"]["search_string"])
    check("日志中留空的来源 → 待回填标注",
          "待人工回填" in by_src2["WebOfScience"]["note"])
    check("exported_count 不采信日志（仍为实算 2）",
          by_src2["PubMed"]["exported_count"] == "2")

    print("\n[3] 疑似重复只计数不删除")
    dup_total, dup_detail = suspect_duplicate_count(merged)
    check("检出 1 组疑似重复", dup_total == 1, f"{dup_total}")
    check("跨库/库内分布正确",
          dup_detail["cross_source_groups"] == 1 and dup_detail["intra_source_groups"] == 0,
          str(dup_detail))
    check("疑似重复记录未被删除（仍 4 条）", len(merged) == 4)

    print("\n[4] 硬错误（显式拒绝，不静默）")
    dup_file = tmp / "dup.csv"
    _mk_csv(dup_file, [_sample_row(record_id="pubmed-00001",
                                   source_database="PubMed", title="Other")])
    try:
        merge([fa, dup_file])
        check("record_id 冲突 → SchemaError", False, "未抛出！")
    except SchemaError as e:
        check("record_id 冲突 → SchemaError", True, str(e)[:40] + "…")
    bad_header = tmp / "bad.csv"
    bad_header.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    try:
        merge([bad_header])
        check("表头不符 → SchemaError", False, "未抛出！")
    except SchemaError as e:
        check("表头不符 → SchemaError", True, str(e)[:40] + "…")
    try:
        merge([tmp / "missing.csv"])
        check("文件缺失 → SchemaError", False, "未抛出！")
    except SchemaError:
        check("文件缺失 → SchemaError", True)
    bad_log = tmp / "bad_log.csv"
    bad_log.write_text("source_database,search_string,search_date,hit_count\n"
                       "pubmed,s,2026-07-19,many\n", encoding="utf-8")
    try:
        load_search_log(bad_log)
        check("hit_count 非数字 → SchemaError", False, "未抛出！")
    except SchemaError as e:
        check("hit_count 非数字 → SchemaError", True, str(e)[:40] + "…")

    print("\n[5] 写盘往返")
    out_path = tmp / "all_raw_records.csv"
    sum_path = tmp / "source_summary.csv"
    write_records_csv(out_path, merged)
    write_summary_csv(sum_path, summary2)
    back = read_records_csv(out_path)
    check("合并结果可回读且行数一致", len(back) == 4)
    check("summary 列齐全",
          list(summary2[0].keys()) == SUMMARY_FIELDS)

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
        prog="merge_sources.py",
        description="合并多库统一格式题录 CSV 并生成检索来源汇总（HBM-Meta-Agent / S1）")
    ap.add_argument("--inputs", nargs="+", type=Path, default=[],
                    help="多个统一格式 CSV（parse_ris/parse_enl/parse_bibtex 的输出）")
    ap.add_argument("--output", type=Path,
                    help="合并输出 CSV（如 all_raw_records.csv）")
    ap.add_argument("--summary", type=Path,
                    help="检索来源汇总 CSV（如 source_summary.csv）")
    ap.add_argument("--search-log", type=Path,
                    help="人工维护的检索日志 CSV（GATE-1 确认后填写）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--inputs", args.inputs), ("--output", args.output),
                              ("--summary", args.summary)) if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2

    try:
        merged, notes = merge(args.inputs)
        search_log = load_search_log(args.search_log) if args.search_log else None
    except SchemaError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    write_records_csv(args.output, merged)
    summary = build_summary(merged, search_log)
    write_summary_csv(args.summary, summary)

    dup_total, dup_detail = suspect_duplicate_count(merged)
    total_hits = sum(int(s["hit_count"]) for s in summary
                     if s["hit_count"].isdigit())
    print(f"已合并 {len(merged)} 条题录 → {args.output}")
    for n in notes:
        print(f"  - {n}")
    print(f"检索来源汇总（{len(summary)} 个库）→ {args.summary}")
    print(f"  PRISMA identification：命中数合计（已回填者）= {total_hits}；"
          f"导出数合计 = {len(merged)}")
    print(f"  疑似重复 {dup_total} 组"
          f"（跨库 {dup_detail['cross_source_groups']} / 库内 "
          f"{dup_detail['intra_source_groups']}）—— 仅计数，去重裁决在 S2 人工完成")
    blank = [s["source_database"] for s in summary if not s["search_string"]]
    if blank:
        print(f"  ⚠️ 以下库的检索式/命中数待人工回填（GATE-1）：{', '.join(blank)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
