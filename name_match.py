"""한글 이름 → 영문 표기 후보. 보도자료 의료진(김남국)과 논문 저자명(Nam-Kug Kim)을 맞추는 데 쓴다.

같은 이름도 사람마다 영문 표기가 달라서(영 → Young/Yeong, 이 → Lee/Yi/Rhee) 음절마다 흔한 표기를
모두 만들어 두고, 논문 저자의 (성, 이름)이 그중 하나와 같으면 같은 사람으로 본다.
동명이인은 여기서 가르지 않는다 — 날짜 범위와 마지막 판정(press_match.py)이 가른다.
"""
import itertools
import re

CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo",
        "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p",
        "t", "t", "ng", "t", "t", "k", "t", "p", "t"]

CHO_ALT = {"g": ["g", "k"], "d": ["d", "t"], "b": ["b", "p"], "j": ["j", "ch"], "s": ["s", "sh"],
           "r": ["r", "l"], "kk": ["kk", "gg", "k"], "tt": ["tt", "dd", "t"], "pp": ["pp", "bb", "p"],
           "ss": ["ss", "s"], "jj": ["jj", "j"], "ch": ["ch", "c"]}
JUNG_ALT = {"eo": ["eo", "u", "o", "eu"], "yeo": ["yeo", "yu", "yo", "you"], "u": ["u", "oo", "woo", "wu"],
            "eu": ["eu", "u"], "ui": ["ui", "eui", "ee", "i"], "i": ["i", "ee", "y"], "ae": ["ae", "e", "ai"],
            "ye": ["ye", "ae", "e", "hye"], "oe": ["oe", "oi", "wi", "we"], "yu": ["yu", "yoo", "you"],
            "o": ["o", "oh"], "wo": ["wo", "won"]}
JONG_ALT = {"k": ["k", "g"], "p": ["p", "b"], "t": ["t", "s", "d"], "l": ["l", "r"]}

# 성은 관습 표기가 따로 있다 (이 → Lee, 박 → Park …)
SURNAME = {
    "김": ["kim", "gim"], "이": ["lee", "yi", "rhee", "ri", "lie", "i"], "박": ["park", "pak", "bak"],
    "최": ["choi", "choe", "chae"], "정": ["jung", "jeong", "chung", "joung", "cheong"], "강": ["kang", "gang"],
    "조": ["cho", "jo", "joh"], "윤": ["yoon", "yun"], "장": ["jang", "chang"], "임": ["lim", "im", "yim", "rim"],
    "한": ["han"], "오": ["oh", "o"], "서": ["seo", "suh", "so"], "신": ["shin", "sin"], "권": ["kwon", "gwon"],
    "황": ["hwang"], "안": ["ahn", "an"], "송": ["song"], "류": ["ryu", "yoo", "yu", "ryoo", "lyu"],
    "유": ["yoo", "yu", "ryu", "you"], "전": ["jeon", "jun", "chun", "jeun"], "홍": ["hong"], "고": ["ko", "go", "koh"],
    "문": ["moon", "mun"], "양": ["yang"], "손": ["son", "sohn"], "배": ["bae", "pae"], "백": ["baek", "paik", "back", "baik", "paek"],
    "허": ["heo", "hur", "huh"], "남": ["nam"], "심": ["shim", "sim"], "노": ["noh", "roh", "no", "ro"], "하": ["ha"],
    "곽": ["kwak", "gwak"], "성": ["sung", "seong", "song"], "차": ["cha"], "주": ["joo", "ju", "chu"],
    "우": ["woo", "wu", "u"], "구": ["koo", "ku", "gu"], "민": ["min"], "진": ["jin", "chin"], "나": ["na", "ra"],
    "지": ["ji", "chi"], "엄": ["um", "eom", "uhm"], "채": ["chae"], "원": ["won"], "천": ["chun", "cheon"],
    "방": ["bang", "pang"], "공": ["kong", "gong"], "현": ["hyun", "hyeon"], "함": ["ham"], "변": ["byun", "byeon", "pyun"],
    "염": ["yeom", "yum", "youm"], "여": ["yeo", "yoh", "yo"], "추": ["choo", "chu"], "도": ["do", "doh"],
    "석": ["seok", "suk"], "선": ["sun", "seon"], "설": ["seol", "sul"], "마": ["ma"], "길": ["kil", "gil"],
    "연": ["yeon", "yun", "youn"], "위": ["wi", "wee"], "표": ["pyo"], "명": ["myung", "myeong"], "기": ["ki", "gi"],
    "반": ["ban"], "왕": ["wang"], "금": ["keum", "kum"], "옥": ["ok"], "육": ["yook", "yuk"], "인": ["in"],
    "맹": ["maeng"], "제": ["je"], "모": ["mo"], "탁": ["tak"], "국": ["kook", "kuk"], "은": ["eun"],
    "편": ["pyun", "pyeon"], "용": ["yong"], "예": ["ye", "yeh"], "경": ["kyung", "gyeong"], "봉": ["bong"],
    "사": ["sa"], "부": ["boo", "bu"], "소": ["so"], "라": ["ra", "la"], "어": ["eo", "uh"], "시": ["si", "shi"],
}


def syllable_variants(ch):
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return [ch]
    cho, jung, jong = CHO[code // 588], JUNG[(code % 588) // 28], JONG[code % 28]
    out = set()
    for c in CHO_ALT.get(cho, [cho]):
        for v in JUNG_ALT.get(jung, [jung]):
            for f in JONG_ALT.get(jong, [jong]):
                s = c + v + f
                out.add(s)
                if c == "" and v.startswith(("eo", "u")) and f == "ng":   # 영→young, 용→yong 은 위에서, 웅→woong
                    out.add("w" + s)
    # 자주 쓰는 관습 표기
    if ch == "영":
        out |= {"young", "yeong", "yong"}
    if ch == "현":
        out |= {"hyun", "hyeon", "hyoun"}
    if ch == "석":
        out |= {"seok", "suk", "sok"}
    if ch == "준":
        out |= {"jun", "joon", "june"}
    if ch == "희":
        out |= {"hee", "hui", "heui", "hi"}
    if ch == "의":
        out |= {"ui", "eui", "ee"}
    return sorted(out)


def norm_en(s):
    return re.sub(r"[^a-z]", "", s.lower())


_cache = {}


def name_keys(ko):
    """'김남국' → {('kim', 'namkuk'), ('kim', 'namkug'), ...}"""
    if ko in _cache:
        return _cache[ko]
    keys = set()
    if 2 <= len(ko) <= 4 and re.fullmatch(r"[가-힣]+", ko):
        sur, given = ko[0], ko[1:]
        surs = SURNAME.get(sur, syllable_variants(sur))
        parts = [syllable_variants(c) for c in given]
        for combo in itertools.product(*parts):
            g = "".join(combo)
            for s in surs:
                keys.add((s, g))
    _cache[ko] = keys
    return keys


def en_key(full):
    """'Nam-Kug Kim' → ('kim', 'namkug')"""
    toks = full.replace(".", " ").split()
    if len(toks) < 2:
        return None
    return norm_en(toks[-1]), norm_en("".join(toks[:-1]))


def same_person(ko, en):
    k = en_key(en)
    return bool(k) and k in name_keys(ko)
