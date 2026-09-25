#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapshot.py — 快照指纹（shared/）

用途
  为每份产出提供"这一版结果基于哪一版数据 + 哪一版口径"的可追溯凭证。
  按 `shared/data-contract.md` §4 定义的格式产出指纹块。

核心作用
  手稿引用的快照哈希与当前快照不一致 → 直接判定 `STALE_MANUSCRIPT`，
  从机制上根除"稿子数值与数据快照不同步"（本项目 R2-2/R3-1 三条审稿意见的根因）。

设计要点
  1. `tag` 格式：`{analyte}_{YYYY-MM-DD}`（analyte 来自 project.yaml；无配置时回退文件名词干）
  2. `sha256`：**文件字节级**哈希（保证任何字节改动都能被发现）
  3. `record_count` / `study_count`：从表内读取
  4. 指纹块自带 `snapshot_yaml_block()`，供报告头部直接嵌入

用法
  from snapshot import build_snapshot, render_fingerprint_block
  snap = build_snapshot(Path("主表.xlsx"), analyte="As")     # 见函数签名
  print(render_fingerprint_block(snap))

自检
  python snapshot.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


CONTRACT_VERSION = "1.0.0"
SHA_DISPLAY_LEN = 12          # 可读显示长度（完整哈希仍保留）
CHUNK = 1 << 20               # 1 MiB


class SnapshotError(Exception):
    """快照构建失败（文件缺失/不可读/无记录等）。"""


# ===========================================================================
# 1. 数据结构
# ===========================================================================

@dataclass
class Snapshot:
    tag: str                                  # {analyte}_{YYYY-MM-DD}
    sha256: str                               # 完整 64 位十六进制
    record_count: int = 0
    study_count: int = 0
    source_table: str = ""
    generated_at: str = ""
    config_version: str = "1.0.0"
    contract_version: str = CONTRACT_VERSION
    generator: str = ""
    git_commit: str = ""

    @property
    def short_sha(self) -> str:
        return self.sha256[:SHA_DISPLAY_LEN] if self.sha256 else ""

    def fingerprint(self) -> str:
        """单行指纹，用于日志与表格：`tag@short_sha`。"""
        return f"{self.tag}@{self.short_sha}"

    def block(self) -> dict:
        """按 data-contract.md §4 的字段顺序返回。"""
        return {
            "source_table": self.source_table,
            "sha256": self.sha256,
            "generated_at": self.generated_at,
            "config_version": self.config_version,
            "contract_version": self.contract_version,
            "record_count": self.record_count,
            "study_count": self.study_count,
            "generator": self.generator,
            "git_commit": self.git_commit,
        }

    def yaml_block(self) -> str:
        """渲染为可直接嵌入报告头部的 YAML 块。"""
        b = self.block()
        lines = ["snapshot:"]
        for k in ("source_table", "sha256", "generated_at", "config_version",
                  "contract_version", "record_count", "study_count",
                  "generator", "git_commit"):
            v = b[k]
            lines.append(f"  {k}: {v if v != '' else 'null'}")
        return "\n".join(lines)

    def markdown_block(self) -> str:
        """渲染为 Markdown 代码块（供报告直接嵌入）。"""
        return "```yaml\n" + self.yaml_block() + "\n```"

    def same_data_as(self, other: "Snapshot | None") -> bool:
        """
        数据是否同一版本。**任一哈希为空时返回 False**（不可判定 ≠ 相同，
        保守起见视为不同，促使调用方显式声明快照）。
        """
        if other is None:
            return False
        if not self.sha256 or not other.sha256:
            return False
        return self.sha256 == other.sha256

    def is_stale_against(self, current: "Snapshot") -> bool:
        """本快照（如稿件绑定的）相对 current 是否已过时。"""
        return not self.same_data_as(current)


# ===========================================================================
# 2. 哈希与计数
# ===========================================================================

def file_sha256(path: Path) -> str:
    """文件**字节级** sha256（分块读取，支持大文件）。"""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError as exc:
        raise SnapshotError(f"无法读取主表以计算哈希：{exc}") from exc
    return h.hexdigest()


def read_counts(path: Path) -> tuple[int, int]:
    """
    返回 (record_count, study_count)。
    record_count = 表内数据行数（跳过表头与全空行）
    study_count  = 唯一 `study_no` 数；该列缺失时退化为 record_count
    """
    rows: list[dict] = []
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            with path.open(encoding="utf-8-sig", newline="") as fh:
                rows = [dict(r) for r in csv.DictReader(fh)]
        except OSError as exc:
            raise SnapshotError(f"无法读取 CSV：{exc}") from exc
        except UnicodeDecodeError as exc:
            raise SnapshotError(f"CSV 编码不是 UTF-8：{exc}") from exc
    elif suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise SnapshotError("需要 openpyxl：pip install openpyxl") from exc
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb[wb.sheetnames[0]]
            it = ws.iter_rows(values_only=True)
            hdr = [str(h).strip() if h is not None else "" for h in next(it)]
            for r in it:
                if r is None or all(v is None for v in r):
                    continue          # 跳过全空行
                rows.append({hdr[i]: (r[i] if i < len(r) else None)
                             for i in range(len(hdr))})
            wb.close()
        except OSError as exc:
            raise SnapshotError(f"无法读取 Excel：{exc}") from exc
    elif suffix == ".xls":
        raise SnapshotError("不支持旧版 .xls，请先另存为 .xlsx 或 .csv")
    else:
        raise SnapshotError(f"不支持的主表类型：{suffix}")

    record_count = len(rows)
    studies = {str(r.get("study_no") or "").strip() for r in rows}
    studies.discard("")
    study_count = len(studies) if studies else record_count
    return record_count, study_count


def derive_tag(path: Path, analyte: str | None = None,
               on: date | None = None) -> str:
    """
    tag = `{analyte}_{YYYY-MM-DD}`。
    analyte 为空时回退到文件名词干（去掉已有的日期后缀，避免重复）。
    """
    d = on or date.today()
    if analyte:
        return f"{analyte}_{d.isoformat()}"
    stem = path.stem
    # 去掉形如 `_2026-06-05` 的日期后缀
    import re
    stem = re.sub(r"_\d{4}-\d{2}-\d{2}$", "", stem)
    return f"{stem}_{d.isoformat()}"


def now_iso() -> str:
    """本地时区 ISO8601（含偏移）。"""
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


# ===========================================================================
# 3. 构建
# ===========================================================================

def build_snapshot(table_path: Path | str,
                   analyte: str | None = None,
                   config_version: str = "1.0.0",
                   generator: str = "",
                   git_commit: str = "",
                   on: date | None = None) -> Snapshot:
    """从主表构建快照指纹。文件不存在/不受支持/不可读 → SnapshotError。"""
    p = Path(table_path)
    if not p.exists():
        raise SnapshotError(f"主表不存在：{p}")
    if not p.is_file():
        raise SnapshotError(f"不是文件：{p}")

    sha = file_sha256(p)
    n_rec, n_study = read_counts(p)
    return Snapshot(
        tag=derive_tag(p, analyte, on),
        sha256=sha,
        record_count=n_rec,
        study_count=n_study,
        source_table=str(p),
        generated_at=now_iso(),
        config_version=config_version,
        generator=generator or Path(sys.argv[0]).name,
        git_commit=git_commit,
    )


def detect_stale(manuscript_snapshot: "Snapshot | None",
                 current_snapshot: "Snapshot") -> bool:
    """
    判定"稿件绑定的快照"相对"当前快照"是否已过时。

    语义（与 `Snapshot.is_stale_against` 互补）：
      · 稿件**未声明**绑定快照（None）→ 返回 False（无法判定，但不主动判漂移；
        调用方应同时提示"无快照保护"）
      · 任一哈希为空 → 返回 False（不可判定）
      · 哈希不同 → True（STALE_MANUSCRIPT）
    """
    if manuscript_snapshot is None:
        return False
    if not manuscript_snapshot.sha256 or not current_snapshot.sha256:
        return False
    return manuscript_snapshot.sha256 != current_snapshot.sha256


def render_fingerprint_block(snap: Snapshot, style: str = "yaml") -> str:
    """渲染指纹块：style = 'yaml'（默认）或 'markdown'。"""
    if style == "markdown":
        return snap.markdown_block()
    return snap.yaml_block()


# ===========================================================================
# 4. 快照登记表
# ===========================================================================

def append_snapshot_registry(registry_path: Path, snap: Snapshot,
                             note: str = "") -> None:
    """
    追加一行到 `_state/snapshots.csv`（列：snapshot_tag,sha256,created_at,record_count,study_count,note）。
    旧行**永不删除**（审计链）。
    """
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    header = ["snapshot_tag", "sha256", "created_at", "record_count",
              "study_count", "note"]
    new_file = not registry_path.exists()
    with registry_path.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(header)
        w.writerow([snap.tag, snap.sha256, snap.generated_at,
                    snap.record_count, snap.study_count, note])


# ===========================================================================
# 5. self-test
# ===========================================================================

def run_self_test() -> int:
    import tempfile

    print("=" * 70)
    print("snapshot.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    tmp = Path(tempfile.mkdtemp(prefix="hbm_snap_"))

    def write_csv(name: str, rows: list[dict], cols: list[str]) -> Path:
        p = tmp / name
        with p.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        return p

    cols = ["record_id", "study_no", "sample_type", "time", "gm_summary", "sample_size"]
    rows = [
        {"record_id": "R1", "study_no": "S001", "sample_type": "Urine",
         "time": "1995", "gm_summary": "32.58", "sample_size": "1000"},
        {"record_id": "R2", "study_no": "S001", "sample_type": "Urine",
         "time": "1998", "gm_summary": "30.00", "sample_size": "500"},
        {"record_id": "R3", "study_no": "S002", "sample_type": "Blood",
         "time": "2015", "gm_summary": "2.72", "sample_size": "600"},
    ]
    table = write_csv("As_2026-06-05.csv", rows, cols)

    print("\n[1] 哈希计算")
    h1 = file_sha256(table)
    h2 = file_sha256(table)
    check("sha256 为 64 位十六进制",
          len(h1) == 64 and all(c in "0123456789abcdef" for c in h1), f"{h1[:16]}…")
    check("同一文件两次哈希一致（稳定性）", h1 == h2)
    check("short_sha 为 12 位", len(h1[:SHA_DISPLAY_LEN]) == SHA_DISPLAY_LEN)
    # 与标准库核对
    check("与 hashlib 直接计算一致",
          h1 == hashlib.sha256(table.read_bytes()).hexdigest())

    print("\n[2] 字节级敏感性（任何字节改动都要被发现）")
    table2 = write_csv("copy.csv", rows, cols)
    check("内容相同的不同文件哈希相同", file_sha256(table2) == h1)
    rows_mod = [dict(r) for r in rows]
    rows_mod[0]["gm_summary"] = "32.59"      # 改 1 个数字
    table3 = write_csv("mod.csv", rows_mod, cols)
    check("★ 改 1 个字符 → 哈希改变", file_sha256(table3) != h1)
    # 仅追加一个空行（字节改变但行数不变，因为空行被跳过）
    with (tmp / "trail.csv").open("w", encoding="utf-8", newline="") as fh:
        fh.write(table.read_text(encoding="utf-8") + "\n")
    check("★ 仅追加换行 → 哈希改变（字节级）",
          file_sha256(tmp / "trail.csv") != h1)
    check("但记录数不变（空行被跳过）",
          read_counts(tmp / "trail.csv")[0] == 3,
          str(read_counts(tmp / "trail.csv")))

    print("\n[3] 计数")
    n_rec, n_study = read_counts(table)
    check("record_count = 3", n_rec == 3, str(n_rec))
    check("study_count = 2（S001 两行、S002 一行）", n_study == 2, str(n_study))
    # 无 study_no 列 → 退化为 record_count
    no_sno = write_csv("nosno.csv", [{"record_id": "R1"}, {"record_id": "R2"}],
                       ["record_id"])
    check("缺 study_no 列 → study_count 退化为 record_count",
          read_counts(no_sno) == (2, 2), str(read_counts(no_sno)))
    # 空表（仅表头）
    empty = write_csv("empty.csv", [], cols)
    check("仅表头 → (0, 0)", read_counts(empty) == (0, 0), str(read_counts(empty)))

    print("\n[4] tag 格式")
    d = date(2026, 6, 5)
    check("analyte 给定 → As_2026-06-05",
          derive_tag(table, "As", d) == "As_2026-06-05", derive_tag(table, "As", d))
    check("analyte 为空 → 用文件名词干",
          derive_tag(table, None, d) == "As_2026-06-05",
          derive_tag(table, None, d))
    check("★ 文件名已含日期时不重复",
          derive_tag(Path("PFOA_2027-01-20.csv"), None, d) == "PFOA_2026-06-05",
          derive_tag(Path("PFOA_2027-01-20.csv"), None, d))
    check("analyte 为空 + 无日期文件名",
          derive_tag(Path("mytable.csv"), None, d) == "mytable_2026-06-05",
          derive_tag(Path("mytable.csv"), None, d))

    print("\n[5] 构建快照")
    snap = build_snapshot(table, analyte="As", on=d, generator="snapshot.py --self-test")
    check("tag 正确", snap.tag == "As_2026-06-05", snap.tag)
    check("sha256 正确", snap.sha256 == h1)
    check("计数正确", (snap.record_count, snap.study_count) == (3, 2))
    check("source_table 已记录", snap.source_table.endswith("As_2026-06-05.csv"))
    check("generated_at 含时区", "+" in snap.generated_at or snap.generated_at.endswith("Z"),
          snap.generated_at)
    check("fingerprint = tag@short_sha",
          snap.fingerprint() == f"As_2026-06-05@{h1[:12]}", snap.fingerprint())

    print("\n[6] 指纹块（data-contract.md §4 字段齐全）")
    blk = snap.block()
    need = ["source_table", "sha256", "generated_at", "config_version",
            "contract_version", "record_count", "study_count", "generator",
            "git_commit"]
    check("字段齐全", all(k in blk for k in need), str(sorted(blk)))
    y = snap.yaml_block()
    check("YAML 块以 snapshot: 开头", y.startswith("snapshot:"))
    check("YAML 含 sha256", "sha256:" in y)
    check("Markdown 块包裹正确",
          snap.markdown_block().startswith("```yaml") and
          snap.markdown_block().endswith("```"))
    check("render_fingerprint_block 两种风格可用",
          render_fingerprint_block(snap) == y and
          render_fingerprint_block(snap, "markdown") == snap.markdown_block())

    print("\n[7] ★ 漂移判定")
    snap_same = build_snapshot(table, analyte="As", on=d)
    check("同数据 → same_data_as 为 True", snap.same_data_as(snap_same))
    check("同数据 → is_stale_against 为 False", not snap.is_stale_against(snap_same))
    snap_other = build_snapshot(table3, analyte="As", on=d)
    check("★ 数据改动 → same_data_as 为 False", not snap.same_data_as(snap_other))
    check("★ 数据改动 → is_stale_against 为 True", snap.is_stale_against(snap_other))
    check("★ None → same_data_as 为 False（保守）", not snap.same_data_as(None))
    empty_sha = Snapshot(tag="X", sha256="")
    check("★ 空哈希 → same_data_as 为 False（不可判定≠相同）",
          not snap.same_data_as(empty_sha) and not empty_sha.same_data_as(snap))

    print("\n[7b] detect_stale（模块级函数，供 audit_numbers 等复用）")
    check("哈希不同 → True", detect_stale(snap, snap_other) is True)
    check("哈希相同 → False", detect_stale(snap, snap_same) is False)
    check("稿件未声明（None）→ False（但需另提示无保护）",
          detect_stale(None, snap) is False)
    check("任一哈希为空 → False（不可判定）",
          detect_stale(empty_sha, snap) is False
          and detect_stale(snap, empty_sha) is False)
    check("与 is_stale_against 语义互补（同为真的情形一致）",
          detect_stale(snap, snap_other) == snap.is_stale_against(snap_other))

    print("\n[8] 错误处理")
    for label, arg in [("不存在的文件", tmp / "nope.csv"),
                       ("目录", tmp)]:
        try:
            build_snapshot(arg)
            check(f"{label} → SnapshotError", False, "未抛出！")
        except SnapshotError as exc:
            check(f"{label} → SnapshotError", True, str(exc)[:32] + "…")
    bad_ext = tmp / "x.txt"
    bad_ext.write_text("a,b\n1,2\n", encoding="utf-8")
    try:
        build_snapshot(bad_ext)
        check("不支持的类型 → SnapshotError", False, "未抛出！")
    except SnapshotError as exc:
        check("不支持的类型 → SnapshotError", True, str(exc)[:32] + "…")

    print("\n[9] 快照登记表（追加、不覆盖）")
    reg = tmp / "_state" / "snapshots.csv"
    append_snapshot_registry(reg, snap, "first")
    append_snapshot_registry(reg, snap_other, "second")
    lines = reg.read_text(encoding="utf-8-sig").strip().splitlines()
    check("表头 + 2 行", len(lines) == 3, str(len(lines)))
    check("表头正确", lines[0].startswith("snapshot_tag,sha256"))
    check("旧行未被覆盖", "first" in lines[1] and "second" in lines[2])

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
# 6. CLI
# ===========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="snapshot.py",
        description="快照指纹：{tag, sha256, record_count, study_count}（HBM-Meta-Agent / shared）")
    ap.add_argument("--table", type=Path, help="主表路径（.csv / .xlsx）")
    ap.add_argument("--analyte", help="分析物（用于 tag；缺省则用文件名词干）")
    ap.add_argument("--config-version", default="1.0.0")
    ap.add_argument("--config", type=Path, help="project.yaml（提供后读 analyte 与 config_version）")
    ap.add_argument("--register", type=Path, help="追加到快照登记表（如 _state/snapshots.csv）")
    ap.add_argument("--note", default="", help="登记备注")
    ap.add_argument("--style", choices=["yaml", "markdown"], default="yaml")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--version", action="version", version=f"hbm-meta snapshot {CONTRACT_VERSION}")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.table:
        print("错误：需要 --table，或使用 --self-test", file=sys.stderr)
        return 2

    analyte = args.analyte
    cfg_ver = args.config_version
    if args.config:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from project_config import ConfigError, load_config
            cfg = load_config(args.config)
            analyte = analyte or cfg.analyte
            cfg_ver = cfg.config_version
        except ConfigError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

    try:
        snap = build_snapshot(args.table, analyte=analyte,
                              config_version=cfg_ver, generator="snapshot.py")
    except SnapshotError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(render_fingerprint_block(snap, args.style))
    if args.register:
        append_snapshot_registry(args.register, snap, args.note)
        print(f"已登记：{args.register}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
