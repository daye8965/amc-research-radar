"""S2Journal(아산의학도서관) JCR 랭킹 화면을 PDF로 저장한 파일에서 저널 IF를 뽑아 docs/journal_if.json에 합친다.

S2Journal은 내려받기 기능이 없어 화면을 PDF로 인쇄해 넣는다. 한 PDF에 한 화면(최대 100행)이 들어 있다.

    python jcr_import.py Journal1.pdf Journal2.pdf Journal3.pdf

- 행 모양: `2025 <저널명> <ISSN> <분야> <IF> <IF(%)> <Q1>`. 저널이 분야 여러 개에 걸리면 행도 여러 개라
  ISSN으로 합치고 가장 좋은 분야 순위(IF(%)가 작은 쪽)를 남긴다.
- 기존 journal_if.json에 **합친다**. 같은 ISSN은 더 최근 연도가 이긴다.
- PDF 원본은 저장소에 올리지 않는다(.gitignore). journal_if.json에는 저널명·ISSN·IF·상위%·사분위만 남긴다.
"""
import json
import re
import sys
from pathlib import Path

OUT = Path("docs/journal_if.json")
ROW = re.compile(r"(\d{4}) (.+?) (\d{4}-\d{3}[\dX]) (.+?) (\d+\.\d) (\d+\.\d) (Q\d)")


def parse(paths):
    import pypdf
    text = ""
    for p in paths:
        for page in pypdf.PdfReader(p).pages:
            text += page.extract_text() + "\n"
    text = re.sub(r"\s+", " ", text)
    rows = {}
    for m in ROW.finditer(text):
        year, title, issn, cat, jif, pct, q = m.groups()
        title = re.split(r" (?:19|20)\d\d ", title)[-1].strip()   # 앞 행 꼬리가 붙는 경우를 끊는다
        rec = {"저널": title, "IF": float(jif), "상위": float(pct), "사분위": q, "분야": cat.strip(), "연도": int(year)}
        cur = rows.get(issn)
        if cur is None or (rec["연도"], -rec["상위"]) > (cur["연도"], -cur["상위"]):
            rows[issn] = rec
    return rows


def main(paths):
    if not paths:
        print(__doc__)
        return 1
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"저널": {}}
    table = old.get("저널", {})
    new = parse(paths)
    added = 0
    for issn, rec in new.items():
        cur = table.get(issn)
        if cur is None or rec["연도"] >= cur["연도"]:
            added += cur is None
            table[issn] = rec
    years = sorted({r["연도"] for r in table.values()})
    OUT.write_text(json.dumps({"출처": "Clarivate JCR (S2Journal, 울산대학교 아산의학도서관)",
                               "연도": years, "건수": len(table), "저널": table},
                              ensure_ascii=False, indent=0), encoding="utf-8", newline="
")
    print(f"PDF에서 {len(new)}개 · 새로 {added}개 · 합계 {len(table)}개 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
