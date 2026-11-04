"""AI 보도가치 판정이 '팀이 실제로 보도자료를 낸 논문'을 얼마나 '높음'으로 고르는지 잰다. 결과는 로그로만 남긴다.

    python value_eval.py          # ANTHROPIC_API_KEY 필요. 수동 워크플로 value-eval.yml로 돌린다

- 정답 = docs/research.json에서 보도자료가 붙은(`pr`) 판정 대상 논문.
- v3는 정답을 예시로 보여 주므로, **예시로 준 논문을 맞히는 것은 반칙**이다. 그래서 정답을 배포일 순으로 번갈아
  두 묶음으로 나눠, 한 묶음을 예시로 주고 다른 묶음을 맞히게 한다(2겹 교차).
- '아무거나 높음'이면 정답도 다 맞으니, **보도자료가 없는 논문 NEG편**을 섞어 그쪽의 '높음' 비율도 함께 본다.
  둘 다 봐야 한다 — 정답 적중은 오르고 일반 논문의 높음 비율은 15% 안팎에 머물러야 좋아진 것이다.
- 실서비스와 같게 S 등급 하한선(항상 높음)을 적용한 값으로 센다.
"""
import json
import random
import sys
from pathlib import Path

import value_judge as V
from press_match import Judge

NEG = 150


def ask_all(judge, items):
    out = {}
    items = items[:]
    random.Random(7).shuffle(items)                   # 정답이 한 호출에 몰리지 않게
    for i in range(0, len(items), V.BATCH):
        chunk = items[i:i + V.BATCH]
        ans = judge.ask("\n\n".join(V.paper_block(p) for p in chunk)) or {}
        for r in ans.get("결과", []):
            out[r.get("pmid", "").strip()] = r.get("가치")
    return out


def level(p, got):
    v = got.get(p["pmid"])
    if p.get("등급") == "S" and v:
        return "높음"
    return v or "응답없음"


def report(name, pos, neg, got):
    def cnt(group):
        c = {}
        for p in group:
            c[level(p, got)] = c.get(level(p, got), 0) + 1
        return c
    cp, cn = cnt(pos), cnt(neg)
    hp, hn = cp.get("높음", 0), cn.get("높음", 0)
    print(f"[{name}] 정답 {len(pos)}편 중 높음 {hp} ({hp / max(len(pos), 1):.0%}) · 보통 이상 "
          f"{hp + cp.get('보통', 0)} | 일반 논문 {len(neg)}편 중 높음 {hn} ({hn / max(len(neg), 1):.0%}) | {cp} / {cn}")
    for p in pos:
        print(f"    {level(p, got):4} {p.get('등급') or '-'} {p['pr']['보도자료'][:40]}")


def main():
    d = json.loads(Path("docs/research.json").read_text(encoding="utf-8"))
    papers = d["논문"]
    elig = [p for p in papers if V.eligible(p)]
    pos = sorted([p for p in elig if p.get("pr")], key=lambda p: p["pr"]["배포일"])
    rest = [p for p in elig if not p.get("pr")]
    neg = random.Random(42).sample(rest, min(NEG, len(rest)))
    folds = [pos[0::2], pos[1::2]]
    print(f"정답 {len(pos)}편 (묶음 {len(folds[0])}/{len(folds[1])}) · 일반 논문 표본 {len(neg)}편")

    # v2 — 예시 없음. 한 번에 잰다
    j2 = Judge(system=V.build_system([], 2), schema=V.SCHEMA, max_calls=100, max_tokens=8000)
    if not j2.client:
        print("ANTHROPIC_API_KEY 없음")
        return 1
    got2 = ask_all(j2, pos + neg)
    report("v2", pos, neg, got2)

    # v3 — 묶음마다 다른 묶음을 예시로
    got3, negs = {}, {}
    for k in (0, 1):
        examples = folds[1 - k]
        j3 = Judge(system=V.build_system(examples, 3), schema=V.SCHEMA, max_calls=100, max_tokens=8000)
        g = ask_all(j3, folds[k] + neg)
        got3.update({p["pmid"]: g.get(p["pmid"]) for p in folds[k]})
        for p in neg:                                   # 일반 논문은 두 번 물었으니 둘 다 센다
            negs.setdefault(p["pmid"], []).append(g.get(p["pmid"]))
        print(f"  v3 묶음{k + 1}: 호출 {j3.calls} · ${j3.cost()}")
    neg_twice = [dict(p, pmid=f"{p['pmid']}#{i}") for p in neg for i in (0, 1)]
    got3.update({f"{p['pmid']}#{i}": v for p in neg for i, v in enumerate(negs.get(p["pmid"], []))})
    report("v3 (교차)", pos, neg_twice, got3)
    print(f"v2 호출 {j2.calls} · ${j2.cost()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
