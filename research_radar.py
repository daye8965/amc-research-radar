"""연구성과 레이더 — 서울아산병원 소속 저자의 신규 논문을 PubMed에서 모아 보도자료 후보를 고른다.

결과는 docs/research.json 하나. 같은 저장소의 docs/index.html(GitHub Pages)이 읽는다.

- 수집: PubMed E-utilities. 소속(affiliation)에 서울아산병원이 들어간 논문을
  PubMed 등록일(edat) 기준 최근 RESEARCH_DAYS일만큼 다시 훑는다(이미 있는 PMID는 덮어씀).
- 저널 지표: OpenAlex의 2yr_mean_citedness(최근 2년 논문의 평균 피인용 — IF와 같은 정의의 무료 대용치).
  Clarivate IF는 무료 API가 없다. 값이 IF보다 작게 나오는 경향이 있어(Lancet IF 88 → 21.6)
  문턱을 IF 숫자로 생각하면 안 된다.
- 보도자료 표시: 구글 시트 '연구성과' 탭(PMID · 상태 · 배포일 · 메모)을 서비스 계정으로 읽는다.
  시트는 RESEARCH_SHEET_ID, 없으면 REPORTER_SHEET_ID(언론 대시보드와 같은 시트).
  **저장소가 공개라 표시를 저장소 파일에 두지 않는다.** 배포 전 '결정'은 화면에 '검토중'으로만 나가고,
  배포일이 지나야 '배포'로 바뀐다. 메모는 내보내지 않는다.

환경변수: RESEARCH_DAYS(기본 10) · RESEARCH_KEEP_DAYS(기본 365) · NCBI_API_KEY · OPENALEX_API_KEY (모두 선택)
          GOOGLE_SERVICE_ACCOUNT_JSON · RESEARCH_SHEET_ID(또는 REPORTER_SHEET_ID) · RESEARCH_SHEET_RANGE
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
OUT = Path("docs/research.json")

DAYS      = int(os.environ.get("RESEARCH_DAYS", "10") or 10)
KEEP_DAYS = int(os.environ.get("RESEARCH_KEEP_DAYS", "365") or 365)
NCBI_KEY  = os.environ.get("NCBI_API_KEY", "").strip()
OA_KEY    = os.environ.get("OPENALEX_API_KEY", "").strip()
UA        = {"User-Agent": "amc-research-radar/1.0"}

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
# 'University of Ulsan College of Medicine'만 적고 서울아산병원을 안 쓰는 저자가 있다(울산의대 = 서울아산병원 교수).
# 다만 울산대병원(울산)·강릉아산병원도 같은 의대 소속이라, 저자 판정(is_amc)에서 'Seoul'이 함께 있을 때만 인정한다
PUBMED_TERM = ('("Asan Medical Center"[ad] OR "Asan Institute for Life Sciences"[ad] '
               'OR "University of Ulsan College of Medicine"[ad])')

# 연구 논문이 아닌 것 — 수집은 하되 후보에서 뺀다
NON_RESEARCH = {"Comment", "Editorial", "Letter", "Erratum", "Published Erratum", "News",
                "Retraction Notice", "Retraction of Publication", "Expression of Concern",
                "Biography", "Interview", "Congress", "Preprint"}

# 지표와 상관없이 최상위로 보는 저널(ISO 약어). OpenAlex 값이 잡히지 않아도 놓치지 않게
TOP_S = {"N Engl J Med", "Lancet", "JAMA", "BMJ", "Nature", "Science", "Cell", "Nat Med"}
# 이 접두어로 시작하는 저널 가족은 최소 A — 'Nat '처럼 넓은 접두어는 넣지 않는다(Nat Prod Res 등이 딸려 온다)
TOP_A_PREFIX = ("Lancet ", "JAMA ", "Nat Rev ", "Cancer Cell", "Cell Metab", "Cell Stem Cell",
                "Nat Biotechnol", "Nat Genet", "Nat Cancer", "Nat Biomed Eng", "Sci Transl Med")
TOP_A = {"Circulation", "Eur Heart J", "J Clin Oncol", "Ann Oncol", "Gastroenterology", "Gut",
         "J Hepatol", "J Am Coll Cardiol", "Ann Intern Med", "Blood", "Immunity", "Cancer Discov",
         "Hepatology", "Nat Commun", "Am J Respir Crit Care Med", "Eur Urol", "Ann Rheum Dis",
         "Diabetes Care", "Radiology", "Mol Cancer", "J Clin Invest", "Neuron"}
# OpenAlex 2yr_mean_citedness 문턱. IF와 축척이 달라 실측으로 맞춘 값(파일 아래 주석 참조)
TIER_A_CITED = 10.0
TIER_B_CITED = 6.0


def log(*a):
    print(*a, flush=True)


try:                                              # 윈도 콘솔(cp949)에서 '—' 때문에 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── PubMed ─────────────────────────────────────────────────────────────────
def eutils(path, **params):
    if NCBI_KEY:
        params["api_key"] = NCBI_KEY
    for attempt in range(5):
        r = requests.get(EUTILS + path, params=params, headers=UA, timeout=60)
        if r.status_code == 429:                 # 키 없으면 초당 3건
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.12 if NCBI_KEY else 0.4)
        return r
    r.raise_for_status()


def search_pmids(start, end):
    term = f"{PUBMED_TERM} AND {start:%Y/%m/%d}:{end:%Y/%m/%d}[edat]"
    ids, retstart = [], 0
    while True:
        js = eutils("esearch.fcgi", db="pubmed", retmode="json", retmax=500,
                    retstart=retstart, term=term).json()["esearchresult"]
        ids += js.get("idlist", [])
        retstart += 500
        if retstart >= int(js.get("count", 0)):
            return ids


def text_of(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def is_amc(aff):
    a = aff.lower()
    if "asan medical center" in a or "asan institute for life sciences" in a or "asan medical centre" in a:
        return "gangneung" not in a or "seoul" in a
    return "ulsan college of medicine" in a and "seoul" in a


DEPT_RE = re.compile(r"(?:Department|Division|Center|Centre) (?:of|for) ([A-Z][A-Za-z ,&\-]+?)(?:,|;|$)")


def dept_of(aff):
    m = DEPT_RE.search(aff)
    # 'Neurology Asan Medical Center'처럼 쉼표 없이 병원 이름이 붙은 것을 뗀다
    return re.sub(r"\s+(?:Asan|University|Seoul).*$", "", m.group(1).strip()) if m else ""


MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def pub_date(art):
    """전자 출판일(ArticleDate)이 있으면 그것, 없으면 호(issue) 날짜."""
    ad = art.find("Article/ArticleDate")
    if ad is not None and ad.findtext("Year"):
        return f"{ad.findtext('Year')}-{int(ad.findtext('Month') or 1):02d}-{int(ad.findtext('Day') or 1):02d}"
    pd = art.find("Article/Journal/JournalIssue/PubDate")
    if pd is None:
        return ""
    y = pd.findtext("Year") or (pd.findtext("MedlineDate") or "")[:4]
    m = pd.findtext("Month") or "1"
    m = MON.get(m[:3], int(m) if m.isdigit() else 1)
    d = pd.findtext("Day") or "1"
    return f"{y}-{m:02d}-{int(d) if d.isdigit() else 1:02d}" if y.isdigit() else ""


def conclusion_of(art):
    """초록의 결론 단락(라벨이 있으면) 또는 마지막 두 문장. 보도 가치를 판단하는 데 쓰라고 300자까지만."""
    parts = art.findall("Article/Abstract/AbstractText")
    if not parts:
        return ""
    for p in parts:
        lab = (p.get("Label") or p.get("NlmCategory") or "").upper()
        if lab.startswith("CONCLUSION") or lab == "INTERPRETATION":
            return text_of(p)[:300]
    sents = re.split(r"(?<=[.!?])\s+", text_of(parts[-1]))
    return " ".join(sents[-2:])[:300]


def parse_article(pa):
    mc = pa.find("MedlineCitation")
    art = mc
    pmid = mc.findtext("PMID")
    journal = mc.find("Article/Journal")
    issns = [i.text.strip() for i in journal.findall("ISSN") if i.text]
    issn_l = mc.findtext("MedlineJournalInfo/ISSNLinking")
    if issn_l:
        issns.append(issn_l.strip())
    doi = ""
    for aid in pa.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = (aid.text or "").strip()

    authors = mc.findall("Article/AuthorList/Author")
    n = len(authors)
    amc, first, last, corr, depts = [], False, False, [], []
    for i, au in enumerate(authors):
        name = " ".join(x for x in [au.findtext("ForeName"), au.findtext("LastName")] if x) \
            or au.findtext("CollectiveName") or ""
        affs = [text_of(a) for a in au.findall("AffiliationInfo/Affiliation")]
        mine = [a for a in affs if is_amc(a)]
        if not mine:
            continue
        amc.append(name)
        if i == 0:
            first = True
        if i == n - 1 and n > 1:
            last = True
        # PubMed는 교신저자를 따로 표시하지 않는다. 소속 칸에 이메일을 적은 저자가 대개 교신저자다
        if any("@" in a for a in affs):
            corr.append(name)
        for a in mine:
            d = dept_of(a)
            if d and d not in depts:
                depts.append(d)
    if not amc:
        return None                               # 울산대병원·강릉아산병원만 있는 논문

    types = [text_of(t) for t in mc.findall("Article/PublicationTypeList/PublicationType")]
    edat = ""
    for d in pa.findall("PubmedData/History/PubMedPubDate"):
        if d.get("PubStatus") == "entrez":
            edat = f"{d.findtext('Year')}-{int(d.findtext('Month')):02d}-{int(d.findtext('Day')):02d}"
    return {
        "pmid": pmid,
        "doi": doi,
        "제목": text_of(mc.find("Article/ArticleTitle")).rstrip("."),
        "저널": text_of(journal.find("Title")),
        "약어": journal.findtext("ISOAbbreviation") or "",
        "issn": sorted(set(issns)),
        "출판일": pub_date(mc),
        "등록일": edat,
        "유형": [t for t in types if t not in ("Journal Article",)],
        "연구": not any(t in NON_RESEARCH for t in types),
        "저자수": n,
        "우리저자": amc[:8],
        "우리저자수": len(amc),
        "제1저자": first,
        "교신": corr[:3],
        "마지막저자": last,
        "진료과": depts[:4],
        "결론": conclusion_of(mc),
    }


def fetch_articles(pmids):
    out = []
    for i in range(0, len(pmids), 200):
        chunk = pmids[i:i + 200]
        xml = eutils("efetch.fcgi", db="pubmed", retmode="xml", id=",".join(chunk)).content
        root = ET.fromstring(xml)
        for pa in root.findall("PubmedArticle"):
            try:
                a = parse_article(pa)
            except Exception as e:                # 한 건이 깨져도 나머지는 받는다
                log("  파싱 실패", pa.findtext("MedlineCitation/PMID"), type(e).__name__, e)
                continue
            if a:
                out.append(a)
        log(f"  efetch {i + len(chunk)}/{len(pmids)}")
    return out


# ── OpenAlex 저널 지표 ────────────────────────────────────────────────────
def fetch_journal_stats(issns, cache):
    """ISSN → {이름, 2y, h}. 90일 안에 받아 둔 것은 다시 묻지 않는다."""
    fresh = (NOW - timedelta(days=90)).strftime("%Y-%m-%d")
    need = [s for s in issns if s not in cache or cache[s].get("받은날", "") < fresh]
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        params = {"filter": "issn:" + "|".join(chunk), "per-page": 200,
                  "select": "display_name,issn,summary_stats"}
        if OA_KEY:
            params["api_key"] = OA_KEY
        try:
            r = requests.get("https://api.openalex.org/sources", params=params, headers=UA, timeout=60)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            log("  OpenAlex 실패 — 지표 없이 진행:", type(e).__name__, e)
            return
        found = set()
        for src in results:
            st = src.get("summary_stats") or {}
            rec = {"이름": src.get("display_name", ""),
                   "2y": round(st.get("2yr_mean_citedness") or 0, 2),
                   "h": st.get("h_index") or 0,
                   "받은날": NOW.strftime("%Y-%m-%d")}
            for s in src.get("issn") or []:
                if s in chunk:
                    cache[s] = rec
                    found.add(s)
        for s in chunk:                           # 못 찾은 ISSN도 적어 둬야 매번 다시 묻지 않는다
            if s not in found:
                cache[s] = {"이름": "", "2y": None, "h": None, "받은날": NOW.strftime("%Y-%m-%d")}
        time.sleep(0.3)


def journal_metric(a, cache):
    vals = [cache[s] for s in a["issn"] if s in cache and cache[s].get("2y") is not None]
    if not vals:
        return None
    return max(vals, key=lambda v: v["2y"])


def tier_of(a, m):
    ab = a["약어"]
    cited = (m or {}).get("2y") or 0
    if ab in TOP_S or cited >= 20:
        return "S"
    if ab in TOP_A or ab.startswith(TOP_A_PREFIX) or cited >= TIER_A_CITED:
        return "A"
    if cited >= TIER_B_CITED:
        return "B"
    return ""


def score_of(a):
    """보도자료 후보 순서. 저널 등급이 먼저, 그다음 우리가 주도했는지."""
    s = {"S": 60, "A": 40, "B": 20}.get(a["등급"], 0)
    s += min((a.get("지표") or 0), 40) * 0.5
    if a["교신"]:
        s += 15
    if a["마지막저자"]:
        s += 12
    if a["제1저자"]:
        s += 10
    if not a["연구"]:
        s -= 50
    return round(s, 1)


def is_candidate(a):
    lead = a["제1저자"] or a["마지막저자"] or bool(a["교신"])
    return a["연구"] and lead and a["등급"] in ("S", "A")


# ── 구글 시트의 보도자료 표시 ─────────────────────────────────────────────
SHEET_RANGE = os.environ.get("RESEARCH_SHEET_RANGE", "").strip() or "'연구성과'!A:D"


def norm_date(s):
    m = re.match(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def load_marks(status):
    """PMID → {상태, 배포일}. 공개 화면에 내보내도 되는 값으로 이미 바꿔서 돌려준다.
    시트를 못 읽으면 None — 그럴 때는 지난 결과의 표시를 그대로 둔다."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sheet_id = (os.environ.get("RESEARCH_SHEET_ID", "").strip()
                or os.environ.get("REPORTER_SHEET_ID", "").strip())
    if not (sa_json and sheet_id):
        status["표시"] = "시트 설정 없음 — 지난 표시 유지"
        return None
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_json), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(SHEET_RANGE, safe='')}"
        resp = AuthorizedSession(creds).get(url, timeout=30)
        resp.raise_for_status()
        values = resp.json().get("values", [])
    except Exception as e:
        status["표시"] = f"시트 불러오기 실패 ({type(e).__name__}) — 지난 표시 유지"
        return None

    today = NOW.strftime("%Y-%m-%d")
    marks, bad = {}, 0
    for row in values:
        cells = [str(c).strip() for c in row] + ["", "", ""]
        pmid = re.sub(r"\D", "", cells[0])
        if not pmid:
            continue                               # 머리글·빈 줄
        st, day = cells[1], norm_date(cells[2])
        if st in ("제외", "보류", "안함", "X", "x"):
            pub = {"상태": "보류"}
        elif day and day <= today:
            pub = {"상태": "배포", "배포일": day}    # 배포일이 지나야 '배포'가 보인다
        elif st == "배포" and not day:
            pub = {"상태": "배포"}                 # 이미 나갔다고 적었으면 날짜가 없어도 숨길 게 없다
        elif st in ("검토", "결정", "배포", "후보", ""):
            pub = {"상태": "검토중"}               # 배포 전 '결정'은 '검토'와 같은 모양으로만 내보낸다
        else:
            bad += 1
            pub = {"상태": "검토중"}
        marks[pmid] = pub
    status["표시"] = f"{len(marks)}건 (알 수 없는 상태값 {bad}건은 검토중으로)"
    return marks


# ── 실행 ──────────────────────────────────────────────────────────────────
def main():
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    papers = {p["pmid"]: p for p in old.get("논문", [])}
    jcache = old.get("저널", {})
    status = {}

    end = NOW.date()
    start = end - timedelta(days=DAYS)
    try:
        pmids = search_pmids(start, end)
        log(f"PubMed {start}~{end}: {len(pmids)}건")
        fresh = fetch_articles(pmids)
    except Exception as e:
        log("PubMed 수집 실패:", type(e).__name__, e)
        status["수집"] = f"실패 ({type(e).__name__}) — 지난 목록 유지"
        fresh = []
    else:
        status["수집"] = f"{start}~{end} PubMed {len(pmids)}건 → 우리 저자 {len(fresh)}건"
    for a in fresh:
        prev = papers.get(a["pmid"])
        if prev and prev.get("pr"):
            a["pr"] = prev["pr"]
        papers[a["pmid"]] = a

    # 오래된 것 정리 (등록일 기준)
    cut = (NOW - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    papers = {k: v for k, v in papers.items() if (v.get("등록일") or "9999") >= cut}

    all_issn = sorted({s for p in papers.values() for s in p["issn"]})
    fetch_journal_stats(all_issn, jcache)
    jcache = {k: v for k, v in jcache.items() if k in set(all_issn)}

    marks = load_marks(status)
    for p in papers.values():
        m = journal_metric(p, jcache)
        p["지표"] = m["2y"] if m else None
        p["등급"] = tier_of(p, m)
        p["점수"] = score_of(p)
        p["후보"] = is_candidate(p)
        # 최상위 저널에 우리가 공동저자로만 들어간 논문 — 후보는 아니지만 따로 보여 준다
        p["참여"] = (not p["후보"]) and p["연구"] and p["등급"] == "S"
        if not p["등급"]:
            p.pop("결론", None)                    # 파일 크기 — 등급 밖 논문의 초록 결론은 안 쓴다
        if marks is not None:
            if p["pmid"] in marks:
                p["pr"] = marks[p["pmid"]]
            else:
                p.pop("pr", None)

    items = sorted(papers.values(), key=lambda p: (p.get("등록일", ""), p["점수"]), reverse=True)
    status["후보"] = sum(p["후보"] for p in items)
    OUT.write_text(json.dumps({
        "생성": NOW.strftime("%Y-%m-%d %H:%M"),
        "기준": {"A문턱": TIER_A_CITED, "B문턱": TIER_B_CITED, "보관일수": KEEP_DAYS},
        "상태": status,
        "건수": len(items),
        "논문": items,
        "저널": jcache,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
