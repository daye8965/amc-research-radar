"""서울아산병원 홈페이지 의료진 명단으로 논문 저자의 한글 이름·진료과를 찾는다.

- 명단: 홈페이지 '의료진/진료과'(www.amc.seoul.kr/asan/staff/base/staffBaseInfoList.do?searchHpCd=Dxxx)는 진료과마다
  그 과 의료진 전원(임상강사 포함)을 한 페이지에 보여 준다. 센터·병원 단위 코드는 빼고 **진료과 코드만** 돈다(약 80장).
  robots.txt는 /asan/staff/를 막지 않는다. **일주일에 한 번만** 새로 읽고(STAFF_DAYS), 1초에 1장씩 천천히 읽는다.
  명단은 data/amc_staff.json에 둔다(이름·진료과뿐 — 홈페이지에 공개된 정보).
- 맞추기: 저자 영문 이름을 name_match로 한글 표기 후보와 비교해, **같은 진료과에서 한 명만 맞을 때** 붙인다.
  진료과 정보가 없는 저자는 명단 전체에서 한 명만 맞을 때만. 진료과가 있는데 그 과에 맞는 사람이 없으면 붙이지 않는다
  (기초의학교실 연구원처럼 홈페이지에 없는 동명이인일 가능성이 크다).
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from dept_ko import dept_ko
from name_match import same_person

KST = timezone(timedelta(hours=9))
STAFF_PATH = Path("data/amc_staff.json")
STAFF_DAYS = 7
BASE = "https://www.amc.seoul.kr"
ALL_DEPT = BASE + "/asan/common/dept/allDept.do?drUseYn=Y&cmeUseYn=N&thUseYn=N&allowDept=N&deptFunc=fnSelectDeptPopup"
LIST = BASE + "/asan/staff/base/staffBaseInfoList.do"
UA = {"User-Agent": "amc-research-radar/1.0 (AMC PR team; weekly)"}

DOCTOR_RE = re.compile(r'class="doctor_name">\s*<a[^>]*>([^<]+)</a>(.*?)</table>', re.S)
DEPT_LINK_RE = re.compile(r'<a href="/asan/depts/(D\d+)/[^"]*"[^>]*>([^<]+)</a>')


def norm_dept(d):
    """'간이식ㆍ간담도외과'와 '간이식·간담도외과'처럼 가운뎃점·띄어쓰기만 다른 것을 같게."""
    return re.sub(r"[ㆍ·・\s]", "", d or "")


def is_clinical(name):
    """센터·병원 단위는 빼고 진료과만. (어린이병원 아래 '소아…과'는 진료과다)"""
    return not (name.endswith("센터") or name.endswith("병원"))


def fetch_staff(log=print):
    r = requests.get(ALL_DEPT, headers=UA, timeout=30)
    r.raise_for_status()
    codes = {}
    for c, n in re.findall(r"\('(D\d+)'\)[^>]*>\s*(?:<[^>]+>\s*)*([^<]+?)\s*<", r.text):
        codes.setdefault(c, n.strip())
    depts = {c: n for c, n in codes.items() if is_clinical(n)}
    people = {}
    for i, (code, name) in enumerate(sorted(depts.items())):
        try:
            h = requests.get(LIST, params={"searchHpCd": code}, headers=UA, timeout=30).text
        except Exception as e:
            log(f"  의료진 명단 {code} {name} 실패: {type(e).__name__}")
            continue
        for ko, block in DOCTOR_RE.findall(h):
            ko = ko.strip()
            # 홈페이지는 가운뎃점을 'ㆍ'(한글 아래아)로 쓴다 — 화면·논문 쪽 '·'에 맞춘다
            ds = [n.strip().replace("ㆍ", "·") for c, n in DEPT_LINK_RE.findall(block) if is_clinical(n.strip())]
            key = (ko, tuple(ds) or (name,))
            if key not in people:
                people[key] = {"이름": ko, "진료과": ds or [name.replace("ㆍ", "·")]}
        time.sleep(1)
    staff = sorted(people.values(), key=lambda x: (x["진료과"][0], x["이름"]))
    log(f"  의료진 명단: 진료과 {len(depts)}곳 · {len(staff)}명")
    return staff


def load_staff(log=print):
    """일주일 안에 받은 명단이 있으면 그것을, 없으면 새로 받는다. 실패하면 지난 명단."""
    old = None
    if STAFF_PATH.exists():
        try:
            old = json.loads(STAFF_PATH.read_text(encoding="utf-8"))
        except Exception:
            old = None
    now = datetime.now(KST)
    if old and old.get("받은날", "") >= (now - timedelta(days=STAFF_DAYS)).strftime("%Y-%m-%d"):
        return old["의료진"], "지난 명단 사용"
    try:
        staff = fetch_staff(log)
        if len(staff) < 300:                       # 페이지 모양이 바뀌어 거의 못 읽었으면 지난 것을 쓴다
            raise ValueError(f"명단이 너무 적음({len(staff)}명)")
    except Exception as e:
        log(f"  의료진 명단 받기 실패 — {type(e).__name__}: {e}")
        return (old or {}).get("의료진", []), f"받기 실패({type(e).__name__}) — 지난 명단 사용"
    STAFF_PATH.parent.mkdir(exist_ok=True)
    STAFF_PATH.write_text(json.dumps({"받은날": now.strftime("%Y-%m-%d"), "출처": LIST, "의료진": staff},
                                     ensure_ascii=False, indent=0), encoding="utf-8", newline="\n")
    return staff, "새로 받음"


def dept_ok(ours, site):
    """논문 쪽 진료과(우리말)와 홈페이지 진료과가 같은 과로 볼 만한가."""
    a, b = norm_dept(ours), norm_dept(site)
    if not a or not b:
        return False
    if a == b or a.endswith(b) or b.endswith(a):      # 흉부외과 ↔ 심장혈관흉부외과
        return True
    if a == "내과" and b.endswith("내과"):              # 소속을 '내과'로만 적는 교수가 많다
        return True
    if a == "외과" and b.endswith("외과"):
        return True
    if a in ("소아청소년과", "소아과") and b.startswith("소아"):
        return True
    return False


def match_staff(papers, staff):
    """영문 이름 → {한글, 진료과}. 같은 과에서 한 명만 맞을 때만."""
    # 저자별로 논문에 적힌 진료과(우리말)를 모은다
    authors = {}
    for p in papers:
        for a in (p.get("주도저자") or []):
            if a.get("이름"):
                authors.setdefault(a["이름"], set()).add(dept_ko(a.get("진료과", "")))
    out, why = {}, {"과일치": 0, "과정보없음": 0, "후보없음": 0, "여럿": 0, "과불일치": 0}
    for en, depts in authors.items():
        depts.discard("")
        cands = [s for s in staff if same_person(s["이름"], en)]
        if not cands:
            why["후보없음"] += 1
            continue
        if depts:
            hit = [s for s in cands if any(dept_ok(d, sd) for d in depts for sd in s["진료과"])]
            names = {s["이름"] for s in hit}
            if len(names) == 1:
                out[en] = {"한글": hit[0]["이름"], "진료과": hit[0]["진료과"][0]}
                why["과일치"] += 1
            elif hit:
                why["여럿"] += 1
            else:
                why["과불일치"] += 1
        else:
            names = {s["이름"] for s in cands}
            if len(names) == 1 and len(cands) == 1:
                out[en] = {"한글": cands[0]["이름"], "진료과": cands[0]["진료과"][0]}
                why["과정보없음"] += 1
            else:
                why["여럿"] += 1
    return out, why
