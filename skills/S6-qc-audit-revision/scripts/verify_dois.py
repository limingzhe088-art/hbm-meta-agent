#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_dois.py — DOI 核验（Crossref）+ 字段级差异清单

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --out / --self-test / --offline）
  ✅ --self-test（比对逻辑用本地假响应验证，**不依赖网络**）
  ✅ 核心逻辑：
       DOI 归一化与有效性检查
       Crossref 查询（带 User-Agent、超时、重试）
       字段比对（标题 / 期刊 / 年份 / 卷 / 期 / 页）与容差规则
       四状态分类：VERIFIED / MISMATCH / DOI_INVALID / NO_DOI
       输出字段级修改清单（对照模板 04）
  ⏳ 第四步待办：库文件（.enl / .ris / .bib）解析器接入、CI 离线自检

设计说明
  网络核验与比对逻辑**分离**：比对函数 `compare_record()` 为纯函数，
  `--self-test` 用构造的 Crossref 响应验证它，因此 CI 可离线运行。
  真机核验由 `--online`（默认开启）触发。

用法
  python verify_dois.py --self-test                    # 离线自检
  python verify_dois.py --input refs.csv --out 清单.md # 联网核验
  python verify_dois.py --input refs.csv --offline     # 仅格式检查，不联网
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


CROSSREF_API = "https://api.crossref.org/works/"
USER_AGENT = "hbm-meta-agent/1.0 (https://github.com/; mailto:noreply@example.com)"
TIMEOUT = 30
MAX_RETRY = 2
SLEEP_BETWEEN = 0.5          # 礼貌限速

STATUS_VERIFIED = "VERIFIED"
STATUS_MISMATCH = "MISMATCH"
STATUS_INVALID = "DOI_INVALID"
STATUS_NO_DOI = "NO_DOI"

DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$")

TITLE_PREFIX_LEN = 30


# ===========================================================================
# 1. DOI 归一化
# ===========================================================================

def normalize_doi(raw: str) -> str:
    """去除 URL 前缀、空白与多余标点。"""
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    return s.strip().rstrip(".")


def is_valid_doi_format(doi: str) -> bool:
    return bool(DOI_RE.match(doi or ""))


# ===========================================================================
# 2. 字段比对（纯函数，可离线自检）
# ===========================================================================

@dataclass
class FieldDiff:
    field: str
    local: str
    remote: str


@dataclass
class VerifyResult:
    key: str                      # 作者+年份（人读标识）
    doi: str
    status: str = STATUS_VERIFIED
    diffs: list[FieldDiff] = field(default_factory=list)
    remote_title: str = ""
    message: str = ""


def _norm_title(t: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", (t or "").lower())
    return " ".join(s.split())


def _norm_journal(j: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", (j or "").lower())
    # 常见缩写与全称差异：只比较归一后的字符序列是否互为前缀包含关系
    return s


def journals_match(a: str, b: str) -> bool:
    """
    期刊名比对，容忍缩写。

    说明：`Environ` 不是 `Environmental` 的前缀，因此不能用子串法。
    采用**逐词首字母/前缀**匹配：
      - 完全一致（归一后）→ 通过
      - 一方是另一方的子串 → 通过（如 `Environ Int` ⊂ `Environment International`）
      - 缩写逐词匹配全称逐词（`na[i]` 是 `nb[j]` 的前缀，j 单调不减）→ 通过
    """
    na, nb = _norm_journal(a), _norm_journal(b)
    if not na or not nb:
        return True          # 缺一不可判错
    if na == nb:
        return True
    if na in nb or nb in na:
        return True

    wa = re.findall(r"[a-z]+", (a or "").lower())
    wb = re.findall(r"[a-z]+", (b or "").lower())
    if not wa or not wb:
        return True
    if len(wa) > len(wb):
        wa, wb = wb, wa

    j = 0
    for w in wa:
        if j >= len(wb):
            return False
        # 逐词必须在全称中按顺序找到前缀匹配（允许跳过被省略的词）
        while j < len(wb) and not wb[j].startswith(w):
            j += 1
        if j >= len(wb):
            return False
        j += 1
    return True


def titles_match(a: str, b: str, prefix_len: int = TITLE_PREFIX_LEN) -> bool:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return True
    return na[:prefix_len] == nb[:prefix_len]


def compare_record(local: dict, remote: dict) -> VerifyResult:
    """
    local : {'key','doi','title','journal','year','volume','issue','pages'}
    remote: Crossref message dict
    """
    key = local.get("key") or local.get("doi", "")
    doi = normalize_doi(local.get("doi", ""))
    res = VerifyResult(key=key, doi=doi)

    if not doi or str(doi).upper() == "NO_DOI":
        res.status = STATUS_NO_DOI
        res.message = "无 DOI，需人工核验"
        return res
    if not is_valid_doi_format(doi):
        res.status = STATUS_INVALID
        res.message = f"DOI 格式非法：{doi}"
        return res

    if not remote:
        res.status = STATUS_INVALID
        res.message = "Crossref 无返回"
        return res

    r_title = (remote.get("title") or [""])[0]
    r_journal = (remote.get("container-title") or [""])[0] if remote.get("container-title") else ""
    r_year = str((remote.get("issued", {}).get("date-parts", [[None]])[0] or [None])[0] or "")
    r_vol = str(remote.get("volume", "") or "")
    r_issue = str(remote.get("issue", "") or "")
    r_pages = str(remote.get("page", "") or "")
    res.remote_title = r_title

    if not titles_match(local.get("title", ""), r_title):
        res.diffs.append(FieldDiff("Title", local.get("title", ""), r_title))
    if not journals_match(local.get("journal", ""), r_journal):
        res.diffs.append(FieldDiff("Journal", local.get("journal", ""), r_journal))
    if str(local.get("year", "")).strip() and str(local.get("year", "")).strip() != r_year:
        res.diffs.append(FieldDiff("Year", str(local.get("year", "")), r_year))
    if str(local.get("volume", "")).strip() and r_vol and str(local["volume"]).strip() != r_vol:
        res.diffs.append(FieldDiff("Volume", str(local["volume"]), r_vol))
    if str(local.get("issue", "")).strip() and r_issue and str(local["issue"]).strip() != r_issue:
        res.diffs.append(FieldDiff("Issue", str(local["issue"]), r_issue))
    if str(local.get("pages", "")).strip() and r_pages and str(local["pages"]).strip() != r_pages:
        res.diffs.append(FieldDiff("Pages", str(local["pages"]), r_pages))

    res.status = STATUS_MISMATCH if res.diffs else STATUS_VERIFIED
    if res.status == STATUS_VERIFIED:
        res.message = "全部字段一致"
    else:
        res.message = f"{len(res.diffs)} 个字段不一致"
    return res


# ===========================================================================
# 3. Crossref 查询
# ===========================================================================

def fetch_crossref(doi: str, offline: bool = False) -> dict | None:
    if offline:
        return None
    url = CROSSREF_API + urllib.parse.quote(doi)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRY + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8")).get("message", {})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == MAX_RETRY:
                return None
        except Exception:
            if attempt == MAX_RETRY:
                return None
        time.sleep(1.0)
    return None


# ===========================================================================
# 4. 报告
# ===========================================================================

def render_report(results: list[VerifyResult], source: str) -> str:
    counts = {s: sum(1 for r in results if r.status == s)
              for s in (STATUS_VERIFIED, STATUS_MISMATCH, STATUS_INVALID, STATUS_NO_DOI)}
    lines = [
        "# EndNote 文献库需修改清单（DOI 核验）",
        "",
        f"- 来源：`{source}`",
        f"- 记录数：{len(results)}",
        f"- **VERIFIED** {counts[STATUS_VERIFIED]}　**MISMATCH** {counts[STATUS_MISMATCH]}　"
        f"**DOI_INVALID** {counts[STATUS_INVALID]}　**NO_DOI** {counts[STATUS_NO_DOI]}",
        "",
        "> 在文献库中的定位方法：搜索框输入「作者姓氏+年份」打开该记录，点击相应字段修改。",
        "> **每个重复副本都要改**，或用 Find Duplicates 合并后只改保留的那条。",
        "",
        "## 字段级修改清单",
        "",
    ]
    need_fix = [r for r in results if r.diffs]
    if not need_fix:
        lines.append("无字段级差异。")
    else:
        lines += ["| 文献 | DOI | 字段 | 库中现为 | **应改为** |",
                  "|---|---|---|---|---|"]
        for r in need_fix:
            for i, d in enumerate(r.diffs):
                lines.append(f"| {r.key if i == 0 else '〃'} | {r.doi if i == 0 else '〃'} | "
                             f"{d.field} | {d.local or '（空）'} | **{d.remote}** |")

    lines += ["", "## 需人工核验（无 DOI 或 DOI 无效）", ""]
    manual = [r for r in results if r.status in (STATUS_INVALID, STATUS_NO_DOI)]
    if not manual:
        lines.append("无。")
    else:
        for r in manual:
            lines.append(f"- **{r.key}**：{r.message}"
                         + (f"　远端标题：{r.remote_title[:60]}" if r.remote_title else ""))

    lines += ["", "## 全部记录", "",
              "| 文献 | DOI | 状态 | 远端标题 | 说明 |",
              "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.key} | {r.doi} | **{r.status}** | "
                     f"{r.remote_title[:50] or '—'} | {r.message} |")
    lines += ["", "---", "",
              "> AI 只产出差异与建议；字段是否修改由人工确认"
              "（见 `references/citation_verification_protocol.md` §7）。", ""]
    return "\n".join(lines)


# ===========================================================================
# 5. self-test（离线，使用构造的 Crossref 响应）
# ===========================================================================

def _remote(**kw) -> dict:
    m = {
        "title": [kw.get("title", "")],
        "container-title": [kw.get("journal", "")],
        "issued": {"date-parts": [[int(kw["year"]) if kw.get("year") else None]]},
        "volume": kw.get("volume", ""),
        "issue": kw.get("issue", ""),
        "page": kw.get("pages", ""),
    }
    return m


def run_self_test() -> int:
    print("=" * 70)
    print("verify_dois.py --self-test（离线，不访问网络）")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    print("\n[1] DOI 归一化与格式校验")
    check("去除 https://doi.org/ 前缀",
          normalize_doi("https://doi.org/10.1136/bmj.n160") == "10.1136/bmj.n160")
    check("去除 doi: 前缀",
          normalize_doi("doi:10.1136/bmj.n160") == "10.1136/bmj.n160")
    check("去除尾点", normalize_doi("10.1136/bmj.n160.") == "10.1136/bmj.n160")
    check("合法 DOI", is_valid_doi_format("10.1136/bmj.n160"))
    check("合法 DOI（含斜杠）", is_valid_doi_format("10.1016/j.envint.2014.06.005"))
    # 注：`10.1007/s00204-015-1659-6x` 按 DOI 语法**合法**（后缀可含字母），
    #     因此格式校验不拒它；真实性问题（指向不存在的记录）由 DOI_INVALID 状态兜住。
    check("语合法但解析不到的 DOI 不会被格式校验拒绝",
          is_valid_doi_format("10.1007/s00204-015-1659-6x"))
    check("非法 DOI（后缀缺失）", not is_valid_doi_format("10.1007/"))
    check("非法 DOI（无 10. 前缀）", not is_valid_doi_format("doi-2020-001"))
    check("非法 DOI（纯字符串）", not is_valid_doi_format("not-a-doi"))

    print("\n[2] 标题比对（前 30 字符归一化）")
    check("完全一致", titles_match("Arsenic in drinking water",
                                   "Arsenic in drinking water"))
    check("大小写与标点无关",
          titles_match("Arsenic in Drinking Water: A Review",
                       "arsenic in drinking water a review"))
    check("前 30 字符相同即通过",
          titles_match("A state-of-the-science review of arsenic effects",
                       "A state of the science review of arsenic EFFECTS"))
    check("不同标题不通过",
          not titles_match("Arsenic in drinking water", "Cadmium exposure in children"))

    print("\n[3] 期刊名比对（允许缩写）")
    check("完全一致", journals_match("Environ Int", "Environ Int"))
    check("缩写 vs 全称",
          journals_match("Environ Health Perspect",
                         "Environmental Health Perspectives"))
    check("Int J Environ Res Publ vs 全称",
          journals_match("Int J Environ Res Publ",
                         "International Journal of Environmental Research and Public Health"))
    check("明显不同不通过", not journals_match("Talanta", "Science"))
    check("一方为空不判错", journals_match("", "Science"))

    print("\n[4] VERIFIED")
    local = {"key": "Nigra 2017", "doi": "10.1016/S2468-2667(17)30195-0",
             "title": "The effect of the EPA maximum contaminant level on arsenic exposure",
             "journal": "Lancet Public Health", "year": "2017", "volume": "2",
             "issue": "11", "pages": "e513-e521"}
    remote = _remote(title="The effect of the EPA maximum contaminant level on arsenic exposure",
                     journal="Lancet Public Health", year="2017", volume="2", issue="11",
                     pages="e513-e521")
    r = compare_record(dict(local), remote)
    check("全部一致 → VERIFIED", r.status == STATUS_VERIFIED, r.status)
    check("无字段差异", r.diffs == [], str([d.field for d in r.diffs]))

    print("\n[5] MISMATCH（本项目实证：Castriota 2022）")
    local_c = {"key": "Castriota 2020", "doi": "10.1289/ehp4517",
               "title": "A state-of-the-science review of arsenic's effects on glucose homeostasis",
               "journal": "Environmental Health Perspectives", "year": "2020",
               "volume": "128", "issue": "1", "pages": "016001"}
    remote_c = _remote(
        title="A state-of-the-science review of arsenic's effects on glucose homeostasis",
        journal="Environmental Health Perspectives", year="2022", volume="130",
        issue="1", pages="016001")
    r = compare_record(local_c, remote_c)
    check("→ MISMATCH", r.status == STATUS_MISMATCH, r.status)
    fields = {d.field for d in r.diffs}
    check("检出 Year 差异", "Year" in fields, str(sorted(fields)))
    check("检出 Volume 差异", "Volume" in fields, str(sorted(fields)))
    y = next(d for d in r.diffs if d.field == "Year")
    check("Year: 2020 → 2022", y.local == "2020" and y.remote == "2022",
          f"{y.local} → {y.remote}")

    print("\n[6] MISMATCH（本项目实证：Shen 2016 指向不同文章）")
    local_s = {"key": "Shen 2016", "doi": "10.1007/s00204-015-1659-6",
               "title": "Factors affecting arsenic methylation in arsenic-exposed humans",
               "journal": "Archives of Toxicology", "year": "2016",
               "volume": "90", "issue": "", "pages": "2721-2733"}
    remote_s = _remote(
        title="Factors affecting arsenic methylation in arsenic-exposed humans",
        journal="International Journal of Environmental Research and Public Health",
        year="2016", volume="13", issue="11", pages="205")
    r = compare_record(local_s, remote_s)
    check("→ MISMATCH", r.status == STATUS_MISMATCH, r.status)
    fields = {d.field for d in r.diffs}
    check("检出 Journal 差异", "Journal" in fields, str(sorted(fields)))
    check("检出 Volume 差异", "Volume" in fields)
    check("检出 Pages 差异", "Pages" in fields)
    j = next(d for d in r.diffs if d.field == "Journal")
    check("Journal 给出正确目标", "Environmental Research" in j.remote, j.remote)

    print("\n[7] DOI_INVALID")
    r = compare_record({"key": "X 2020", "doi": "not-a-doi"}, _remote(title="x"))
    check("格式非法 → DOI_INVALID", r.status == STATUS_INVALID, r.status)
    r = compare_record({"key": "Y 2020", "doi": "10.1234/abc"}, {})
    check("Crossref 无返回 → DOI_INVALID", r.status == STATUS_INVALID, r.status)
    r = compare_record({"key": "Z 2020", "doi": "10.1234/abc"}, None)
    check("远端为 None → DOI_INVALID", r.status == STATUS_INVALID, r.status)

    print("\n[8] NO_DOI")
    for v in ("", "NO_DOI", None):
        r = compare_record({"key": "W 1998", "doi": v}, _remote(title="x"))
        check(f"doi={v!r} → NO_DOI", r.status == STATUS_NO_DOI, r.status)
    check("NO_DOI 提示人工核验", "人工核验" in compare_record(
        {"key": "W 1998", "doi": "NO_DOI"}, {}).message)

    print("\n[9] 缺字段不误报")
    r = compare_record({"key": "A 2020", "doi": "10.1234/abc",
                        "title": "Some title here", "year": "2020"},
                       _remote(title="Some title here", journal="J Test",
                               year="2020", volume="1", pages="1-2"))
    check("本地缺卷期页时不报差异", r.status == STATUS_VERIFIED,
          str([d.field for d in r.diffs]))
    r = compare_record({"key": "B 2020", "doi": "10.1234/abc",
                        "title": "Some title", "journal": "J Test",
                        "year": "2020", "volume": "2"},
                       _remote(title="Some title", journal="J Test",
                               year="2020", volume="1"))
    check("双方都有值且不同 → 报差异", r.status == STATUS_MISMATCH and
          any(d.field == "Volume" for d in r.diffs))

    print("\n[10] 报告渲染")
    results = [compare_record(dict(local), remote),
               compare_record(local_c, remote_c),
               compare_record(local_s, remote_s),
               compare_record({"key": "W 1998", "doi": "NO_DOI"}, {})]
    md = render_report(results, "refs.csv")
    check("含四状态计数", all(s in md for s in ("VERIFIED", "MISMATCH", "DOI_INVALID", "NO_DOI")))
    check("含字段级修改表", "应改为" in md)
    check("含人工核验节", "需人工核验" in md)
    check("含定位方法说明", "定位方法" in md and "Find Duplicates" in md)
    check("含全部记录表", "全部记录" in md)

    print("\n[11] 离线模式不联网")
    check("offline 返回 None", fetch_crossref("10.1136/bmj.n160", offline=True) is None)

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

def build_parser():
    p = argparse.ArgumentParser(
        prog="verify_dois.py",
        description="DOI / Crossref 核验与字段级修改清单（HBM-Meta-Agent / S6）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 全部 VERIFIED / 1 存在差异或待人工核验 / 2 前置条件不满足")
    p.add_argument("--input", type=Path, help="文献表（.csv，列：key,doi,title,journal,year,volume,issue,pages）")
    p.add_argument("--out", type=Path, help="清单输出（.md）")
    p.add_argument("--offline", action="store_true", help="不联网，仅做格式检查")
    p.add_argument("--limit", type=int, default=0, help="最多核验 N 条（0=全部）")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--version", action="version", version="hbm-meta S6 verify_dois 1.0.0")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()

    if not args.input:
        print("错误：需要 --input，或使用 --self-test", file=sys.stderr)
        return 2
    if not args.input.exists():
        print(f"错误：文献表不存在：{args.input}", file=sys.stderr)
        return 2

    with args.input.open(encoding="utf-8-sig", newline="") as fh:
        records = [dict(r) for r in csv.DictReader(fh)]
    if args.limit:
        records = records[:args.limit]

    results: list[VerifyResult] = []
    for i, rec in enumerate(records, start=1):
        doi = normalize_doi(rec.get("doi", ""))
        if args.offline or not is_valid_doi_format(doi):
            remote = None
        else:
            remote = fetch_crossref(doi)
            time.sleep(SLEEP_BETWEEN)
        results.append(compare_record(rec, remote))
        if i % 10 == 0:
            print(f"  已核验 {i}/{len(records)}")

    md = render_report(results, str(args.input))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"清单：{args.out}")
    else:
        print(md)

    n_bad = sum(1 for r in results
                if r.status in (STATUS_MISMATCH, STATUS_INVALID, STATUS_NO_DOI))
    print(f"记录={len(results)}  VERIFIED={sum(1 for r in results if r.status == STATUS_VERIFIED)}  "
          f"待处理={n_bad}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
