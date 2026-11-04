"""연구성과 레이더 — 서울아산병원 소속 저자의 신규 논문을 PubMed에서 모아 보도자료 후보를 고른다.

결과는 docs/research.json 하나. 같은 저장소의 docs/index.html(GitHub Pages)이 읽는다.

- 수집: PubMed E-utilities. 소속(affiliation)에 서울아산병원이 들어간 논문을
  PubMed 등록일(edat) 기준 최근 RESEARCH_DAYS일만큼 다시 훑는다(이미 있는 PMID는 덮어씀).
- 저널 지표: OpenAlex의 2yr_mean_citedness(최근 2년 논문의 평균 피인용 — IF와 같은 정의의 무료 대용치).
  Clarivate IF는 무료 API가 없다. 값이 IF보다 작게 나오는 경향이 있어(Lancet IF 88 → 21.6)
  문턱을 IF 숫자로 생각하면 안 된다.
- 보도자료 표시: 우리가 배포한 보도자료 목록(언론 대시보드 개발본의 press_releases.json)과
  논문을 자동으로 맞춘다(press_match.py). 배포일이 지난 보도자료만 쓴다.

환경변수: RESEARCH_DAYS(기본 10) · RESEARCH_KEEP_DAYS(기본 0 = 지우지 않음) · NCBI_API_KEY · OPENALEX_API_KEY (모두 선택)
          ANTHROPIC_API_KEY(보도자료 판정) · PRESS_URL(보도자료 목록 주소, 기본은 개발 대시보드)
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from press_match import match_releases
from value_judge import eligible as value_eligible, judge_value

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
OUT = Path("docs/research.json")

DAYS      = int(os.environ.get("RESEARCH_DAYS", "10") or 10)
# 0이면 오래된 논문을 지우지 않는다 — 화면에서 연도별로 보려면 쌓여 있어야 한다(2026-11-04부터)
KEEP_DAYS = int(os.environ.get("RESEARCH_KEEP_DAYS", "0") or 0)
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
TIER_S_CITED = 20.0
TIER_A_CITED = 10.0
TIER_B_CITED = 6.0
# JCR IF 문턱 — docs/journal_if.json(jcr_import.py가 S2Journal PDF에서 만든 것)에 있는 저널은 IF로 매긴다.
# OpenAlex 값보다 IF를 우선한다(Ann Oncol: OpenAlex 1.8 vs IF 80.4)
JIF_PATH = Path("docs/journal_if.json")
TIER_S_IF = 30.0
TIER_A_IF = 10.0
TIER_B_IF = 5.0


def load_jif():
    try:
        return json.loads(JIF_PATH.read_text(encoding="utf-8")).get("저널", {})
    except Exception:
        return {}


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
    leads = []                                    # 우리 쪽 제1·교신·마지막 저자 — 이름·역할·진료과 (분석 탭)
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
        roles = [r for r, ok in (("제1", i == 0), ("교신", any("@" in a for a in affs)),
                                 ("마지막", i == n - 1 and n > 1)) if ok]
        if roles:
            leads.append({"이름": name, "역할": roles,
                          "진료과": next((dept_of(a) for a in mine if dept_of(a)), "")})
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
        "우리저자": amc,                  # 보도자료 의료진과 맞추려면 전원이 필요하다
        "우리저자수": len(amc),
        "제1저자": first,
        "교신": corr[:3],
        "마지막저자": last,
        "진료과": depts[:4],
        "주도저자": leads[:4],
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


def jif_of(a, jif):
    """이 논문 저널의 JCR 기록 (ISSN 중 하나라도 맞으면). 없으면 None."""
    recs = [jif[s] for s in a["issn"] if s in jif]
    return max(recs, key=lambda r: r["IF"]) if recs else None


def tier_of(a, m, j=None):
    ab = a["약어"]
    if j:                                         # JCR IF가 있으면 그것만 본다
        if ab in TOP_S or j["IF"] >= TIER_S_IF:
            return "S"
        if ab in TOP_A or ab.startswith(TOP_A_PREFIX) or j["IF"] >= TIER_A_IF:
            return "A"
        return "B" if j["IF"] >= TIER_B_IF else ""
    cited = (m or {}).get("2y") or 0
    if ab in TOP_S or cited >= TIER_S_CITED:
        return "S"
    if ab in TOP_A or ab.startswith(TOP_A_PREFIX) or cited >= TIER_A_CITED:
        return "A"
    if cited >= TIER_B_CITED:
        return "B"
    return ""


def score_of(a):
    """보도자료 후보 순서. 저널 등급이 먼저, 그다음 우리가 주도했는지."""
    s = {"S": 60, "A": 40, "B": 20}.get(a["등급"], 0)
    # IF가 있으면 IF를 OpenAlex 축척으로 대략 맞춰 쓴다(NEJM IF 85 ≈ OpenAlex 33)
    m = a["IF"] / 2.5 if a.get("IF") else (a.get("지표") or 0)
    s += min(m, 40) * 0.5
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
    pr_cache = old.get("보도자료판정", {})
    value_cache = old.get("보도가치", {})
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
        papers[a["pmid"]] = a

    # 오래된 것 정리 (등록일 기준) — KEEP_DAYS가 0이면 하지 않는다
    if KEEP_DAYS > 0:
        cut = (NOW - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        papers = {k: v for k, v in papers.items() if (v.get("등록일") or "9999") >= cut}

    all_issn = sorted({s for p in papers.values() for s in p["issn"]})
    fetch_journal_stats(all_issn, jcache)
    jcache = {k: v for k, v in jcache.items() if k in set(all_issn)}

    jif = load_jif()
    for p in papers.values():
        m = journal_metric(p, jcache)
        j = jif_of(p, jif)
        p["지표"] = m["2y"] if m else None
        if j:
            p["IF"], p["IF상위"] = j["IF"], j["상위"]
        else:
            p.pop("IF", None)
            p.pop("IF상위", None)
        p["등급"] = tier_of(p, m, j)
        p["점수"] = score_of(p)
        p["후보"] = is_candidate(p)
        # 최상위 저널에 우리가 공동저자로만 들어간 논문 — 후보는 아니지만 따로 보여 준다
        p["참여"] = (not p["후보"]) and p["연구"] and p["등급"] == "S"
        if not p["등급"] and not value_eligible(p):
            p.pop("결론", None)                    # 파일 크기 — 등급 밖이고 보도가치도 안 보는 논문의 결론은 안 쓴다

    # 결론까지 정리한 뒤에 맞춘다 — 판정에 결론을 보여 준다
    names = {}
    pr_cache = match_releases(list(papers.values()), pr_cache, NOW.date(), status, names)
    if not names:
        names = old.get("한글이름", {})          # 보도자료 목록을 못 읽은 날은 지난 것을 쓴다
    value_cache = judge_value(list(papers.values()), value_cache, status)

    items = sorted(papers.values(), key=lambda p: (p.get("등록일", ""), p["점수"]), reverse=True)
    status["후보"] = sum(p["후보"] for p in items)
    OUT.write_text(json.dumps({
        "생성": NOW.strftime("%Y-%m-%d %H:%M"),
        # 화면의 '등급 기준' 설명이 이 값을 그대로 읽는다 — 기준을 바꾸면 설명도 함께 바뀐다
        "기준": {"S문턱": TIER_S_CITED, "A문턱": TIER_A_CITED, "B문턱": TIER_B_CITED,
                 "S_IF": TIER_S_IF, "A_IF": TIER_A_IF, "B_IF": TIER_B_IF,
                 "JCR저널수": len(jif), "JCR연도": sorted({r["연도"] for r in jif.values()}),
                 "S저널": sorted(TOP_S), "A저널": sorted(TOP_A), "A접두어": [x.strip() for x in TOP_A_PREFIX],
                 "보관일수": KEEP_DAYS},
        "상태": status,
        "건수": len(items),
        "논문": items,
        "저널": jcache,
        "보도자료판정": pr_cache,
        "보도가치": value_cache,
        "한글이름": names,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
