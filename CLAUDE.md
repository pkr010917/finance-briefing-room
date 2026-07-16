# 금융 브리핑 룸

금융업의 거대 트렌드를 포스트잇 블럭 형태로 정리해 보여주는 웹사이트.
매일 뉴스를 휘발성으로 소비하는 대신, 큰 흐름(트렌드) 안에서 개별 뉴스를 이해하는 것이 목적.
원본 아이디어: `/Users/park/옵시디언/원석/금융 브리핑 룸 만들기.md`

## 구조 (빌드 도구 없음 — 순수 HTML/CSS/JS)

디자인: 블룸버그 터미널 스타일 (다크 + 앰버 포인트, IBM Plex Mono/Sans KR).
레이아웃: 왼쪽 트렌드 목록 + 오른쪽 상세의 마스터-디테일 2패널. 손글씨/포스트잇 스타일 금지.

- `index.html` — 페이지 뼈대 (상단 바 + 목록/상세 패널 + 하단 상태 바)
- `style.css` — 터미널 다크 테마 (색 변수는 파일 상단 :root에 정의)
- `data.json` — **트렌드 데이터 (단일 소스). 내용 수정/추가는 이 파일만**
- `script.js` — data.json fetch, 목록/상세 렌더링, 시계, 키보드 탐색
- `scripts/update_briefing.py` — 자동 갱신: 네이버 뉴스 수집 → Claude API로 브리핑 재작성 → data.json 저장
- `.github/workflows/daily-briefing.yml` — 매일 KST 07:00 자동 실행 (GitHub Actions)
- `자동화-설정-가이드.md` — 사용자용 1회 설정 가이드 (API 키 발급, Secrets 등록, Pages)

자동화에 필요한 Secrets: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, ANTHROPIC_API_KEY.
모델은 기본 claude-opus-4-8, 변수 BRIEFING_MODEL로 교체 가능.

## 실행 방법

```
python3 -m http.server 8765
```

브라우저에서 http://localhost:8765 접속.

## 사용자 참고

- 사용자는 코딩 입문자 — 설명은 쉽게, 한국어로
- 트렌드 데이터는 AI가 주기적으로 조사해 `data.js`를 갱신하는 방식
