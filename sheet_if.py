"""공유 구글 시트(「저널 IF 공유 데이터 | S2Journal」)에서 저널 IF를 읽는다 — 2026-11-16.

S2Journal JCR 랭킹 전체(약 2만 2천 저널)를 사용자가 시트로 정리해 '링크가 있는 모든 사용자 · 뷰어'로 열어 뒀다.
`/export?format=csv`로 키 없이 받는다. 매일 수집할 때 새로 읽으므로 시트를 고치면 다음 실행에 반영된다.

- 맞추기: **ISSN 먼저**, 없으면 저널명 정규화(newsroom_if.norm_title)로. 시트에는 ISSN이 한 칸뿐이고
  논문에는 인쇄판·전자판·연결 ISSN이 다 있어서 대부분 ISSN으로 맞는다.
- 같은 ISSN이 여러 해 있으면 **가장 최근 연도**.
- `상위` = 분야별 `IF%` 중 가장 작은 값(가장 좋은 분야 순위). `IF%`가 `-`이면 없음.
- **시트 전체를 저장소에 올리지 않는다.** JCR은 Clarivate 데이터라, 공개 저장소에는 우리 논문 저널에 실제로
  쓴 것만 data/sheet_if_used.json에 남긴다(시트를 못 읽은 날 이 값으로 버틴다).
"""
import csv
import io
import json
import os
import re
from pathlib import Path

import requests

from newsroom_if import norm_title

SHEET_ID = "1_o1z93ONKxL3nWzSMmz9jAmv6FfKp6T-SbOMzWIi2mo"
URL = os.environ.get("JIF_SHEET_URL", "").strip() or \
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
USED_PATH = Path("data/sheet_if_used.json")
ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dXx]$")
PCT_RE = re.compile(r"IF%\s*([\d.]+)")


def _num(s):
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def fetch():
    """시트 → (ISSN → 기록, 저널명 → 기록). 기록 = {저널, IF, 상위, 사분위, 분야, 연도}."""
    r = requests.get(URL, timeout=90, headers={"User-Agent": "amc-research-radar/1.0"})
    r.raise_for_status()
    rows = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))
    by_issn, by_title = {}, {}
    for x in rows:
        jif = _num(x.get("IF numeric") or x.get("IF (source display)"))
        if jif is None:
            continue
        pcts = [float(p) for p in PCT_RE.findall(x.get("Category quartiles") or "")]
        rec = {"저널": (x.get("Journal") or "").strip(), "IF": jif,
               "상위": min(pcts) if pcts else None, "사분위": (x.get("Best quartile") or "").strip(),
               "분야": (x.get("Categories") or "").strip(), "연도": int(_num(x.get("Year (S2Journal label)")) or 0)}
        issn = (x.get("ISSN") or "").strip().upper()
        if ISSN_RE.match(issn):
            cur = by_issn.get(issn)
            if cur is None or rec["연도"] > cur["연도"]:
                by_issn[issn] = rec
        t = norm_title(rec["저널"])
        if t:
            cur = by_title.get(t)
            if cur is None or rec["연도"] > cur["연도"]:
                by_title[t] = rec
    return by_issn, by_title


def lookup(paper, by_issn, by_title):
    recs = [by_issn[s.upper()] for s in paper.get("issn", []) if s.upper() in by_issn]
    if recs:
        return max(recs, key=lambda r: (r["연도"], r["IF"]))
    return by_title.get(norm_title(paper.get("저널", "")))


def load_used():
    try:
        return json.loads(USED_PATH.read_text(encoding="utf-8")).get("저널", {})
    except Exception:
        return {}


def save_used(used, total):
    USED_PATH.parent.mkdir(exist_ok=True)
    USED_PATH.write_text(json.dumps({"출처": "공유 구글 시트 「저널 IF 공유 데이터 | S2Journal」",
                                     "시트저널수": total, "저널": used},
                                    ensure_ascii=False, indent=0, sort_keys=True),
                         encoding="utf-8", newline="\n")
