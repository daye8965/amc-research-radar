"""우리가 배포한 보도자료가 어느 논문을 다룬 것인지 맞춘다.

보도자료 목록은 언론 대시보드 개발본이 구글 시트 '언론 업무' 탭에서 뽑아 공개 주소에 올려 둔
press_releases.json을 그대로 읽는다(시트를 따로 읽지 않으므로 이 저장소엔 구글 키가 필요 없다).

1) 후보 좁히기(규칙) — 보도자료 의료진의 한글 이름을 영문 표기로 바꿔(name_match) 논문의 우리 저자와 맞추고,
   PubMed 등록일이 배포일 PRESS_BEFORE일 전 ~ PRESS_AFTER일 뒤 안에 있는 논문만 남긴다.
   보도자료는 대개 논문이 온라인에 실린 뒤 몇 주~몇 달 안에 나간다.
2) 고르기(Claude) — 보도자료 제목은 한글, 논문 제목은 영어라 규칙으로는 같은 교수의 여러 논문 중
   하나를 못 고른다. 후보 최대 PRESS_MAX_CANDS편을 보여 주고 어느 것인지(또는 해당 없음) 묻는다.
   행사·수상·출간·수술 성과처럼 논문과 무관한 보도자료는 '없음'이 정답이다.

**배포일이 오늘 이전인 보도자료만** 맞춘다 — 목록에는 배포 예정 건도 들어 있고, 이 페이지는 공개 주소다.
판정은 research.json의 `보도자료판정`에 쌓아 두고, 같은 보도자료·같은 후보면 다시 묻지 않는다.
ANTHROPIC_API_KEY가 없으면 새로 묻지 않고 지난 판정만 쓴다.
"""
import json
import os
import re
from datetime import date, timedelta

import requests

from name_match import same_person

PRESS_URL = (os.environ.get("PRESS_URL", "").strip()
             or "https://daye8965.github.io/hospital-news-dashboard-dev/press_releases.json")
PRESS_BEFORE = 180      # 배포일 기준 논문 등록일이 이만큼 앞까지
PRESS_AFTER = 7         # 배포 뒤에 PubMed에 늦게 올라오는 경우
PRESS_MAX_CANDS = 8
MODEL = "claude-haiku-5-5"            # 언론 대시보드 AI 판정과 같은 경량 모델
PRICE_IN, PRICE_OUT = 0.10, 0.50      # 달러 / 100만 토큰
MAX_CALLS = int(os.environ.get("PRESS_MAX_CALLS", "300") or 300)
# 프롬프트·판정 규칙을 바꾸면 올린다 — 판이 다른 지난 판정은 다시 묻는다
JUDGE_VER = 2

SYSTEM = """당신은 서울아산병원 홍보팀을 돕습니다. 병원이 배포한 한국어 보도자료 하나와,
그 보도자료의 의료진이 저자로 들어간 영어 논문 후보 목록을 받습니다.
이 보도자료가 후보 중 어느 논문의 연구 결과를 알리는 것인지 고르세요.

- 보도자료의 주제(질환·치료·기술·연구 대상)가 논문 제목·결론과 같은 연구를 가리킬 때만 고릅니다.
  같은 교수의 같은 분야 논문이라도 다른 연구면 고르지 않습니다.
- 행사·심포지엄·수상·임명·출간·후원·협약·센터 개소 같은 보도자료는 논문 소개가 아니므로 '없음'입니다.
- 수술·시술 성공 사례, 첫 시행·도입, 누적 건수 달성 보도자료도 대개 '없음'입니다. 같은 분야·같은 술기의
  논문(동물 실험, 다른 환자 증례, 다른 결과지표)이 후보에 있어도, 그 논문의 결과를 알리는 보도자료가 아니면 고르지 않습니다.
- 보도자료가 말하는 핵심 결과(무엇을 누구에게 해서 무엇을 보였나)가 논문 제목·결론에 그대로 있어야 '높음'입니다.
  주제는 비슷하지만 대상·방법·결과 중 하나라도 확인되지 않으면 '보통' 이하입니다.
- 잘못 붙이는 것이 빠뜨리는 것보다 나쁩니다.
pmid에는 고른 논문의 PMID를, 없으면 빈 문자열을 넣습니다. 확신은 높음·보통·낮음 중 하나, 근거는 한 문장."""

SCHEMA = {
    "type": "object",
    "properties": {
        "pmid": {"type": "string"},
        "확신": {"type": "string", "enum": ["높음", "보통", "낮음"]},
        "근거": {"type": "string"},
    },
    "required": ["pmid", "확신", "근거"],
    "additionalProperties": False,
}


def load_releases(today):
    """배포일이 오늘 이전이고 의료진이 적힌 보도자료만."""
    r = requests.get(PRESS_URL, params={"t": int(today.strftime("%Y%m%d"))}, timeout=30,
                     headers={"User-Agent": "amc-research-radar/1.0"})
    r.raise_for_status()
    out = []
    for x in r.json().get("목록", []):
        d = x.get("날짜", "")
        if not d or d > today.isoformat() or not x.get("의료진"):
            continue
        out.append({"날짜": d, "제목": x.get("제목", ""), "의료진": x["의료진"], "진료과": x.get("진료과", "")})
    return out


def _lead_names(p):
    names = set(p.get("교신") or [])
    au = p.get("우리저자") or []
    if au and p.get("제1저자"):
        names.add(au[0])
    if au and p.get("마지막저자"):
        names.add(au[-1])
    return names


def candidates(rel, papers):
    rd = date.fromisoformat(rel["날짜"])
    out = []
    for p in papers:
        if not p.get("등록일"):
            continue
        gap = (rd - date.fromisoformat(p["등록일"])).days
        if not -PRESS_AFTER <= gap <= PRESS_BEFORE:
            continue
        au = p.get("우리저자") or []
        hit = [k for k in rel["의료진"] if any(same_person(k, a) for a in au)]
        if not hit:
            continue
        lead = _lead_names(p)
        lead_hit = sum(1 for k in hit if any(same_person(k, a) for a in lead))
        out.append((lead_hit, len(hit), gap, p, hit))
    # 우리 의료진이 주도한 논문 → 맞는 이름이 많은 논문 → 배포일에 가까운 논문
    out.sort(key=lambda c: (-c[0], -c[1], c[2]))
    return out[:PRESS_MAX_CANDS]


def prompt_for(rel, cands):
    lines = [f"보도자료 배포일: {rel['날짜']}",
             f"보도자료 제목: {rel['제목']}",
             f"의료진: {', '.join(rel['의료진'])}" + (f" / 진료과: {rel['진료과']}" if rel['진료과'] else ""),
             "", "논문 후보:"]
    for lead_hit, n, gap, p, hit in cands:
        role = "주도" if lead_hit else "공동저자"
        lines.append(f"- PMID {p['pmid']} | {p.get('약어') or p.get('저널')} | PubMed 등록 {p['등록일']} "
                     f"| 보도자료 의료진 {', '.join(hit)}({role})")
        lines.append(f"  제목: {p['제목']}")
        if p.get("결론"):
            lines.append(f"  결론: {p['결론']}")
    return "\n".join(lines)


class Judge:
    def __init__(self):
        self.client = None
        self.fatal = None
        self.calls = self.tin = self.tout = self.errors = 0
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            import anthropic
            self.anthropic = anthropic
            self.client = anthropic.Anthropic(max_retries=4)

    def ready(self):
        return self.client is not None and not self.fatal and self.calls < MAX_CALLS

    def ask(self, text):
        self.calls += 1
        a = self.anthropic
        try:
            resp = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": text}],
            )
        except a.APIError as e:
            self.errors += 1
            # 키·권한 문제는 다시 해도 똑같이 막힌다 — 한 번 알리고 멈춘다
            if isinstance(e, (a.AuthenticationError, a.PermissionDeniedError)):
                self.fatal = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"  [중단] {self.fatal} — ANTHROPIC_API_KEY를 확인하세요")
            else:
                print(f"  API 오류: {type(e).__name__}: {str(e)[:200]}")
            return None
        self.tin += resp.usage.input_tokens
        self.tout += resp.usage.output_tokens
        if resp.stop_reason in ("refusal", "max_tokens"):
            self.errors += 1
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            self.errors += 1
            return None

    def cost(self):
        return round((self.tin * PRICE_IN + self.tout * PRICE_OUT) / 1_000_000, 4)


def match_releases(papers, cache, today, status):
    """papers(list) 각각에 p['pr']을 붙이고, 판정 캐시(cache, dict)를 고쳐 돌려준다."""
    try:
        releases = load_releases(today)
    except Exception as e:
        status["보도자료"] = f"목록 불러오기 실패 ({type(e).__name__}) — 지난 판정 유지"
        releases = None

    by_pmid = {p["pmid"]: p for p in papers}
    judge = Judge()
    asked = kept = 0
    new_cache = {}
    if releases is not None:
        oldest = min((p["등록일"] for p in papers if p.get("등록일")), default=today.isoformat())
        for rel in releases:
            # 논문 보관 기간보다 오래된 보도자료는 후보가 없어 볼 필요가 없다
            if rel["날짜"] < oldest:
                continue
            key = f"{rel['날짜']}|{rel['제목']}"
            cands = candidates(rel, papers)
            if not cands:
                continue
            cset = sorted(c[3]["pmid"] for c in cands)
            prev = cache.get(key)
            if prev and prev.get("후보") == cset and prev.get("판") == JUDGE_VER:
                new_cache[key] = prev                 # 같은 후보로 이미 물었다
                kept += 1
                continue
            if not judge.ready():
                if prev:
                    new_cache[key] = prev             # 못 물으면 지난 판정이라도 쓴다
                continue
            ans = judge.ask(prompt_for(rel, cands))
            if ans is None:
                if prev:
                    new_cache[key] = prev
                continue
            asked += 1
            pick = re.sub(r"\D", "", ans.get("pmid", ""))
            if pick not in cset:
                pick = ""                             # 후보 밖 PMID를 지어내면 버린다
            sure = ans.get("확신", "")
            # 확신이 '높음'일 때만 붙인다 — 2026-11-04 첫 실행에서 30건 중 5건이 수술 성공·건수 달성
            # 보도자료에 같은 분야 다른 논문을 붙인 것이었다
            new_cache[key] = {"pmid": pick if sure == "높음" else "", "후보": cset, "확신": sure,
                              "고른것": pick, "근거": ans.get("근거", "")[:200], "모델": MODEL, "판": JUDGE_VER}
    else:
        new_cache = dict(cache)

    for p in papers:
        p.pop("pr", None)
    matched = 0
    for key, v in sorted(new_cache.items()):          # 날짜순 — 같은 논문이면 나중 보도자료가 남는다
        p = by_pmid.get(v.get("pmid") or "")
        if not p:
            continue
        day, title = key.split("|", 1)
        p["pr"] = {"상태": "배포", "배포일": day, "보도자료": title}
        matched += 1

    status["보도자료"] = (f"맞춘 논문 {matched}편 · 새로 물음 {asked} · 지난 판정 {kept}"
                       + (f" · 오류 {judge.errors}" if judge.errors else "")
                       + ("" if judge.client else " · ANTHROPIC_API_KEY 없음(새 판정 안 함)")
                       + (f" · 중단: {judge.fatal}" if judge.fatal else ""))
    if judge.calls:
        status["AI비용"] = {"호출": judge.calls, "입력토큰": judge.tin, "출력토큰": judge.tout, "달러": judge.cost()}
    return new_cache
