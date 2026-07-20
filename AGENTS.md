# 금융 브리핑 룸

금융업의 거대 트렌드를 블룸버그 터미널 스타일로 보여주는 웹사이트.
매일 뉴스를 휘발성으로 소비하는 대신, 큰 흐름(트렌드) 안에서 개별 뉴스를 이해하는 것이 목적.
운영 중인 사이트: https://pkr010917.github.io/finance-briefing-room/

## 구조 (빌드 도구 없음 — 순수 HTML/CSS/JS + Python 스크립트)

디자인: 블룸버그 터미널 스타일 (다크 + 앰버 포인트, IBM Plex Mono/Sans KR).
레이아웃: 왼쪽 트렌드 목록 + 오른쪽 상세의 마스터-디테일 2패널. 손글씨/포스트잇 스타일 금지.

- `index.html` — 페이지 뼈대 (상단 바 + 목록/상세 패널 + 하단 상태 바)
- `style.css` — 터미널 다크 테마 (색 변수는 파일 상단 :root에 정의)
- `data.json` — **트렌드 데이터 (단일 소스). 내용 수정/추가는 이 파일만**
- `script.js` — data.json fetch, 목록/상세 렌더링, 시계, 키보드 탐색
- `scripts/update_briefing.py` — 자동 갱신: 네이버 뉴스 수집 → Claude API로 브리핑 재작성 → data.json 저장
- `.github/workflows/daily-briefing.yml` — 매일 KST 07:00 자동 실행 (GitHub Actions)
- `자동화-설정-가이드.md` — 사용자용 1회 설정 가이드

자동화 Secrets: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, ANTHROPIC_API_KEY.
브리핑 작성 모델은 기본 claude-opus-4-8, 저장소 변수 BRIEFING_MODEL로 교체 가능.

## 실행 방법

```
python3 -m http.server 8765
```

브라우저에서 http://localhost:8765 접속.

## 작업 시 주의

- GitHub Actions 봇이 매일 data.json에 커밋함 → **로컬 수정 전 반드시 pull 먼저**
- 사용자는 코딩 입문자 — 설명은 쉽게, 한국어로. push는 사용자가 GitHub Desktop으로 직접 함
- data.json의 `query` 필드가 네이버 뉴스 검색어. 트렌드 추가/삭제는 data.json만 고치면 됨
