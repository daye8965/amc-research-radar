"""서울아산병원 뉴스룸 보도자료에 적힌 저널 피인용지수(IF)를 모은다.

우리 보도자료는 거의 늘 `‘국제외과학회지(International Journal of Surgery, 피인용지수 10.1)’` 모양으로
저널 영문명과 IF를 적는다. S2Journal에서 받은 JCR 목록(docs/journal_if.json)은 화면을 인쇄한 것이라
수백 개 저널뿐이어서, 거기 없는 저널을 이 값으로 채운다(2026-11-06 — Int J Surg가 IF 없이 OpenAlex 4.2로
매겨져 등급이 비었다).

- 목록: news.amc.seoul.kr/news/con/list.do?pressChk=Y (보도자료). robots.txt가 막지 않는다. 1초에 1장씩.
- 이미 읽은 보도자료(cntId)는 data/newsroom_if.json에 남겨 다시 읽지 않는다. 매일은 첫 몇 장만 본다.
- `5년 피인용지수`는 뺀다(2년 IF와 축척이 다르다). 저널 하나에 값이 여럿이면 **가장 최근 보도자료**의 값을 쓴다.
- 맞추기는 저널 영문명을 정규화해 PubMed 저널명과 **글자 그대로** 비교한다. 약칭(`IBD`)은 안 맞고 그냥 버린다.
"""
import html
import json
import re
import time
from pathlib import Path

import requests

PATH = Path("data/newsroom_if.json")
BASE = "https://news.amc.seoul.kr/news/con/"
UA = {"User-Agent": "amc-research-radar/1.0 (AMC PR team; daily)"}
FROM = "2025-01-01"          # IF는 해마다 바뀐다 — 이보다 오래된 보도자료는 읽지 않는다
MAX_PAGES = 120

# ‘한글명(English Name, 피인용지수 10.1)’ — 한글명 없이 (English, 피인용지수) 만 있는 것도 받는다
IF_RE = re.compile(r"[(（]\s*([A-Za-z][^,，()（）]{1,120}?)\s*[,，]\s*(5년\s*)?피인용\s*지수\s*(?:는|:)?\s*(\d{1,3}(?:\.\d{1,3})?)\s*[)）]")
DATE_RE = re.compile(r"(20\d\d)\.(\d\d)\.(\d\d)")


def _text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


def norm_title(s):
    """저널명 비교용 — 괄호 속 발행지·대소문자·&/and·문장부호 차이를 지운다."""
    s = re.sub(r"\(.*?\)", " ", s or "")
    s = s.replace("&", " and ").lower()
    s = re.sub(r"^the\s+", "", s.strip())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def extract(text):
    out = []
    for name, five, v in IF_RE.findall(text):
        if five:
            continue
        name = name.strip()
        try:
            val = float(v)
        except ValueError:
            continue
        if 0 < val < 300 and len(norm_title(name)) >= 4:
            out.append({"저널": name, "IF": val})
    return out


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"보도자료": {}}


def update(log=print, max_new=400):
    """새 보도자료만 읽어 캐시를 늘린다. 실패해도 지난 캐시를 돌려준다."""
    db = load()
    seen = db.setdefault("보도자료", {})
    new = 0
    try:
        for pg in range(1, MAX_PAGES + 1):
            h = requests.get(BASE + "list.do", params={"pressChk": "Y", "pageIndex": pg}, headers=UA, timeout=30).text
            ids = list(dict.fromkeys(re.findall(r"cntId=(\d+)", h)))
            if not ids:
                break
            todo = [i for i in ids if i not in seen]
            oldest = None
            for cid in todo:
                t = _text(requests.get(BASE + "detail.do", params={"cntId": cid}, headers=UA, timeout=30).text)
                m = DATE_RE.search(t)
                day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
                seen[cid] = {"날짜": day, "IF": extract(t)}
                oldest = min(oldest or day, day) if day else oldest
                new += 1
                time.sleep(1)
                if new >= max_new:
                    break
            time.sleep(1)
            if new >= max_new:
                break
            # 한 장이 전부 이미 읽은 것이면 그 뒤도 다 읽은 것이다 (처음 채울 때만 끝까지 내려간다)
            if not todo:
                dates = [seen[i]["날짜"] for i in ids if seen.get(i, {}).get("날짜")]
                if dates and min(dates) < FROM or db.get("다채움"):
                    break
            if oldest and oldest < FROM:
                db["다채움"] = True
                break
    except Exception as e:
        log(f"  뉴스룸 IF 읽기 실패 — {type(e).__name__}: {e}")
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(db, ensure_ascii=False, indent=0), encoding="utf-8", newline="\n")
    return db, new


def table(db):
    """정규화한 저널명 → {저널, IF, 날짜, cntId} (가장 최근 보도자료 값)."""
    out = {}
    for cid, r in db.get("보도자료", {}).items():
        if (r.get("날짜") or "") < FROM:
            continue
        for x in r.get("IF", []):
            k = norm_title(x["저널"])
            cur = out.get(k)
            if cur is None or (r["날짜"], int(cid)) > (cur["날짜"], int(cur["cntId"])):
                out[k] = {"저널": x["저널"], "IF": x["IF"], "날짜": r["날짜"], "cntId": cid}
    return out


def lookup(paper, tab):
    """논문 저널명으로 뉴스룸 IF를 찾는다. 없으면 None."""
    return tab.get(norm_title(paper.get("저널", "")))
