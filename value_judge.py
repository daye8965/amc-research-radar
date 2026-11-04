"""AI 보도가치 판정 — 우리 의료진이 주도한 연구 논문마다 '일반 언론에 낼 만한가'를 Claude에게 묻는다.

저널 등급(S·A·B)은 '좋은 저널인가'만 본다. 실제로 팀이 보도자료를 낸 논문 23편 중 14편은 등급 후보 밖이었다
(Sci Rep·BMC Geriatr 같은 저널이지만 환자에게 와닿는 결과·국내 첫 사례·생활 밀착형 주제).
그 빈칸을 메우려는 칸이다. 등급과 따로 보여 주고, 후보 판정(`후보`)에는 섞지 않는다.

- 대상: 연구 논문(`연구`) + 우리 의료진이 제1·교신·마지막 저자. 공동저자로만 들어간 논문은 우리가 낼 일이 드물다.
- 한 번에 BATCH편씩 묻는다(호출 수·비용을 줄이려고). 판정은 research.json의 `보도가치`(pmid → 결과)에 쌓고,
  같은 판(VALUE_VER)이면 다시 묻지 않는다. 새 논문만 매일 조금씩 묻게 된다.
- ANTHROPIC_API_KEY가 없으면 새로 묻지 않고 지난 판정만 붙인다.
"""
import os
import re

from press_match import Judge

VALUE_VER = 3           # 프롬프트·기준을 바꾸면 올린다 — 판이 다른 판정은 다시 묻는다
# v2(2026-11-04): v1은 '저널 명성은 보지 말라'고 해서 Lancet·NEJM 논문이 '술기 비교'로 보통이 될 수 있었다.
# 실제 보도자료는 'NEJM 3번째 논문 게재'·'(IF 81.2)'처럼 저널 게재 자체를 소재로 쓴다.
# v3(2026-11-04): v2는 팀이 실제로 보도자료를 낸 23편 중 5편만 '높음'이었다. 팀은 '첫 기준·가이드라인',
# '새 진단 기술', '한국인 데이터', '난치병 치료 가능성'을 저널 등급과 상관없이 낸다. 그래서 기준을 팀 쪽으로 옮기고,
# 팀이 실제로 낸 논문을 예시로 보여 준다(examples). 측정은 value_eval.py — 예시와 맞히는 논문을 갈라서 잰다.
BATCH = 20
MAX_CALLS = int(os.environ.get("VALUE_MAX_CALLS", "150") or 150)

# v2 프롬프트 — value_eval.py가 비교용으로 쓴다
SYSTEM_V2 = """당신은 서울아산병원 홍보팀에서 보도자료 기획을 돕습니다. 우리 병원 의료진이 주도한 논문 목록을 받고,
각 논문이 **일반 언론(종합일간지·방송·포털)** 에 보도자료로 낼 가치가 얼마나 되는지 판단합니다.
각 논문에 저널 등급(S·A·B)과 IF(또는 피인용)가 붙어 있습니다.
**최상위 학술지 게재는 그 자체로 강한 기사 소재입니다**("세계 최고 권위 학술지 ○○ 게재"). S 등급 저널의 연구 논문은 높음,
A 등급 저널의 임상 결과(환자 대상 치료·진단·예후)는 높음으로 보기 쉽습니다. 다만 등급이 낮아도 주제가 좋으면 높음입니다.

높음 — 기자가 바로 기사를 쓸 만한 것. 예:
  · 환자·국민이 바로 체감하는 결과(생존율·재발·부작용을 크게 바꿈, 검사·치료가 쉬워짐, 흔한 질환의 새 위험 요인)
  · 국내·세계 첫 시도, 대규모(수천~수만 명) 한국인 데이터로 확인한 사실, 다기관 임상시험의 분명한 결론
  · 생활과 맞닿은 주제(약 복용, 운동, 수면, 음주·흡연, 고령자·소아·임산부), AI·로봇 등 새 기술의 실제 성과
보통 — 의미 있지만 전문지·의학 전문 기자 대상에 가까운 것(술기 비교, 예후 인자, 특정 환자군 분석)
낮음 — 기초 기전 연구로 환자 연결이 먼 것, 증례 보고, 소규모·기술적 방법론, 가이드라인 해설, 일반인이 이해하기 어려운 것

'높음'은 전체의 10~20% 정도가 되도록 고르세요.
각 논문마다 pmid, 가치(높음·보통·낮음), 한줄(기사 제목처럼 쓴 한국어 요약 40자 이내),
이유(판단 근거 한국어 60자 이내)를 돌려주세요. 받은 논문은 빠짐없이 모두 답합니다."""

SYSTEM_V3 = """당신은 서울아산병원 홍보팀에서 보도자료 기획을 돕습니다. 우리 병원 의료진이 주도한 논문 목록을 받고,
**우리 팀이 이 논문으로 보도자료를 낼 만한지** 판단합니다. 아래 '팀이 실제로 낸 예시'가 기준입니다 — 일반적인 뉴스 감각보다
이 팀이 실제로 고른 논문의 모양을 따르세요.

이 팀이 고르는 논문의 특징:
  · **저널 등급과 상관없이** 낸다. Sci Rep·BMC Geriatr·Biomater Res 같은 저널의 논문도 냈다.
  · 우리 의료진이 **새 진단·검사 기술**을 만들었거나 정확도를 크게 높인 것(혈액·소변·내시경·영상·AI 진단, 바이오마커)
  · **국내·세계 첫 기준·가이드라인·공식**을 제시한 것(희귀 종양 치료 가이드라인, 수술 기준 공식)
  · **난치·희귀질환의 새 치료 가능성**(오가노이드·줄기세포·면역치료·약물 재창출 — 기초연구라도 치료로 이어지는 메시지가 분명하면)
  · **한국인·아시아인 환자 데이터**로 발생률·위험 요인·장기 성적을 밝힌 것
  · 치료를 **더 쉽게·덜 힘들게** 바꾸는 것(치료 횟수 줄이기, 약 중단해도 되는지, 내시경으로 수술 대신)
  · 최상위 학술지(S 등급) 게재, A 등급 저널의 임상 결과

높음 — 위 특징이 분명하고, 한 문장으로 일반인에게 설명되는 결과가 있다
보통 — 의미는 있지만 메시지가 전문적이거나 결과가 애매하다(단순 술기 비교, 예후 인자 나열, 소규모 후향 분석)
낮음 — 증례 보고, 리뷰·해설, 방법론·통계 기법, 환자와 연결이 먼 기초 기전, 우리 병원 역할이 작아 보이는 것

'높음'은 전체의 15% 안팎이 되도록 고르세요.
각 논문마다 pmid, 가치(높음·보통·낮음), 한줄(기사 제목처럼 쓴 한국어 요약 40자 이내),
이유(판단 근거 한국어 60자 이내)를 돌려주세요. 받은 논문은 빠짐없이 모두 답합니다.
{examples}"""

MAX_EXAMPLES = 40


def example_text(papers):
    """팀이 실제로 보도자료를 낸 논문 → 프롬프트 예시. 보도자료가 쌓일수록 저절로 늘어난다."""
    ex = sorted([p for p in papers if p.get("pr")], key=lambda p: p["pr"].get("배포일", ""), reverse=True)
    if not ex:
        return ""
    lines = ["", "팀이 실제로 보도자료로 낸 논문 (보도자료 제목 ← 저널 | 논문 제목):"]
    for p in ex[:MAX_EXAMPLES]:
        lines.append(f"- {p['pr']['보도자료']} ← {p.get('약어') or p.get('저널')} | {p['제목']}")
    return "\n".join(lines)


def build_system(examples_from, version=None):
    if (version or VALUE_VER) == 2:
        return SYSTEM_V2
    return SYSTEM_V3.replace("{examples}", example_text(examples_from))


SCHEMA = {
    "type": "object",
    "properties": {
        "결과": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "가치": {"type": "string", "enum": ["높음", "보통", "낮음"]},
                    "한줄": {"type": "string"},
                    "이유": {"type": "string"},
                },
                "required": ["pmid", "가치", "한줄", "이유"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["결과"],
    "additionalProperties": False,
}


def eligible(p):
    return p.get("연구") and (p.get("제1저자") or p.get("마지막저자") or p.get("교신"))


def paper_block(p):
    metric = f"IF {p['IF']}" if p.get("IF") is not None else (
        f"피인용 {p['지표']}" if p.get("지표") is not None else "지표 없음")
    role = "·".join(r for r, ok in (("제1저자", p.get("제1저자")), ("교신", bool(p.get("교신"))),
                                    ("마지막저자", p.get("마지막저자"))) if ok)
    types = ", ".join(p.get("유형") or [])
    tier = f"{p['등급']}등급 · " if p.get("등급") else ""
    lines = [f"[PMID {p['pmid']}] {p.get('약어') or p.get('저널')} ({tier}{metric})"
             + (f" · {types}" if types else "") + f" · 우리 역할: {role} · 저자 {p.get('저자수')}명",
             f"제목: {p['제목']}"]
    if p.get("결론"):
        lines.append(f"결론: {p['결론']}")
    return "\n".join(lines)


def judge_value(papers, cache, status):
    """papers(list)에 p['AI가치']를 붙이고 캐시(dict, pmid → 판정)를 돌려준다."""
    targets = [p for p in papers if eligible(p)]
    todo = [p for p in targets if (cache.get(p["pmid"]) or {}).get("판") != VALUE_VER]
    todo.sort(key=lambda p: p.get("등록일", ""), reverse=True)       # 새 논문부터

    # 예시는 이 판정 실행에서 고정 — 보도자료가 새로 붙은 논문이 생겨도 이미 판정한 것은 다시 묻지 않는다
    judge = Judge(system=build_system(papers), schema=SCHEMA, max_calls=MAX_CALLS, max_tokens=8000)
    asked = 0
    for i in range(0, len(todo), BATCH):
        if not judge.ready():
            break
        chunk = todo[i:i + BATCH]
        ans = judge.ask("\n\n".join(paper_block(p) for p in chunk))
        if not ans:
            continue
        want = {p["pmid"] for p in chunk}
        for r in ans.get("결과", []):
            pmid = re.sub(r"\D", "", r.get("pmid", ""))
            if pmid not in want or r.get("가치") not in ("높음", "보통", "낮음"):
                continue                               # 목록 밖 PMID·이상한 값은 버린다
            cache[pmid] = {"가치": r["가치"], "한줄": r.get("한줄", "")[:60],
                           "이유": r.get("이유", "")[:100], "판": VALUE_VER}
            asked += 1

    alive = {p["pmid"] for p in papers}
    cache = {k: v for k, v in cache.items() if k in alive}
    for p in papers:
        v = cache.get(p["pmid"])
        if v and eligible(p):
            p["AI가치"] = {"가치": v["가치"], "한줄": v["한줄"], "이유": v["이유"]}
            # 하한선 — S 등급 저널에 우리가 주도한 연구 논문은 AI 판단과 상관없이 높음
            if p.get("등급") == "S" and v["가치"] != "높음":
                p["AI가치"].update({"가치": "높음", "이유": f"최상위 학술지({p.get('약어')}) 게재 — S 등급은 항상 높음"})
        else:
            p.pop("AI가치", None)

    left = sum(1 for p in targets if (cache.get(p["pmid"]) or {}).get("판") != VALUE_VER)
    high = sum(1 for p in papers if (p.get("AI가치") or {}).get("가치") == "높음")
    status["보도가치"] = (f"대상 {len(targets)}편 · 새로 판정 {asked} · 남은 것 {left} · 높음 {high}편"
                       + ("" if judge.client else " · ANTHROPIC_API_KEY 없음(새 판정 안 함)")
                       + (f" · 중단: {judge.fatal}" if judge.fatal else ""))
    if judge.calls:
        status["보도가치비용"] = {"호출": judge.calls, "입력토큰": judge.tin, "출력토큰": judge.tout,
                             "달러": judge.cost()}
    return cache
