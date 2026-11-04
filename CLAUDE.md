# amc-research-radar

서울아산병원 홍보팀 언론Unit의 **연구성과 레이더**입니다. 서울아산병원 소속 저자의 PubMed 신규 논문을
매일 모아 **보도자료 후보**를 골라 보여 주는 정적 페이지입니다. 교수님이 먼저 연락해 오기를 기다리지 않고
우리 논문을 먼저 찾아가려는 것입니다.

- 공개 주소: https://daye8965.github.io/amc-research-radar/
- 2026-11-04에 언론 대시보드(`../hospital-news-dashboard-dev`) 탭으로 먼저 만들었다가 **별도 저장소로 옮겼습니다.**
  처음엔 텔레그램 알림도 이야기됐지만 **페이지로만** 만들었습니다.

## 구조

| 파일 | 역할 |
|---|---|
| `research_radar.py` | PubMed·OpenAlex 수집, 등급·후보 판정, 구글 시트의 보도자료 표시 → `docs/research.json` |
| `docs/index.html` | 페이지 본체 (단일 파일, `research.json`을 읽어 렌더링). 외부 의존은 `xlsx-js-style`(엑셀) 하나 |
| `docs/research.json` | 수집 결과. **사람이 직접 편집하지 말 것** — 표시는 시트에서 합니다 |
| `.github/workflows/research.yml` | 매일 1회 수집·커밋. 수동 실행 때 `days`로 다시 훑을 기간을 정합니다 |

## 수집 (`research_radar.py`)

- PubMed E-utilities, 소속에 `Asan Medical Center`·`Asan Institute for Life Sciences`·
  `University of Ulsan College of Medicine`이 있는 논문을 **PubMed 등록일(edat)** 기준 최근
  `RESEARCH_DAYS`(워크플로 기본 10일)만큼 다시 훑습니다. 같은 PMID는 덮어쓰고 `RESEARCH_KEEP_DAYS`(365일) 넘으면 지웁니다.
  1년치 2,526건 중 우리 저자가 있는 논문 2,330건(2026-11-04).
- **울산의대만 적힌 저자도 우리입니다**(울산의대 = 서울아산병원 교수). 다만 울산대병원·강릉아산병원도
  같은 의대라 저자 판정(`is_amc`)은 **같은 소속 칸에 `Seoul`이 있을 때만** 인정합니다.
  저자 중 아무도 안 걸리면 논문을 버립니다.
- PubMed는 교신저자를 표시하지 않아 **소속 칸에 이메일을 적은 저자**를 교신으로 추정합니다.
- 키 없이 PubMed는 초당 3건입니다. `NCBI_API_KEY`(선택)를 주면 10건으로 늘어납니다.

### 저널 등급

Clarivate IF는 무료 API가 없어 **OpenAlex `2yr_mean_citedness`** 를 씁니다(IF와 같은 정의).
**값이 IF보다 작게 나옵니다**(Lancet IF 88 → 21.6, NEJM → 33.2). 문턱을 IF 숫자로 생각하면 안 됩니다.

- S = `TOP_S`(NEJM·Lancet·JAMA·BMJ·Nature·Science·Cell·Nat Med) 또는 20 이상
- A = `TOP_A`·`TOP_A_PREFIX`(Lancet·JAMA 가족 등) 또는 10 이상
- B = 6 이상
- **접두어에 `Nat `을 넣으면 안 됩니다** — `Nat Prod Res` 같은 저널이 딸려 옵니다.
- 지표는 ISSN별로 90일 캐시합니다(`research.json`의 `저널`).

### 후보 · 참여

- **후보** = S·A + 우리가 제1·교신·마지막 저자 + 연구 논문(`NON_RESEARCH` 유형 아님). 1년치 71편.
- **참여** = S 저널에 공동저자로만 들어간 연구 논문. 1년치 24편 — NEJM 3·Nat Med 4·Lancet 1편이 **전부 이쪽**이었습니다.
  다기관 임상의 참여 기관이라 우리가 주도한 연구는 아니지만, 저널 무게 때문에 따로 보여 줍니다.
- 정렬용 `점수` = 등급(S 60·A 40·B 20) + 지표 절반(최대 20) + 교신 15·마지막 12·제1 10, 연구 논문 아니면 −50.

## 보도자료 표시 (구글 시트 `연구성과` 탭)

시트(`RESEARCH_SHEET_ID`)의 `연구성과` 탭, `A:D` = `PMID · 상태 · 배포일 · 메모`를 서비스 계정으로 읽습니다.

**저장소와 페이지가 공개라 표시를 저장소 파일에 두지 않습니다** — CSV로 두면 배포 전 연구가 그대로 드러납니다.
화면에 내보내는 값은 셋뿐입니다(`load_marks`).

- 배포일이 오늘 이전이거나 상태가 `배포` → **배포** (+배포일)
- `제외`·`보류`·`안함`·`X` → **보류**
- 그 밖(`검토`·`결정`·빈 칸, 배포일이 미래인 `결정`) → **검토중**. **배포 전 `결정`은 `검토`와 구별되지 않게** 합니다.
- 메모는 내보내지 않습니다. 시트를 못 읽으면 지난 표시를 그대로 둡니다.
- 배포일을 미리 적어 두면 그날이 지난 첫 수집에서 저절로 `배포`로 바뀝니다.
- PMID 칸은 숫자만 뽑아 씁니다(`PMID: 123` 도 됨).

## 화면 (`docs/index.html`)

- 보기 4개: 📣 보도자료 후보(기본) · 🌐 최상위 저널 참여 · 🏷️ 표시한 논문 · 전체. 정렬은 최신순/점수순.
- 필터: 등록일 from/to · 진료과 · 검색(공백 AND, `-단어` 제외 — 제목·저널·저자·진료과·PMID) ·
  `우리가 주도한 논문만`(전체 보기에만 의미).
- `NEW`·`새 후보` = 수집일 기준 **이틀 안**에 PubMed에 등록된 논문.
- 진료과는 소속 칸의 영어 이름이라 `DEPT_KO`로 우리말로 바꿉니다. 없는 건 영어 그대로 나옵니다.
  자주 보이는 영어 이름이 있으면 여기에 더하세요.
- 등급 밖 논문은 초록 결론(`결론`)을 저장하지 않습니다(파일 크기, 1년치 약 1.7MB).
- 엑셀은 지금 보기 그대로 한 장. 서식은 언론 대시보드와 같습니다(나눔고딕 9pt·가운데, 긴 글만 왼쪽).

## Secrets

`GOOGLE_SERVICE_ACCOUNT_JSON`, `RESEARCH_SHEET_ID` — 언론 대시보드의 `GOOGLE_SERVICE_ACCOUNT_JSON`·
`REPORTER_SHEET_ID`와 같은 값을 넣으면 됩니다(같은 시트에 `연구성과` 탭을 둠).
`NCBI_API_KEY`는 선택. 시트 값이 없으면 수집은 되고 표시만 지난 것을 유지합니다.

## 작업 규칙

- 커밋 메시지는 한글 서술형 한 줄. 워크플로의 `자동 수집 …` 커밋과 섞이므로 push 전에 `git pull --rebase`.
- **로컬에서 `requests`가 SSL 오류로 막힙니다**(원내 망의 인증서 가로채기). Actions에서는 문제없습니다.
  로컬에서 돌리려면 윈도 인증서 저장소(`ssl.enum_certificates('ROOT')`)를 PEM으로 내보내 `REQUESTS_CA_BUNDLE`로 주세요.
- 정적 페이지 확인: `python -m http.server 8766` 후 `http://localhost:8766/docs/`
