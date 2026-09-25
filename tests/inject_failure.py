#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inject_failure.py — 故意注入断言失败以验证 CI 真的能挡住问题（STEP4-TODO C5）

用法
  python inject_failure.py --inject            # 注入失败（备份原文件）
  python inject_failure.py --restore           # 还原
  python inject_failure.py --inject --check    # 注入并演示校验链拦截，然后还原

设计：仅改一个断言（把 True 改为 False），不改逻辑；备份到同目录 .bak。
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
VICTIM = ROOT / "skills/S6-qc-audit-revision/scripts/sensitivity.py"
BAK = VICTIM.with_suffix(".py.bak")

# 一个稳定存在的断言（形态判定的边界用例）
NEEDLE = 'check("阈值边界：升 5.0% → →", trend_label(100.0, 105.0) == "→"'
REPLACE = 'check("阈值边界：升 5.0% → →【CI 注入测试】", False and trend_label(100.0, 105.0) == "→"'


def inject() -> int:
    if not VICTIM.exists():
        print(f"错误：目标不存在 {VICTIM}", file=sys.stderr)
        return 2
    if BAK.exists():
        print(f"提示：已存在备份 {BAK.name}，先还原再注入", file=sys.stderr)
        return 2
    src = io.open(VICTIM, encoding="utf-8").read()
    if NEEDLE not in src:
        print("错误：未找到注入锚点（文件可能已变更）", file=sys.stderr)
        return 2
    shutil.copy2(VICTIM, BAK)
    io.open(VICTIM, "w", encoding="utf-8", newline="").write(
        src.replace(NEEDLE, REPLACE, 1))
    print(f"已注入断言失败：{VICTIM.name}（备份 {BAK.name}）")
    return 0


def restore() -> int:
    if not BAK.exists():
        print("错误：无备份可还原", file=sys.stderr)
        return 2
    shutil.copy2(BAK, VICTIM)
    BAK.unlink()
    print(f"已还原：{VICTIM.name}")
    return 0


def show_blocking() -> int:
    """演示校验链是否拦截：目标自检 + C6 + C1。"""
    print("\n" + "=" * 68)
    print("验证 CI 是否能挡住（期望：全部变红）")
    print("=" * 68)
    blocked = 0

    print("\n[1] 目标脚本自身 --self-test")
    r = subprocess.run([sys.executable, str(VICTIM), "--self-test"],
                       capture_output=True)
    tail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    print(f"    exit={r.returncode}  {tail[-1] if tail else ''}")
    blocked += 1 if r.returncode != 0 else 0

    print("\n[2] tests/verify_consistency.py（C6 自检冒烟）")
    r = subprocess.run([sys.executable, str(ROOT / "tests/verify_consistency.py")],
                       capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    fails = [ln.strip() for ln in out.splitlines() if "自检失败" in ln]
    res = [ln.strip() for ln in out.splitlines() if "核验结果" in ln]
    print(f"    exit={r.returncode}  {res[-1] if res else ''}")
    for f in fails:
        print(f"    {f}")
    blocked += 1 if r.returncode != 0 else 0

    print(f"\n被拦截的检查项数：{blocked}/2")
    if blocked == 0:
        print("❌ CI 未能拦住注入的失败 —— 校验链无效！")
        return 1
    print("✅ CI 能拦住注入的失败（校验链有效）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="inject_failure.py",
        description="故意注入断言失败以验证 CI（STEP4-TODO C5）")
    ap.add_argument("--inject", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="注入后演示拦截并自动还原")
    args = ap.parse_args(argv)

    if args.restore:
        return restore()
    if not args.inject:
        ap.print_help()
        return 0

    rc = inject()
    if rc != 0:
        return rc
    if args.check:
        rc_check = show_blocking()
        restore()
        print("\n（已自动还原为正常状态）")
        return rc_check
    print("提示：验证后用 --restore 还原")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
