# 서울아산병원 연구성과 레이더

PubMed에서 서울아산병원 소속 저자의 신규 논문을 매일 모아, 저널 등급과 저자 역할로 **보도자료 후보**를 골라 보여 주는 페이지입니다.

- 페이지: https://daye8965.github.io/amc-research-radar/
- 수집: `research_radar.py` (PubMed E-utilities + OpenAlex 저널 지표) → `docs/research.json`
- 보도자료 표시: 우리가 배포한 보도자료 목록과 자동으로 맞춥니다 (의료진 이름 + 날짜로 후보를 좁히고 Claude가 고름). 배포일이 지난 것만.

자세한 규칙은 [CLAUDE.md](CLAUDE.md)를 보세요.
