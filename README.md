# 서울아산병원 연구성과 레이더

PubMed에서 서울아산병원 소속 저자의 신규 논문을 매일 모아, 저널 등급과 저자 역할로 **보도자료 후보**를 골라 보여 주는 페이지입니다.

- 페이지: https://daye8965.github.io/amc-research-radar/
- 수집: `research_radar.py` (PubMed E-utilities + OpenAlex 저널 지표) → `docs/research.json`
- 보도자료 표시: 구글 시트 `연구성과` 탭 (PMID · 상태 · 배포일 · 메모). 배포 전에는 '검토중'으로만 보입니다.

자세한 규칙은 [CLAUDE.md](CLAUDE.md)를 보세요.
