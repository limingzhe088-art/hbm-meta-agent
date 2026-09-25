#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ci_local.py — 本地模拟 CI（在无 GitHub Actions 环境下预演 validate.yml）

用途
  在没有 GitHub 环境的机器上验证 `.github/workflows/validate.yml` 的每一步，
  避免"推上去才发现 CI 红"。CI 的实际权威仍是 GitHub Actions；本脚本是**预演**。

覆盖步骤
  1  全部脚本 --self-test
  2  模板与实现一致性（gen_templates.py --check）
  3  结构完整性（tests/verify_structure.py）
  4  跨脚本一致性（tests/verify_consistency.py --skip-selftest）
  5  源码卫生：损坏字符探针 / 硬编码路径 / 口径兜底
  6  随包文件是否真的入库（★ 针对"磁盘有、仓库没有"的漏提交）

步骤 6 的由来（CI run #1 真实事故）
  自检在**开发机工作区**里跑，读的是磁盘上的文件；而 CI 在**全新克隆**里跑，
  只看得到 git 追踪的文件。`.gitignore` 里一条锚定路径写错（`!templates/*.csv`
  只豁免根目录，漏掉 `skills/<子技能>/templates/`），使 4 个模板 CSV 永不被提交。
  后果：本地 5/5 全绿，推上去 C1 立刻变红——**本地预演没能预测 CI**。
  本步骤把"文件到底在不在版本库里"变成显式断言，堵住这一类盲区。

用法
  python tests/ci_local.py
  python tests/ci_local.py --verbose
"""
from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

SELFTEST_TARGETS = [
    "shared/project_config.py",
    "shared/inferred_flags.py",
    "shared/snapshot.py",
    "skills/S3-extraction-standardization/scripts/validate_table.py",
    "skills/S4-unit-statistic-conversion/scripts/wan_convert.py",
    "skills/S4-unit-statistic-conversion/scripts/convert_units.py",
    "skills/S5-weighted-pooling-edi/scripts/weighted_gm.py",
    "skills/S5-weighted-pooling-edi/scripts/edi_calculation.py",
    "skills/S5-weighted-pooling-edi/scripts/gen_templates.py",
    "skills/S6-qc-audit-revision/scripts/audit_numbers.py",
    "skills/S6-qc-audit-revision/scripts/dedup_screen.py",
    "skills/S6-qc-audit-revision/scripts/sensitivity.py",
    "skills/S6-qc-audit-revision/scripts/verify_dois.py",
    "skills/S6-qc-audit-revision/scripts/citation_crosswalk.py",
]

# 探针：损坏字符（见 agent/routing.md §2.6）
PROBE_CORRUPT = re.compile(
    r"\b(chdck|gith|rogs|loger|geighted|Falsd|Nond|negline|hdaddr|ddf|ddi)\b")
# 硬编码路径（见 STEP4-TODO A2）
PROBE_HARDPATH = re.compile(r"E:\\|重金属暴露|/Users/|C:\\\\Users")
# 口径兜底（见 STEP4-TODO A1）
PROBE_DEFAULT = re.compile(
    r"^(DEFAULT_CONFIG|DEFAULT_PERIOD_SCHEME|DEFAULT_EDI_PARAMS|"
    r"DEFAULT_UNIT_POLICY|DEFAULT_URINE_REFERENCE)\s*=", re.M)

SCAN_DIRS = ["shared", "skills", "tests"]

# ★ 自我排除：本文件与注入工具**包含探针模式本身**（如正则里的 `chdck`、
#   文档示例里的 `E:\`），扫描它们必然假阳性。GitHub Actions 的 grep 不受影响
#   （它扫的是工作树文件，不扫 workflow 定义本身），此处需显式排除以保持一致。
SELF_EXCLUDE = {"tests/ci_local.py", "tests/inject_failure.py"}


def _py_files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            if p.relative_to(ROOT).as_posix() in SELF_EXCLUDE:
                continue
            out.append(p)
    return sorted(out)


def step_selftests(verbose: bool) -> tuple[bool, list[str]]:
    notes: list[str] = []
    fails = 0
    for rel in SELFTEST_TARGETS:
        p = ROOT / rel
        if not p.exists():
            notes.append(f"❌ MISSING {rel}")
            fails += 1
            continue
        r = subprocess.run([sys.executable, str(p), "--self-test"],
                           capture_output=True, timeout=300)
        out = (r.stdout or b"").decode("utf-8", "replace")
        line = next((ln.strip() for ln in out.splitlines()
                     if "self-test" in ln and ("通过" in ln or "失败" in ln)), "")
        if r.returncode == 0:
            notes.append(f"✅ {Path(rel).name:26s} {line}")
        else:
            fails += 1
            tail = out.strip().splitlines()[-1] if out.strip() else ""
            notes.append(f"❌ {Path(rel).name:26s} exit={r.returncode} {tail[:60]}")
    notes.append(f"—— 自检失败数：{fails} / {len(SELFTEST_TARGETS)} ——")
    return fails == 0, notes


def step_gen_templates(verbose: bool) -> tuple[bool, list[str]]:
    p = ROOT / "skills/S5-weighted-pooling-edi/scripts/gen_templates.py"
    r = subprocess.run([sys.executable, str(p), "--check"], capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    line = next((ln.strip() for ln in out.splitlines() if "self-test" in ln), "")
    return r.returncode == 0, [f"{'✅' if r.returncode == 0 else '❌'} {line}"]


def step_structure(verbose: bool) -> tuple[bool, list[str]]:
    p = ROOT / "tests/verify_structure.py"
    r = subprocess.run([sys.executable, str(p)], capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    line = next((ln.strip() for ln in out.splitlines() if "结构校验" in ln), "")
    return r.returncode == 0, [f"{'✅' if r.returncode == 0 else '❌'} {line}"]


def step_consistency(verbose: bool) -> tuple[bool, list[str]]:
    p = ROOT / "tests/verify_consistency.py"
    r = subprocess.run([sys.executable, str(p), "--skip-selftest"],
                       capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    line = next((ln.strip() for ln in out.splitlines() if "核验结果" in ln), "")
    return r.returncode == 0, [f"{'✅' if r.returncode == 0 else '❌'} {line}"]


def step_hygiene(verbose: bool) -> tuple[bool, list[str]]:
    notes: list[str] = []
    files = _py_files()
    hits_corrupt, hits_path, hits_default = [], [], []
    for p in files:
        src = io.open(p, encoding="utf-8", errors="replace").read()
        rel = p.relative_to(ROOT).as_posix()
        if PROBE_CORRUPT.search(src):
            hits_corrupt.append(rel)
        if PROBE_HARDPATH.search(src):
            hits_path.append(rel)
        if PROBE_DEFAULT.search(src):
            hits_default.append(rel)
    ok = not (hits_corrupt or hits_path or hits_default)
    notes.append(f"{'✅' if not hits_corrupt else '❌'} 损坏字符探针"
                 f"（扫描 {len(files)} 个 .py）"
                 + (f" → {hits_corrupt}" if hits_corrupt else ""))
    notes.append(f"{'✅' if not hits_path else '❌'} 硬编码路径"
                 + (f" → {hits_path}" if hits_path else ""))
    notes.append(f"{'✅' if not hits_default else '❌'} 口径兜底（DEFAULT_*）"
                 + (f" → {hits_default}" if hits_default else ""))
    return ok, notes


# ── 步骤 6：随包文件必须真的入库 ──────────────────────────────────────
# 由 CI run #1 事故确立：这些文件被脚本在自检中直接读取，若被 .gitignore
# 拦下，全新克隆里必然缺失。任何新增"脚本要读的模板/示例"都必须加进来。
REQUIRED_SHIPPED = [
    "templates/project.yaml",
    "skills/S3-extraction-standardization/templates/extraction_template.csv",
    "skills/S4-unit-statistic-conversion/templates/conversion_trace.csv",
    "skills/S5-weighted-pooling-edi/templates/edi_results_OUTPUT_example.csv",
    "skills/S5-weighted-pooling-edi/templates/pooled_results_OUTPUT_example.csv",
    "examples/arsenic-china-1980-2024/03-extraction/master_table_EXAMPLE.csv",
]

# 只做"确实存在但未被追踪"的提示，不据此判失败（避免暂存区状态影响结论）
_TRACK_NOISE = ("__pycache__/", "_state/", ".bak")


def _git(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=str(ROOT),
                          capture_output=True, text=True, errors="replace")


def step_shipped_files(verbose: bool) -> tuple[bool, list[str]]:
    """断言：脚本要读的随包文件，在 git 追踪列表里（而非只存在于本机磁盘）。"""
    notes: list[str] = []
    probe = _git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0:
        notes.append("⏭  跳过：当前不是 git 工作树（无法判定入库状态）")
        return True, notes

    ok = True

    # (a) 必需文件：既存在、又未被忽略、又被追踪
    missing, ignored, untracked = [], [], []
    for rel in REQUIRED_SHIPPED:
        if not (ROOT / rel).exists():
            missing.append(rel)
            continue
        if _git("check-ignore", "-q", "--", rel).returncode == 0:
            ignored.append(rel)
            continue
        if _git("ls-files", "--error-unmatch", "--", rel).returncode != 0:
            untracked.append(rel)
    if missing:
        ok = False
        notes.append(f"❌ 必需文件不存在（跑生成脚本）→ {missing}")
    if ignored:
        ok = False
        notes.append(f"❌ 必需文件被 .gitignore 忽略（CI 克隆后缺失）→ {ignored}")
    if untracked:
        ok = False
        notes.append(f"❌ 必需文件未入库（git add 遗漏）→ {untracked}")
    if not (missing or ignored or untracked):
        notes.append(f"✅ {len(REQUIRED_SHIPPED)} 个随包文件均已入库且未被忽略")

    # (b) 影响面提示：仓库内被 ignore 的 .csv（负向规则失效时会在这里露头）
    ls = _git("ls-files", "--others", "--ignored", "--exclude-standard")
    ignored_csv = [ln for ln in ls.stdout.splitlines()
                   if ln.lower().endswith(".csv")
                   and not any(n in ln for n in _TRACK_NOISE)]
    if ignored_csv:
        notes.append(f"⚠️  仓库内被忽略的 .csv（确认是否应入库）→ {ignored_csv}")

    return ok, notes


STEPS = [
    ("1  全部脚本 --self-test", step_selftests),
    ("2  模板与实现一致性", step_gen_templates),
    ("3  结构完整性", step_structure),
    ("4  跨脚本一致性", step_consistency),
    ("5  源码卫生（探针/硬编码/兜底）", step_hygiene),
    ("6  随包文件已入库（防漏提交）", step_shipped_files),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ci_local.py",
        description="本地模拟 CI（预演 .github/workflows/validate.yml）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("ci_local.py — 本地模拟 CI（预演 validate.yml）")
    print("=" * 72)
    results = []
    for title, fn in STEPS:
        ok, notes = fn(args.verbose)
        results.append((title, ok))
        print(f"\n[{title}]")
        for n in notes:
            print(f"      {n}")

    n_ok = sum(1 for _, ok in results if ok)
    print("\n" + "-" * 72)
    for title, ok in results:
        print(f"  {'✅' if ok else '❌'} {title}")
    print(f"\nCI 预演结果：{n_ok}/{len(results)} 步通过")
    if n_ok == len(results):
        print("★ 全部通过 —— 推送到 GitHub 后 CI 应同样变绿")
    else:
        print("❌ 存在失败步骤 —— GitHub CI 将会变红，请先修复")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
