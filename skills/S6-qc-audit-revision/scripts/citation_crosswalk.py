#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
citation_crosswalk.py — 正文引用 ↔ 文献库对照

阶段状态：第三步骨架版
  ✅ 完整 CLI（--manuscript / --library / --out / --self-test）
  ✅ --self-test（引用提取 + 消歧 + 四类分类 + 机构作者 + 未使用记录）
  ✅ 核心逻辑：
       从稿件提取正文引用（作者-年份制，含 et al. / & / 中文顿号并列）
       归一化引用键（第一作者姓氏小写 + 年份 + 可选后缀 a/b）
       与文献库记录匹配 → MATCHED / MISSING / AMBIGUOUS / UNUSED
       机构作者识别（EFSA / NRC 等）
       输出对照表 + 参考文献插入表（含 EndNote 搜索词）
  ⏳ 第四步待办：编号制引用支持、.enl/.ris 解析、CI 集成

用法
  python citation_crosswalk.py --self-test
  python citation_crosswalk.py --manuscript 稿件.md --library 库.csv --out 对照表.md
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


# 机构作者白名单（缩写 → 全称）
INSTITUTIONAL = {
    "efsa": "European Food Safety Authority",
    "nrc": "National Research Council",
    "who": "World Health Organization",
    "iarc": "International Agency for Research on Cancer",
    "us epa": "United States Environmental Protection Agency",
    "usepa": "United States Environmental Protection Agency",
    "ich": "International Council for Harmonisation",
}

STATUS_MATCHED = "MATCHED"
STATUS_MISSING = "MISSING"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_UNUSED = "UNUSED"

# 正文引用：括号内的 作者-年份（支持 et al. / & / 并列 / 分号）
CITE_BLOCK_RE = re.compile(r"\(([^()]*?\d{4}[a-z]?[^()]*?)\)")
# 单个引用：姓氏 + 可选 et al / and / & + 年份
# 注意两处易错点（均经自核实测修正）：
#   ① 字符类必须**含空格**（`et al.` 中的 `et` 与姓氏之间的空格），否则整组失败；
#   ② authors 组不要用"非贪婪 + 嵌套可选组"，二者会互相干扰导致匹配失败。
#      改用顺序式：姓氏词 → 可选 `et al.` → 可选 `and/& 第二作者` → 年份。
_NAME_WORD = r"[A-Za-z\u4e00-\u9fff\-']+"
CITE_ITEM_RE = re.compile(
    r"(?P<authors>[A-Z\u4e00-\u9fff]" + _NAME_WORD +
    r"(?:\s+et\s+al\.?)?"
    r"(?:\s*(?:and|&)\s*[A-Z\u4e00-\u9fff]" + _NAME_WORD + r")?)"
    r"\s*,?\s*(?P<year>(?:19|20)\d{2})(?P<suffix>[a-z])?(?![0-9])")

# EndNote 搜索词生成：姓氏 + 年份
def endnote_search_term(surname: str, year: str) -> str:
    return f"{surname} {year}".strip()


# ===========================================================================
# 1. 数据结构
# ===========================================================================

@dataclass
class Citation:
    raw: str
    surname: str
    year: str
    suffix: str = ""
    institutional: str = ""

    @property
    def key(self) -> str:
        base = self.institutional or self.surname
        return f"{base.lower()}|{self.year}{self.suffix}"

    @property
    def display(self) -> str:
        name = self.surname or self.institutional
        return f"({name}, {self.year}{self.suffix})"


@dataclass
class LibraryEntry:
    record_id: str
    authors: str
    year: str
    title: str
    journal: str
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    is_institutional: bool = False

    @property
    def surname(self) -> str:
        if not self.authors:
            return ""
        first = re.split(r"[;；]", self.authors)[0].strip()
        m = re.search(r"[A-Za-z\u4e00-\u9fff\-']+", first)
        return m.group(0) if m else first[:20]

    @property
    def keys(self) -> list[str]:
        """可能的引用键（含机构作者）。"""
        out = [f"{self.surname.lower()}|{self.year}"]
        low = self.authors.lower()
        for abbr, full in INSTITUTIONAL.items():
            if abbr in low or full.lower() in low:
                out.append(f"{abbr}|{self.year}")
                out.append(f"{full.lower()}|{self.year}")
                out.append(f"{full.split()[0].lower()}|{self.year}")
        return out


# ===========================================================================
# 2. 提取正文引用
# ===========================================================================

def extract_citations(text: str) -> list[Citation]:
    """
    提取作者-年份制引用。
    支持：`(Smedley et al., 2002)`、`(Smith et al., 1998; Mandal and Suzuki, 2002)`、
          `(EFSA, 2009)`、`(Zhang et al., 2024a)`。
    """
    out: list[Citation] = []
    seen = set()
    for block in CITE_BLOCK_RE.finditer(text):
        content = block.group(1)
        # 按分号拆分并列引用
        for part in re.split(r"[;；]", content):
            for m in CITE_ITEM_RE.finditer(part):
                authors = m.group("authors").strip()
                year = m.group("year")
                suffix = m.group("suffix") or ""
                surname = re.split(r"\s+(?:et\s+al\.?|and|&)\s*", authors)[0].strip()
                surname = re.sub(r"[,&]$", "", surname).strip()
                if not surname:
                    continue
                inst = ""
                low = authors.lower()
                if low in INSTITUTIONAL:
                    inst = low
                elif surname.lower() in INSTITUTIONAL:
                    inst = surname.lower()
                key = f"{(inst or surname).lower()}|{year}{suffix}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(Citation(raw=m.group(0), surname=surname, year=year,
                                    suffix=suffix, institutional=inst))
    return out


# ===========================================================================
# 3. 后缀分配与匹配
# ===========================================================================

SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

# 后缀分配规则（可配置）：
#   'library_order' —— 按库中记录顺序（调用方应保证库已按 record_id 排序）
#   'record_id'     —— 按 record_id 字符串排序
#   'title'         —— 按标题字母序
# 三种规则均为**确定性**：同一输入两次运行必得同一分配。
SUFFIX_ASSIGN_RULE = "library_order"


def assign_suffixes(library: list["LibraryEntry"],
                    rule: str = SUFFIX_ASSIGN_RULE) -> dict[str, str]:
    """
    为同 (surname, year) 的多条记录分配 a/b/c… 后缀。
    返回 {record_id: suffix}（只包含需要后缀的记录）。

    稳定性保证：先按 rule 排序，再按序分配；因此同一输入结果恒定。
    """
    groups: dict[tuple, list[LibraryEntry]] = defaultdict(list)
    for e in library:
        if not e.surname or not e.year:
            continue
        groups[(e.surname.lower(), e.year)].append(e)

    out: dict[str, str] = {}
    for (surname, year), entries in groups.items():
        if len(entries) < 2:
            continue
        if rule == "record_id":
            ordered = sorted(entries, key=lambda x: x.record_id)
        elif rule == "title":
            ordered = sorted(entries, key=lambda x: (x.title or "").lower())
        else:  # library_order
            ordered = list(entries)
        if len(ordered) > len(SUFFIX_ALPHABET):
            # 超过 26 条同姓同年极罕见；超出部分不再分配后缀，交人工
            ordered = ordered[:len(SUFFIX_ALPHABET)]
        for i, e in enumerate(ordered):
            out[e.record_id] = SUFFIX_ALPHABET[i]
    return out


@dataclass
class CrosswalkItem:
    citation: Citation
    status: str
    matched: list[LibraryEntry] = field(default_factory=list)
    note: str = ""

    @property
    def search_term(self) -> str:
        name = self.citation.surname or self.citation.institutional.upper()
        return endnote_search_term(name, self.citation.year)


def crosswalk(citations: list[Citation], library: list[LibraryEntry],
              suffix_rule: str = SUFFIX_ASSIGN_RULE
              ) -> tuple[list[CrosswalkItem], list[LibraryEntry]]:
    """
    匹配正文引用与库记录。

    后缀规则（★ 消歧核心）：
      1) 先为同 (surname, year) 的库记录分配 a/b 后缀（assign_suffixes）
      2) 正文引用带后缀（如 `Zhang 2024a`）→ **精确命中**该后缀对应记录
      3) 正文引用无后缀但库中存在多条 → 标 `AMBIGUOUS`（需人工消歧）
      4) 正文引用无后缀且库中仅一条 → `MATCHED`
    """
    suffixes = assign_suffixes(library, suffix_rule)

    # 键索引：无后缀键 + 带后缀键
    index: dict[str, list[LibraryEntry]] = defaultdict(list)
    for e in library:
        base = f"{e.surname.lower()}|{e.year}"
        index[base].append(e)
        sfx = suffixes.get(e.record_id)
        if sfx:
            index[f"{base}{sfx}"].append(e)
        for k in e.keys:                      # 含机构作者键
            index[k].append(e)

    items: list[CrosswalkItem] = []
    used_ids: set[str] = set()
    for c in citations:
        hits: list[LibraryEntry]
        if c.suffix:
            # ① 后缀精确匹配优先
            hits = index.get(c.key, [])
            if not hits:
                # 正文有后缀但库中无对应后缀记录（可能库未消歧）→ 退到无后缀键并提示
                hits = index.get(f"{c.key[:-1]}", [])
                note = ("正文有后缀但库中未消歧 → 库需补 a/b 后缀"
                        if hits else "库中无对应记录 → 必须补建")
            else:
                note = ""
        else:
            hits = index.get(c.key, [])
            note = ""

        # 去重（同一记录可能命中多个键）
        uniq: list[LibraryEntry] = []
        for h in hits:
            if h.record_id not in {u.record_id for u in uniq}:
                uniq.append(h)

        if not uniq:
            items.append(CrosswalkItem(
                c, STATUS_MISSING, [],
                note or "库中无对应记录 → 必须补建"))
        elif len(uniq) == 1:
            used_ids.add(uniq[0].record_id)
            items.append(CrosswalkItem(c, STATUS_MATCHED, uniq, note))
        else:
            for u in uniq:
                used_ids.add(u.record_id)
            items.append(CrosswalkItem(
                c, STATUS_AMBIGUOUS, uniq,
                note or f"库中同姓同年 {len(uniq)} 条且正文未加后缀 → "
                        f"请按库中顺序加 a/b 后缀消歧"))
    unused = [e for e in library if e.record_id not in used_ids]
    return items, unused


# ===========================================================================
# 4. 报告
# ===========================================================================

def render_report(items: list[CrosswalkItem], unused: list[LibraryEntry],
                  library: list[LibraryEntry]) -> str:
    counts = {s: sum(1 for i in items if i.status == s)
              for s in (STATUS_MATCHED, STATUS_MISSING, STATUS_AMBIGUOUS)}
    lines = [
        "# 正文引用 ↔ 文献库记录对照表",
        "",
        f"- 正文唯一引用：{len(items)}",
        f"- 库中记录：{len(library)}",
        f"- **MATCHED** {counts[STATUS_MATCHED]}　**MISSING** {counts[STATUS_MISSING]}　"
        f"**AMBIGUOUS** {counts[STATUS_AMBIGUOUS]}　**UNUSED** {len(unused)}",
        "",
        "## 对照表",
        "",
        "| # | 正文引用 | 状态 | 库中记录（作者 / 标题 / 期刊 / DOI） | EndNote 搜索词 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for i, it in enumerate(items, start=1):
        if it.matched:
            rec = "<br>".join(
                f"{e.authors[:40]} / {e.title[:48]} / {e.journal[:24]} / {e.doi}"
                for e in it.matched)
        else:
            rec = "—"
        lines.append(f"| {i} | {it.citation.display} | **{it.status}** | {rec} | "
                     f"`{it.search_term}` | {it.note} |")

    lines += ["", "## 库中有但正文未引用（UNUSED）", ""]
    if not unused:
        lines.append("无。")
    else:
        lines.append("> 这些记录会导致参考文献表多出条目；应删除或确认是否为遗漏引用。")
        lines.append("")
        lines += ["| # | 作者 | 年份 | 标题 | DOI |", "|---|---|---|---|---|"]
        for i, e in enumerate(unused, start=1):
            lines.append(f"| {i} | {e.authors[:40]} | {e.year} | {e.title[:60]} | {e.doi} |")

    lines += ["", "## 参考文献插入表（含完整信息）", "",
              "| # | 正文引用 | 搜索词 | 作者 | 年份 | 标题 | 期刊 | 卷 | 期 | 页 | DOI |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, it in enumerate(items, start=1):
        for j, e in enumerate(it.matched or [None]):
            if e is None:
                lines.append(f"| {i} | {it.citation.display} | `{it.search_term}` | "
                             f"— | {it.citation.year} | **待补建** | | | | | |")
            else:
                lines.append(f"| {i} | {it.citation.display} | `{it.search_term}` | "
                             f"{e.authors} | {e.year} | {e.title} | {e.journal} | "
                             f"{e.volume} | {e.issue} | {e.pages} | {e.doi} |")

    lines += ["", "---", "",
              "> AI 只产出对照与差异；补建/消歧/删除由人工确认"
              "（见 `references/citation_verification_protocol.md` §7）。", ""]
    return "\n".join(lines)


# ===========================================================================
# 5. self-test
# ===========================================================================

MANUSCRIPT = """
Arsenic is widely distributed in groundwater (Smedley et al., 2002; Fendorf et al., 2010),
soil (Smith et al., 1998), and the food chain (Zhao et al., 2010).
The WHO established a guideline (WHO, 2011) and EFSA assessed dietary exposure (EFSA, 2009).
Two studies reported cadmium (Zhang et al., 2024a) and soil arsenic (Zhang et al., 2024b).
Arsenic exposure is associated with skin lesions (Karagas et al., 2015; Moon et al., 2017).
"""

LIBRARY = [
    {"record_id": "L01", "authors": "Smedley, P. L.; Kinniburgh, D. G.", "year": "2002",
     "title": "A review of the source, behaviour and distribution of arsenic",
     "journal": "Appl Geochem", "volume": "17", "issue": "5", "pages": "517-568",
     "doi": "10.1016/S0883-2927(02)00018-5"},
    {"record_id": "L02", "authors": "Fendorf, S.; Michael, H. A.; van Geen, A.", "year": "2010",
     "title": "Spatial and temporal variations of groundwater arsenic", "journal": "Science",
     "volume": "328", "issue": "5982", "pages": "1123-1127", "doi": "10.1126/science.1172974"},
    {"record_id": "L03", "authors": "Smith, E.; Naidu, R.; Alston, A. M.", "year": "1998",
     "title": "Arsenic in the soil environment: a review", "journal": "Adv Agron",
     "volume": "64", "issue": "", "pages": "149-195", "doi": ""},
    {"record_id": "L04", "authors": "Zhao, F. J.; McGrath, S. P.; Meharg, A. A.", "year": "2010",
     "title": "Arsenic as a food chain contaminant", "journal": "Annu Rev Plant Biol",
     "volume": "61", "issue": "", "pages": "535-559", "doi": "10.1146/annurev-arplant-042809-112152"},
    {"record_id": "L05", "authors": "Efsa Panel on Contaminants in the Food Chain", "year": "2009",
     "title": "Scientific opinion on arsenic in food", "journal": "EFSA J",
     "volume": "7", "issue": "10", "pages": "1351", "doi": "10.2903/j.efsa.2009.1351",
     "is_institutional": True},
    {"record_id": "L06", "authors": "Karagas, M. R.; Gossai, A.; Pierce, B.", "year": "2015",
     "title": "Drinking water arsenic contamination, skin lesions and malignancies",
     "journal": "Curr Environ Health Rep", "volume": "2", "issue": "1", "pages": "52-68",
     "doi": "10.1007/s40572-014-0040-x"},
    {"record_id": "L07", "authors": "Moon, K. A.; Oberoi, S.; Barchowsky, A.", "year": "2017",
     "title": "A dose-response meta-analysis of chronic arsenic exposure",
     "journal": "Int J Epidemiol", "volume": "46", "issue": "6", "pages": "1924-1939",
     "doi": "10.1093/ije/dyx202"},
    # 同姓同年两篇 → 正文引用歧义测试
    {"record_id": "L08", "authors": "Zhang, S.; Zhang, J.", "year": "2024",
     "title": "Escalating arsenic contamination throughout Chinese soils", "journal": "Nat Sustain",
     "volume": "7", "issue": "6", "pages": "766-775", "doi": "10.1038/s41893-024-01341-7"},
    {"record_id": "L09", "authors": "Zhang, X.; Bold, T.", "year": "2024",
     "title": "Spatio-temporal distribution of cadmium levels in Chinese population",
     "journal": "Heliyon", "volume": "10", "issue": "7", "pages": "e28879",
     "doi": "10.1016/j.heliyon.2024.e28879"},
    # 库中有但正文未引用
    {"record_id": "L10", "authors": "Hadei, M.; Shahsavani, A.", "year": "2021",
     "title": "A systematic review and meta-analysis of human biomonitoring studies",
     "journal": "Ecotoxicol Environ Saf", "volume": "212", "issue": "", "pages": "111986",
     "doi": "10.1016/j.ecoenv.2021.111986"},
]


def _to_lib(d: dict) -> LibraryEntry:
    return LibraryEntry(record_id=d["record_id"], authors=d["authors"], year=d["year"],
                        title=d["title"], journal=d["journal"], volume=d.get("volume", ""),
                        issue=d.get("issue", ""), pages=d.get("pages", ""),
                        doi=d.get("doi", ""), is_institutional=d.get("is_institutional", False))


def run_self_test() -> int:
    print("=" * 70)
    print("citation_crosswalk.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    library = [_to_lib(d) for d in LIBRARY]

    print("\n[1] 正文引用提取")
    cites = extract_citations(MANUSCRIPT)
    keys = [c.key for c in cites]
    check("提取到 10 条唯一引用（Smedley/Fendorf/Smith/Zhao/WHO/EFSA/Zhang×2/Karagas/Moon）",
          len(cites) == 10, str(len(cites)))
    check("Smedley 2002", "smedley|2002" in keys)
    check("Fendorf 2010", "fendorf|2010" in keys)
    check("Smith 1998", "smith|1998" in keys)
    check("Zhao 2010", "zhao|2010" in keys)
    check("EFSA 2009（机构）", "efsa|2009" in keys, str(keys))
    check("WHO 2011", any("who" in k for k in keys))
    check("Zhang 2024a 带后缀", "zhang|2024a" in keys, str(keys))
    check("Zhang 2024b 带后缀", "zhang|2024b" in keys)
    check("Karagas 2015 / Moon 2017", "karagas|2015" in keys and "moon|2017" in keys)
    check("分号并列引用被拆分",
          sum(1 for c in cites if c.year == "2002") == 1)

    print("\n[2] 库记录键生成")
    smedley = library[0]
    check("姓氏提取", smedley.surname == "Smedley", smedley.surname)
    efsa = next(e for e in library if e.record_id == "L05")
    check("机构作者生成多个键",
          any(k.startswith("efsa|") for k in efsa.keys), str(efsa.keys))
    check("机构作者也可用全称首词匹配",
          any(k.startswith("efsa") for k in efsa.keys) or
          any("european" in k for k in efsa.keys), str(efsa.keys))

    print("\n[3] 匹配分类")
    items, unused = crosswalk(cites, library)
    by_year_name = {(i.citation.surname.lower(), i.citation.year): i for i in items}
    check("Smedley 2002 → MATCHED",
          by_year_name[("smedley", "2002")].status == STATUS_MATCHED,
          by_year_name[("smedley", "2002")].status)
    check("EFSA 2009 → MATCHED（机构作者）",
          by_year_name[("efsa", "2009")].status == STATUS_MATCHED,
          by_year_name[("efsa", "2009")].status)
    check("WHO 2011 → MISSING（库中无）",
          any(i.citation.surname == "WHO" and i.status == STATUS_MISSING for i in items),
          str([(i.citation.display, i.status) for i in items if i.citation.surname == "WHO"]))
    check("MISSING 项提示补建",
          all("补建" in i.note for i in items if i.status == STATUS_MISSING))

    print("\n[4] ★ 后缀消歧（新增：精确命中，不再误报 AMBIGUOUS）")
    # 库中 L08/L09 同姓同年 → 应按顺序分配 2024a / 2024b
    sfx_map = assign_suffixes(library)
    check("L08 分配后缀 a", sfx_map.get("L08") == "a", str(sfx_map))
    check("L09 分配后缀 b", sfx_map.get("L09") == "b", str(sfx_map))
    check("记录顺序稳定时后缀稳定",
          assign_suffixes(library) == sfx_map)
    # 顺序反转 → 后缀随之改变（证明依赖顺序，因此调用方必须排序）
    sfx_rev = assign_suffixes(list(reversed(library)))
    check("顺序反转后后缀互换（说明必须排序）",
          sfx_rev.get("L08") == "b" and sfx_rev.get("L09") == "a", str(sfx_rev))
    check("按 record_id 规则与库顺序一致（库已排序）",
          assign_suffixes(library, "record_id") == sfx_map)
    check("按标题字母序规则可切换",
          assign_suffixes(library, "title").get("L08") is not None)
    check("单一记录不分配后缀",
          all(not v for k, v in assign_suffixes(
              [e for e in library if e.record_id == "L01"]).items()))

    z_items = [i for i in items if i.citation.surname == "Zhang"]
    check("Zhang 有 2 条引用", len(z_items) == 2, str(len(z_items)))
    z_ok = all(i.status == STATUS_MATCHED for i in z_items)
    check("★ 带后缀的引用精确命中 → MATCHED（不再 AMBIGUOUS）", z_ok,
          str([(i.citation.display, i.status) for i in z_items]))
    a_item = next((i for i in z_items if i.citation.suffix == "a"), None)
    b_item = next((i for i in z_items if i.citation.suffix == "b"), None)
    check("2024a → L08（土壤砷）",
          a_item and len(a_item.matched) == 1 and a_item.matched[0].record_id == "L08",
          a_item.matched[0].record_id if a_item and a_item.matched else "None")
    check("2024b → L09（镉时空分布）",
          b_item and len(b_item.matched) == 1 and b_item.matched[0].record_id == "L09",
          b_item.matched[0].record_id if b_item and b_item.matched else "None")
    check("命中的标题正确（非交叉命中）",
          a_item and "Chinese soils" in a_item.matched[0].title
          and b_item and "cadmium" in b_item.matched[0].title)

    # 无后缀时仍应标 AMBIGUOUS
    no_sfx = extract_citations("(Zhang et al., 2024)")
    items_ns, _ = crosswalk(no_sfx, library)
    check("★ 无后缀引用 → AMBIGUOUS（需人工消歧）",
          len(items_ns) == 1 and items_ns[0].status == STATUS_AMBIGUOUS,
          items_ns[0].status if items_ns else "无")
    check("AMBIGUOUS 提示加后缀",
          "后缀" in items_ns[0].note, items_ns[0].note)

    # 库未消歧（正文有后缀但库中无后缀记录）→ 退到无后缀键并提示
    lib_no_sfx = [e for e in library if e.record_id != "L09"]
    items_lib, _ = crosswalk([next(i.citation for i in z_items
                                   if i.citation.suffix == "a")], lib_no_sfx)
    check("库缺同姓同年另一条时仍可命中",
          items_lib[0].status in (STATUS_MATCHED, STATUS_AMBIGUOUS), items_lib[0].status)

    print("\n[5] 未使用记录")
    check("识别 UNUSED（Hadei 2021）",
          any(e.record_id == "L10" for e in unused), str([e.record_id for e in unused]))
    check("被引用记录不在 UNUSED",
          not any(e.record_id in ("L01", "L02", "L05") for e in unused))

    print("\n[6] EndNote 搜索词")
    sm = next(i for i in items if i.citation.surname == "Smedley")
    check("搜索词 = 'Smedley 2002'", sm.search_term == "Smedley 2002", sm.search_term)
    e = next(i for i in items if i.citation.surname == "EFSA")
    check("机构搜索词使用原写法", e.search_term.startswith("EFSA"), e.search_term)

    print("\n[7] 报告渲染")
    md = render_report(items, unused, library)
    check("含四状态计数", all(s in md for s in ("MATCHED", "MISSING", "AMBIGUOUS", "UNUSED")))
    check("含对照表", "对照表" in md)
    check("含 UNUSED 节", "库中有但正文未引用" in md)
    check("含插入表（完整字段）",
          "参考文献插入表" in md and "搜索词" in md)
    check("MISSING 在插入表标为待补建", "待补建" in md)

    print("\n[8] 边界情形")
    check("空文本 → 无引用", extract_citations("") == [])
    check("无年份括号不误提取", extract_citations("(see Figure 1)") == [])
    check("编号制引用不被误提取", extract_citations("As reported [12, 13].") == [])
    check("et al. 姓氏被截断", extract_citations("(Wang et al., 2020)")[0].surname == "Wang")
    check("& 并列被处理", extract_citations("(Li & Chen, 2019)")[0].surname == "Li")
    check("and 并列被处理", extract_citations("(Li and Chen, 2019)")[0].surname == "Li")

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
        prog="citation_crosswalk.py",
        description="正文引用 ↔ 文献库对照（HBM-Meta-Agent / S6）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 全部 MATCHED / 1 存在 MISSING/AMBIGUOUS/UNUSED / 2 前置条件不满足")
    p.add_argument("--manuscript", type=Path, help="稿件文件（.md / .txt）")
    p.add_argument("--library", type=Path,
                   help="文献库导出（.csv，列：record_id,authors,year,title,journal,volume,issue,pages,doi）")
    p.add_argument("--out", type=Path, help="对照表输出（.md）")
    p.add_argument("--suffix-rule", default=SUFFIX_ASSIGN_RULE,
                   choices=["library_order", "record_id", "title"],
                   help="同姓同年记录的后缀分配规则（默认 library_order，须先按 record_id 排序）")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--version", action="version", version="hbm-meta S6 citation_crosswalk 1.0.0")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()

    if not args.manuscript or not args.library:
        print("错误：需要 --manuscript 与 --library，或使用 --self-test", file=sys.stderr)
        return 2
    for pth in (args.manuscript, args.library):
        if not pth.exists():
            print(f"错误：文件不存在：{pth}", file=sys.stderr)
            return 2

    cites = extract_citations(args.manuscript.read_text(encoding="utf-8"))
    with args.library.open(encoding="utf-8-sig", newline="") as fh:
        library = [_to_lib({k: (v or "") for k, v in r.items()})
                   for r in csv.DictReader(fh)]
    # record_id 缺省时用行号补
    for i, e in enumerate(library, start=1):
        if not e.record_id:
            e.record_id = f"L{i:03d}"
    # ★ 排序保证后缀分配稳定（assign_suffixes 的 library_order 规则依赖顺序）
    library.sort(key=lambda e: e.record_id)

    items, unused = crosswalk(cites, library, suffix_rule=args.suffix_rule)
    md = render_report(items, unused, library)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"对照表：{args.out}")
    else:
        print(md)

    n_bad = sum(1 for i in items if i.status != STATUS_MATCHED) + len(unused)
    print(f"正文引用={len(items)}  库记录={len(library)}  待处理={n_bad}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
