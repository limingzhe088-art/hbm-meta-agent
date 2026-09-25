#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_consistency.py — 跨脚本一致性核验（★ A1–A4 的最终验收标准）

为什么需要
  `inferred_flags` 曾因"5 处各写一份字典"而漏同步 2 处（`validate_table.py` 16 项、
  `wan_convert.py` 漏 2 项）。现在字典已收敛为 `shared/inferred_flags.py` 单一来源，
  但**单一来源本身也需要被验证**：必须证明"任何脚本对同一 `conversion_path`
  产出的标记集合完全相同"，否则收敛只是形式。

核验内容
  C1  所有涉及 `inferred_flags` 的脚本要么从单一来源导入，要么不自行派生标记
  C2  `derive_flags()` 对同一 `conversion_path` 的输出**确定性**（两次运行一致）
  C3  反推规则表覆盖闭集内所有会被登记的标记（无遗漏、无越界）
  C4  S4 端到端产出的每个标记都在闭集内（含 S3/S4/S5 各脚本的产出路径）
  C5  登记处（脚本写 `inferred_flags` 字段的地方）与反推处使用同一来源
  C6  各脚本自检全部可执行（冒烟：`--self-test` 退出码 0）

用法
  python tests/verify_consistency.py            # 全部核验
  python tests/verify_consistency.py --json     # 结构化输出（CI 用）
"""
from __future__ import annotations

import argparse
import io
import json
import os
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
SHARED = ROOT / "shared"
sys.path.insert(0, str(SHARED))

try:
    from inferred_flags import CLOSED_FLAG_SET, CONTRACT_DOCUMENTED, SCRIPT_INTERNAL
except ImportError as exc:  # pragma: no cover
    print(f"错误：无法导入 shared/inferred_flags.py（{exc}）", file=sys.stderr)
    raise SystemExit(2)

# 可能需要处理 inferred_flags 的脚本（相对 ROOT）
CANDIDATE_SCRIPTS = [
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

# 权威反推实现所在文件
DERIVE_OWNER = "skills/S4-unit-statistic-conversion/scripts/wan_convert.py"

# 用于 C2/C4 的测试用 conversion_path（覆盖全部规则表命中的片段）
TEST_PATHS = [
    "reported_GM",
    "reported_creatinine_corrected",
    "AM_SD→AMSD_to_LN→GM",
    "Median_IQR→Median_as_GM→GM",
    "Median_IQR→Wan_S4→GM",
    "Median_IQR→Wan_S6→GM",
    "Median_Range→Wan_S2→GM",
    "Min_Max→Wan_S1→GM",
    "Min_Max→Wan_S3→GM",
    "Median_IQR→Wan_S7→GM",
    "AM_IQR→Wan_S5→GM",
    "nmol/L→ug/L→reported_GM",
    "Median_IQR→Wan_S4→ug/L→ICRP89_Adults→ug/g Cr→GM",
    "ug/g Cr→ICRP89_Adults→ug/L",
    "Median_IQR→Wan_S4→GM;time_represented",
    "AM_SD→AMSD_to_LN→GM;blood_matrix_assumed",
    "reported_GM;species_total_assumed",
    "sd_from_iqr→AM_SD→AMSD_to_LN→GM",
]

# 会被 derive_flags 登记的全部标记（用于 C3 覆盖核对）
DERIVABLE = {
    "gm_from_mean_sd", "gm_from_median", "gm_from_iqr", "gm_from_range",
    "no_n_fallback", "molar_conversion", "unit_converted_creatinine",
    "unit_converted_volume", "time_represented", "blood_matrix_assumed",
    "species_total_assumed", "sd_from_iqr",
}


def _read(rel: str) -> str:
    p = ROOT / rel
    return io.open(p, encoding="utf-8", errors="replace").read() if p.exists() else ""


def _load_module(rel: str, name: str):
    """按路径加载脚本模块（不执行其 main）。"""
    import importlib.util
    p = ROOT / rel
    if not p.exists():
        return None, f"{rel} 不存在"
    # 脚本自身会向 sys.path 注入依赖目录；此处先补 shared 与 S5，便于导入
    for extra in (SHARED, ROOT / "skills/S5-weighted-pooling-edi/scripts",
                  p.parent):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    # ★ 必须先注册进 sys.modules：dataclass 等装饰器会通过
    #   sys.modules[cls.__module__] 查找模块，未注册会抛 AttributeError。
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit as exc:
        sys.modules.pop(name, None)
        return None, f"{rel} 在导入时退出（exit {exc.code}）"
    except Exception as exc:
        sys.modules.pop(name, None)
        return None, f"{rel} 导入失败：{type(exc).__name__}: {exc}"
    return mod, ""


# ===========================================================================
# 各项核验
# ===========================================================================

def check_c1_imports() -> tuple[bool, list[str]]:
    """C1：涉及标记的脚本必须从单一来源导入，或确实不派生/不校验标记。"""
    problems: list[str] = []
    details: list[str] = []
    for rel in CANDIDATE_SCRIPTS:
        src = _read(rel)
        if not src:
            problems.append(f"{rel} 不存在")
            continue
        imports_registry = bool(re.search(
            r"from\s+inferred_flags\s+import|import\s+inferred_flags", src))
        # 是否自行派生标记（在代码里写 flags 列表 / 反推规则表）
        derives = bool(re.search(r"flags\s*=\s*\[|flags\.append\(", src))
        defines_local_dict = bool(re.search(
            r"^(INFERRED_FLAG_DICT|CLOSED_FLAG_DICT)\s*=\s*\{", src, re.M))
        registers = '"inferred_flags"' in src or "'inferred_flags'" in src

        if defines_local_dict:
            problems.append(f"{rel} 仍自建标记字典副本（应导入单一来源）")
        if (derives or registers) and not imports_registry:
            problems.append(
                f"{rel} 涉及 inferred_flags（{'派生' if derives else ''}"
                f"{'登记' if registers else ''}）但未从单一来源导入")
        details.append(
            f"{Path(rel).name}: 导入={'是' if imports_registry else '否'} "
            f"派生={'是' if derives else '否'} "
            f"登记={'是' if registers else '否'}")
    return not problems, problems + ["  " + d for d in details]


def check_c2_determinism() -> tuple[bool, list[str]]:
    """C2：derive_flags 对同一路径两次运行结果一致（确定性）。"""
    mod, err = _load_module(DERIVE_OWNER, "wc_consistency")
    if mod is None:
        return False, [err]
    problems: list[str] = []
    for path in TEST_PATHS:
        a, _ = mod.derive_flags(path)
        b, _ = mod.derive_flags(path)
        # 标记是**集合**语义（写入主表时用 `;` 连接，顺序不承载含义），
        # 因此按集合比较；但仍要求输出内部无重复。
        if set(a) != set(b):
            problems.append(f"`{path}` 两次结果不同：{a} vs {b}")
        if len(a) != len(set(a)):
            problems.append(f"`{path}` 输出有重复项：{a}")
    return not problems, problems


def check_c3_rule_coverage() -> tuple[bool, list[str]]:
    """C3：反推规则表覆盖闭集内全部可派生标记，且无越界。"""
    mod, err = _load_module(DERIVE_OWNER, "wc_coverage")
    if mod is None:
        return False, [err]
    problems: list[str] = []
    derived: set[str] = set()
    for path in TEST_PATHS:
        fl, _ = mod.derive_flags(path)
        derived |= set(fl)
    missing = DERIVABLE - derived
    if missing:
        problems.append(f"可派生但测试路径未覆盖：{sorted(missing)}")
    out_of_set = derived - CLOSED_FLAG_SET
    if out_of_set:
        problems.append(f"导出了闭集外的标记：{sorted(out_of_set)}")
    # 规则表里的标记必须都在闭集内
    table = getattr(mod, "RULE_TABLE", [])
    bad = {f for _, fl in table for f in fl if f not in CLOSED_FLAG_SET}
    if bad:
        problems.append(f"RULE_TABLE 含闭集外标记：{sorted(bad)}")
    if not table:
        problems.append("RULE_TABLE 为空（无法核对）")
    return not problems, problems + [
        f"规则表条目={len(table)}　测试路径={len(TEST_PATHS)}　覆盖标记={len(derived)}"]


def check_c4_end_to_end() -> tuple[bool, list[str]]:
    """C4：S4 端到端产出的每个标记都在闭集内。"""
    mod, err = _load_module(DERIVE_OWNER, "wc_e2e")
    if mod is None:
        return False, [err]
    problems: list[str] = []
    cases = [
        {"stat_type": "GM_GSD", "initial_gm": "23.5", "initial_gsd": "2.1"},
        {"stat_type": "AM_SD", "initial_mean": "2.72", "initial_sd": "0.90",
         "sample_size": "800"},
        {"stat_type": "Median_IQR", "initial_median": "7.5", "initial_p25": "5.2",
         "initial_p75": "9.1", "sample_size": "450"},
        {"stat_type": "Median_IQR", "initial_median": "7.5", "initial_p25": "5.2",
         "initial_p75": "9.1"},
        {"stat_type": "Min_Max", "initial_min": "1", "initial_max": "5",
         "initial_mean": "3", "sample_size": "100"},
    ]
    seen: set[str] = set()
    for strategy in ("as_gm", "via_wan"):
        for case in cases:
            try:
                r = mod.convert_record(dict(case), variant="minus",
                                       median_strategy=strategy)
            except Exception as exc:
                problems.append(f"{case['stat_type']}/{strategy} 抛异常：{exc}")
                continue
            flags, _ = mod.derive_flags(r.path)
            seen |= set(flags)
            out = set(flags) - CLOSED_FLAG_SET
            if out:
                problems.append(
                    f"{case['stat_type']}/{strategy} 产出闭集外标记：{sorted(out)}")
    return not problems, problems + [f"端到端覆盖标记：{sorted(seen)}"]


def check_c5_derivation_consistency() -> tuple[bool, list[str]]:
    """C5：唯一反推实现——不得有第二个 derive_flags 定义。"""
    defs: list[str] = []
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        src = io.open(p, encoding="utf-8", errors="replace").read()
        if re.search(r"^def\s+derive_flags\s*\(", src, re.M):
            defs.append(str(p.relative_to(ROOT)).replace(os.sep, "/"))
    problems: list[str] = []
    if len(defs) != 1:
        problems.append(f"derive_flags 定义了 {len(defs)} 处（应为 1 处）：{defs}")
    if defs and defs[0] != DERIVE_OWNER:
        problems.append(f"唯一实现位于 {defs[0]}，与预期 {DERIVE_OWNER} 不符")
    # 登记处必须调用 derive_flags（而非直接用内部 flags）
    wsrc = _read(DERIVE_OWNER)
    if "authoritative_flags" not in wsrc:
        problems.append("登记处未使用 derive_flags 的权威结果（缺少唯一来源规则）")
    return not problems, problems + [f"derive_flags 定义处：{defs}"]


def check_c6_selftests() -> tuple[bool, list[str]]:
    """C6：各脚本 --self-test 冒烟（退出码 0）。"""
    targets = ["shared/project_config.py", "shared/inferred_flags.py",
               "shared/snapshot.py"] + CANDIDATE_SCRIPTS
    problems: list[str] = []
    ok_count = 0
    for rel in targets:
        p = ROOT / rel
        if not p.exists():
            problems.append(f"{rel} 不存在")
            continue
        try:
            r = subprocess.run([sys.executable, str(p), "--self-test"],
                               capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            problems.append(f"{rel} 自检超时")
            continue
        if r.returncode != 0:
            tail = (r.stdout or r.stderr or b"").decode("utf-8", "replace")
            tail = tail.strip().splitlines()[-1] if tail.strip() else ""
            problems.append(f"{rel} 自检失败（exit {r.returncode}）：{tail[:80]}")
        else:
            ok_count += 1
    return not problems, problems + [f"自检通过 {ok_count}/{len(targets)}"]


CHECKS = [
    ("C1", "脚本从单一来源导入（或确实不派生标记）", check_c1_imports),
    ("C2", "derive_flags 确定性（两次运行一致）", check_c2_determinism),
    ("C3", "反推规则表覆盖闭集（无遗漏/无越界）", check_c3_rule_coverage),
    ("C4", "S4 端到端产出标记全在闭集内", check_c4_end_to_end),
    ("C5", "唯一反推实现（无第二处 derive_flags）", check_c5_derivation_consistency),
    ("C6", "各脚本 --self-test 冒烟通过", check_c6_selftests),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify_consistency.py",
        description="跨脚本一致性核验（HBM-Meta-Agent，A1–A4 最终验收）")
    ap.add_argument("--json", action="store_true", help="结构化输出")
    ap.add_argument("--skip-selftest", action="store_true",
                    help="跳过 C6（自检冒烟），仅静态与逻辑核验")
    args = ap.parse_args(argv)

    results = []
    print("=" * 72)
    print("verify_consistency.py — 跨脚本一致性核验（A1–A4 最终验收）")
    print("=" * 72)
    for cid, desc, fn in CHECKS:
        if cid == "C6" and args.skip_selftest:
            results.append({"id": cid, "desc": desc, "ok": True, "notes": ["已跳过"]})
            print(f"\n[{cid}] {desc}\n      ⏭  已跳过（--skip-selftest）")
            continue
        ok, notes = fn()
        results.append({"id": cid, "desc": desc, "ok": ok, "notes": notes})
        print(f"\n[{cid}] {desc}")
        for n in notes:
            print(f"      {'✅' if ok else '❌'} {n}" if not n.startswith("  ")
                  else f"        {n}")

    n_ok = sum(1 for r in results if r["ok"])
    print("\n" + "-" * 72)
    print(f"核验结果：{n_ok}/{len(results)} 项通过")
    if n_ok == len(results):
        print("★ A1–A4 验收通过：命名规范/配置单点/口径一致/快照指纹已闭环")
    if args.json:
        print(json.dumps({"ok": n_ok == len(results), "results": results},
                         ensure_ascii=False, indent=2))
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
