# 금융 브리핑 룸

금융업의 거대 트렌드를 정리해 보여주는 웹사이트 + 텔레그램 데일리 뉴스레터 봇 (통합 저장소).
매일 뉴스를 휘발성으로 소비하는 대신, 큰 흐름(트렌드) 안에서 개별 뉴스를 이해하는 것이 목적.
원본 아이디어: `/Users/park/옵시디언/원석/금융 브리핑 룸 만들기.md`
(기존 telegram-finance-newsletter 저장소를 2026-07-21 이곳으로 통합함)

## 구조 (빌드 도구 없음 — 순수 HTML/CSS/JS + Python 스크립트)

디자인: 블룸버그 터미널 스타일 (다크 + 앰버 포인트, IBM Plex Mono/Sans KR).
레이아웃: 왼쪽 트렌드 목록 + 오른쪽 상세의 마스터-디테일 2패널. 손글씨/포스트잇 스타일 금지.

- `index.html` — 페이지 뼈대 (상단 바 + 목록/상세 패널 + 하단 상태 바)
- `style.css` — 터미널 다크 테마 (색 변수는 파일 상단 :root에 정의)
- `data/trends.json` — **트렌드 데이터 (단일 소스). 내용 수정/추가는 이 파일만**
  - 트렌드별 `articles`는 매일 축적됨 (URL 중복 제거, 트렌드당 최근 20건 유지)
  - 어느 트렌드에도 안 맞는 기사는 최상위 `unclassified`에 보관 (사이트에는 미표시)
- `data/history.json` — 뉴스레터가 최근 다룬 주제 기록 (중복 주제 방지용)
- `script.js` — data/trends.json fetch, 목록/상세 렌더링, 시계, 키보드 탐색
- `scripts/newsletter_bot.py` — 뉴스레터: Claude 웹 검색 리서치 → 텔레그램 발송 → 기사를 트렌드별로 분류해 축적
- `scripts/update_briefing.py` — 브리핑 갱신: 네이버 뉴스 수집 → Claude API로 트렌드 설명 재작성 → 기사 축적
- `.github/workflows/daily.yml` — 평일 KST 07:00 통합 실행: 뉴스레터 → 브리핑 갱신 → data/ 커밋
- `자동화-설정-가이드.md` — 사용자용 1회 설정 가이드 (API 키 발급, Secrets 등록, Pages)

자동화에 필요한 Secrets: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, ANTHROPIC_API_KEY,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.

## 모델·비용 (2026-08-14 기준)

- 뉴스레터: claude-sonnet-5, 생각하기 끔(`thinking: disabled`), 웹검색 `web_search_20260209` 5회
  - Sonnet 5는 thinking을 생략하면 자동으로 켜져 MAX_TOKENS를 잡아먹으므로 **명시적으로 꺼야 함**
  - 검색 결과가 대화에 누적되어 매 호출마다 재전송되므로 **검색 횟수가 비용의 최대 변수**
- 브리핑: claude-opus-5, `effort: medium` (변수 BRIEFING_MODEL / BRIEFING_EFFORT로 교체 가능)
- 실측 운영비: 평일 하루 약 $0.42, 월 약 $9~10. 비용은 console.anthropic.com → Usage에서 확인
- **잔액이 0이 되면 Actions가 매일 조용히 실패한다** (2026-08-05~08-14 10일간 중단된 전례). 월 1회 잔액 확인 권장

## 실행 방법

```
python3 -m http.server 8765
```

브라우저에서 http://localhost:8765 접속.

## 사용자 참고

- 사용자는 코딩 입문자 — 설명은 쉽게, 한국어로
- 트렌드 데이터는 AI가 주기적으로 조사해 `data.js`를 갱신하는 방식
