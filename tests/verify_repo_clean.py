#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_repo_clean.py — 仓库洁净度校验：字节码残留 + 文本文件 CRLF

为什么需要它（两次真实事故）
  1. 远端提交 78c8029（GitHub 网页 "Add file → Upload files"）
     **网页上传不读 .gitignore**，把 shared/__pycache__/*.pyc 等 7 个字节码文件
     一起提交了（本机 .gitignore 明明写着 __pycache__/）。
  2. 同一次上传把 4 个 CSV 的行尾从 LF 变成 CRLF。
     CRLF 会破坏脚本里大量"精确文本比较"断言（跨平台 clone 后自检变红），
     也会污染 Release 包。

设计要点
  * 检查对象是**版本库内容**（`git cat-file blob :<path>` / `git ls-tree`），
    不是工作区——只有查版本库才看得见"已经提交进去的脏东西"。
  * **纯 Python 实现**（不依赖 GNU grep / PCRE / bash 数组），
    使 `tests/ci_local.py` 与 `.github/workflows/validate.yml` 走同一套判定逻辑，
    避免"两处实现、两套结论"。

用法
  python tests/verify_repo_clean.py            # 校验，失败 exit 1
  python tests/verify_repo_clean.py --verbose  # 列出全部被检查的文本文件
  python tests/verify_repo_clean.py --self-test
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

# 文本文件判定：按扩展名，或按无扩展名的常见文本文件名
TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".json", ".csv", ".tsv",
                 ".cff", ".txt", ".toml", ".cfg", ".ini", ".rst", ".gitkeep"}
TEXT_EXACT = {".gitignore", ".gitattributes", "LICENSE", "NOTICE"}

# 字节码 / 缓存目录标记
BYTECODE_MARKERS = ("__pycache__/", ".pyc", ".pyo", ".pyd", ".pytest_cache/",
                    ".mypy_cache/", ".ruff_cache/")


def _git(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=str(cwd or ROOT),
                          capture_output=True, text=True, errors="replace")


def is_text_path(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in TEXT_SUFFIXES or p.name in TEXT_EXACT


def tracked_files(cwd: Path | None = None) -> list[str]:
    r = _git("ls-files", "--full-name", cwd=cwd)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def blob_bytes(path: str, cwd: Path | None = None) -> bytes:
    """取版本库内该路径的原始字节（走索引 :path，即已入库内容）。"""
    r = subprocess.run(["git", "cat-file", "blob", ":" + path],
                       cwd=str(cwd or ROOT), capture_output=True)
    return r.stdout


def blob_bytes_rev(path: str, rev: str, cwd: Path | None = None) -> bytes:
    """取指定版本（如 origin/main、HEAD~1）内该路径的原始字节。"""
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"],
                       cwd=str(cwd or ROOT), capture_output=True)
    return r.stdout


def audit_rev(rev: str, cwd: Path | None = None,
              verbose: bool = False) -> tuple[bool, list[str], dict]:
    """校验**任意 git 版本**的内容洁净度（用于核验历史提交或远端分支）。"""
    notes: list[str] = []
    ls = subprocess.run(["git", "ls-tree", "-r", "--name-only", rev],
                        cwd=str(cwd or ROOT), capture_output=True, text=True,
                        errors="replace")
    if ls.returncode != 0:
        return False, [f"❌ 无法读取版本 {rev}: {ls.stderr.strip()}"], {}
    files = [ln for ln in ls.stdout.splitlines() if ln.strip()]
    bytecode = [f for f in files if any(m in f for m in BYTECODE_MARKERS)]
    text_files = [f for f in files if is_text_path(f)]
    crlf_files = []
    for f in text_files:
        b = blob_bytes_rev(f, rev, cwd)
        if b"\r\n" in b:
            crlf_files.append((f, b.count(b"\r\n")))
    ok = not bytecode and not crlf_files
    notes.append(f"{'✅' if not bytecode else '❌'} 版本 {rev} 无字节码"
                 f"（{len(files)} 个文件）" + (f" → {bytecode}" if bytecode else ""))
    notes.append(f"{'✅' if not crlf_files else '❌'} 版本 {rev} 文本一律 LF"
                 f"（{len(text_files)} 个文本文件）")
    for f, n in crlf_files:
        notes.append(f"      CRLF ×{n:<4} {f}")
    if verbose:
        for f in text_files:
            notes.append(f"        · {f}")
    return ok, notes, {"tracked": len(files), "text": len(text_files),
                       "bytecode": len(bytecode), "crlf": len(crlf_files)}


def audit(cwd: Path | None = None, verbose: bool = False) -> tuple[bool, list[str], dict]:
    notes: list[str] = []
    probe = _git("rev-parse", "--is-inside-work-tree", cwd=cwd)
    if probe.returncode != 0:
        return True, ["⏭  跳过：当前不是 git 工作树"], {}

    files = tracked_files(cwd)
    bytecode = [f for f in files if any(m in f for m in BYTECODE_MARKERS)]
    text_files = [f for f in files if is_text_path(f)]

    crlf_files = []
    for f in text_files:
        b = blob_bytes(f, cwd)
        if b"\r\n" in b:
            n = b.count(b"\r\n")
            crlf_files.append((f, n))

    ok = not bytecode and not crlf_files
    notes.append(f"{'✅' if not bytecode else '❌'} 无字节码/缓存"
                 f"（扫描 {len(files)} 个已入库文件）"
                 + (f" → {bytecode}" if bytecode else ""))
    notes.append(f"{'✅' if not crlf_files else '❌'} 版本库内文本一律 LF"
                 f"（检查 {len(text_files)} 个文本文件）")
    for f, n in crlf_files:
        notes.append(f"      CRLF ×{n:<4} {f}")
    if verbose:
        notes.append("      被检查的文本文件：")
        for f in text_files:
            notes.append(f"        · {f}")

    stats = {"tracked": len(files), "text": len(text_files),
             "bytecode": len(bytecode), "crlf": len(crlf_files)}
    return ok, notes, stats


# ── 自检：验证判定逻辑本身（含正反例构造）─────────────────────────────
def self_test() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    check("文本扩展名 .py 判为文本", is_text_path("a/b/c.py"))
    check("文本扩展名 .csv 判为文本", is_text_path("skills/x/templates/t.csv"))
    check("无扩展名 .gitignore 判为文本", is_text_path(".gitignore"))
    check("LICENSE 判为文本", is_text_path("LICENSE"))
    check("图片 .png 不判为文本", not is_text_path("docs/a.png"))
    check("数据 .xlsx 不判为文本", not is_text_path("data/raw.xlsx"))

    check("字节码探针命中 __pycache__",
          any(m in "shared/__pycache__/x.cpython-312.pyc" for m in BYTECODE_MARKERS))
    check("字节码探针命中 .pyc 后缀",
          any(m in "a/b.pyc" for m in BYTECODE_MARKERS))
    check("字节码探针不误伤 docs.py",
          not any(m in "tests/docs.py" for m in BYTECODE_MARKERS))
    check("字节码探针不误伤 .pytest_cache 之外的目录",
          not any(m in "tests/cache_helper.py" for m in BYTECODE_MARKERS))

    check("CRLF 检测：\\r\\n 命中", b"a\r\nb".count(b"\r\n") == 1)
    check("CRLF 检测：纯 LF 不命中", b"a\nb".count(b"\r\n") == 0)
    check("CRLF 检测：CR 非换行不命中", b"a\rb".count(b"\r\n") == 0)

    # 真实仓库现状（在仓库内跑才有意义）
    if _git("rev-parse", "--is-inside-work-tree").returncode == 0:
        ok, notes, stats = audit()
        check("真实仓库当前洁净", ok, "; ".join(notes[:2]))
        check("真实仓库已入库文件数 > 0", stats.get("tracked", 0) > 0,
              str(stats))

    # 反向验证：临时构造一个"脏"仓库，守门脚本必须报红并点名
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dirty = Path(td)
        _git("init", "-q", ".", cwd=dirty)
        # ★ 关键：本仓库的 .gitattributes（* text=auto eol=lf）会作用到 %TEMP% 下的
        #   临时仓库，使 git add 自动把 CRLF 规范成 LF，反向用例就永远构造不出来
        #   （实测：漏掉这一行时 CRLF 用例 0 命中）。故显式声明 `* -text`，
        #   让该临时仓库按字节原样入库，才能验证 CRLF 探针真的有效。
        (dirty / ".gitattributes").write_bytes(b"* -text\n")
        (dirty / "shared" / "__pycache__").mkdir(parents=True)
        (dirty / "shared" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\x01\x02")
        (dirty / "README.md").write_bytes(b"# t\r\nline\r\n")
        (dirty / "ok.py").write_bytes(b"# fine\n")
        _git("add", "-A", "-f", cwd=dirty)
        ok2, notes2, stats2 = audit(cwd=dirty)
        check("反向：脏仓库被判为不洁净", not ok2)
        check("反向：点名字节码", stats2.get("bytecode", 0) >= 1, str(stats2))
        check("反向：点名 CRLF 文本", stats2.get("crlf", 0) >= 1, str(stats2))
        joined = " ".join(notes2)
        check("反向：输出含 __pycache__ 路径", "__pycache__" in joined)
        check("反向：输出含 README.md", "README.md" in joined)
        check("反向：干净文件 ok.py 未被误点名",
              "ok.py" not in joined.replace("ok.py\n", ""))

    fails = [c for c in checks if not c[1]]
    print("=" * 68)
    print("verify_repo_clean.py — 自检")
    print("=" * 68)
    for name, ok_, detail in checks:
        print(f"  {'✅' if ok_ else '❌'} {name}" + (f"   [{detail}]" if detail else ""))
    print("-" * 68)
    print(f"self-test 通过：{len(checks) - len(fails)}/{len(checks)} 项全部通过"
          if not fails else f"self-test 失败：{len(fails)}/{len(checks)} 项未通过")
    return 0 if not fails else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify_repo_clean.py",
        description="仓库洁净度校验：字节码残留 + 文本文件 CRLF")
    ap.add_argument("--verbose", action="store_true", help="列出被检查的文件")
    ap.add_argument("--self-test", action="store_true", help="校验判定逻辑本身")
    ap.add_argument("--repo", default=None,
                    help="被校验仓库的工作树路径（默认：本仓库）")
    ap.add_argument("--rev", default=None,
                    help="改为校验指定 git 版本（如 origin/main、HEAD~1）")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    cwd = Path(args.repo).resolve() if args.repo else None
    label = f"（仓库：{cwd}）" if cwd else ""
    print("=" * 68)
    print(f"verify_repo_clean.py — 仓库洁净度校验（字节码 / CRLF）{label}")
    print("=" * 68)
    if args.rev:
        ok, notes, stats = audit_rev(args.rev, cwd=cwd, verbose=args.verbose)
    else:
        ok, notes, stats = audit(cwd=cwd, verbose=args.verbose)
    for n in notes:
        print(n)
    print("-" * 68)
    if ok:
        print(f"✅ 洁净：{stats.get('tracked')} 个已入库文件，"
              f"{stats.get('text')} 个文本文件全部 LF，无字节码")
        return 0
    print(f"❌ 不洁净：字节码 {stats.get('bytecode')} 个，"
          f"CRLF 文本 {stats.get('crlf')} 个")
    print("   修复：git rm -r --cached <路径> 后重新提交；")
    print("         行尾问题见 .gitattributes（* text=auto eol=lf）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
