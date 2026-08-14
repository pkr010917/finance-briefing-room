"""
금융 데일리 뉴스레터 → 텔레그램 자동 발송 봇
- Anthropic API(웹 검색 도구 포함)로 뉴스레터 생성
- 텔레그램 봇 API로 발송
- data/history.json에 과거 주제를 저장해 중복 주제 방지
- 리서치한 기사를 거시 트렌드별로 분류해 data/trends.json에 축적
  (분류 결과는 웹사이트의 '관련 기사' 목록에 표시됨)

필요 환경변수:
  ANTHROPIC_API_KEY  : Anthropic API 키
  TELEGRAM_BOT_TOKEN : 텔레그램 봇 토큰 (BotFather에서 발급)
  TELEGRAM_CHAT_ID   : 뉴스레터를 받을 채팅 ID
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import requests

# ────────────────────────── 설정 ──────────────────────────
MODEL = "claude-sonnet-5"        # 품질/비용 균형이 좋은 모델
MAX_TOKENS = 8000                # 뉴스레터가 길기 때문에 넉넉하게
# 검색 1회마다 기사 본문이 대화에 쌓이고 다음 호출 때 전부 다시 전송되므로,
# 검색 횟수가 비용에 가장 큰 영향을 줍니다. 비용이 부담되면 4로 낮추세요.
MAX_WEB_SEARCHES = 5             # 1회 실행당 최대 검색 횟수 (비용 통제)
DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"
TRENDS_FILE = DATA_DIR / "trends.json"
HISTORY_DAYS = 14                # 최근 14일 주제를 중복 방지에 사용
MAX_ARTICLES_PER_TREND = 20      # 트렌드당 축적할 기사 수 (오래된 것부터 삭제)
MAX_UNCLASSIFIED = 30            # 미분류 기사 보관 수
KST = timezone(timedelta(hours=9))

TELEGRAM_MSG_LIMIT = 4096        # 텔레그램 메시지 글자수 제한

# 뉴스레터 맨 끝에 붙는 브리핑 룸 웹사이트 링크
SITE_URL = "https://pkr010917.github.io/finance-briefing-room/"
SITE_FOOTER = f"\n\n🖥 브리핑 룸에서 트렌드별로 모아보기\n{SITE_URL}"


# ────────────────────────── 과거 주제 관리 ──────────────────────────
def load_recent_topics() -> list[str]:
    """최근 HISTORY_DAYS일 동안 다룬 주제 목록을 반환."""
    if not HISTORY_FILE.exists():
        return []
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    cutoff = datetime.now(KST) - timedelta(days=HISTORY_DAYS)
    topics: list[str] = []
    for entry in history:
        try:
            entry_date = datetime.fromisoformat(entry["date"])
        except (KeyError, ValueError):
            continue
        if entry_date.tzinfo is None:
            entry_date = entry_date.replace(tzinfo=KST)
        if entry_date >= cutoff:
            topics.extend(entry.get("topics", []))
    return topics


def save_topics(topics: list[str]) -> None:
    """오늘 다룬 주제를 history.json에 추가 저장."""
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []
    history.append({
        "date": datetime.now(KST).isoformat(),
        "topics": topics,
    })
    # 파일이 무한정 커지지 않도록 최근 60개 항목만 유지
    history = history[-60:]
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ────────────────────────── 뉴스레터 생성 ──────────────────────────
def build_prompt(recent_topics: list[str], trend_titles: list[str]) -> str:
    today = datetime.now(KST).strftime("%Y년 %m월 %d일 (%a)")
    topics_block = (
        "\n".join(f"- {t}" for t in recent_topics)
        if recent_topics
        else "(없음 — 오늘이 첫 호)"
    )
    trends_block = "\n".join(f"- {t}" for t in trend_titles)
    return f"""오늘은 {today}입니다. 한국 금융권 데일리 뉴스레터를 작성해주세요.

## 1단계: 뉴스 리서치
먼저 web_search 도구로 반드시 검색하세요. 기억에 의존해 답하지 말고, 오늘 검색한 기사만 근거로 쓰세요.
아래 세 영역을 각각 최소 1회씩 검색하고, 검색 결과가 부실한 영역만 한 번 더 검색하세요 (총 {MAX_WEB_SEARCHES}회 이내).
1. 시중은행 동향 (KB국민·신한·하나·우리·NH농협 등)
2. 금융권 규제·정책당국 동향 (금융위원회, 금융감독원, 한국은행 등)
3. 금융권 채용·취업 공고 (은행·금융공기업 신입/인턴 공고, 채용 박람회, 마감 임박 일정)

## 중복 방지 규칙 (중요)
아래는 최근 {HISTORY_DAYS}일간 이미 다룬 주제입니다. 동일한 주제는 다시 다루지 마세요.
{topics_block}

단, 지속적인 팔로업이 필요한 중요 주제(예: 진행 중인 입법, 금리 사이클)는 [팔로업] 태그를 달아 "새로운 진전이 있을 때만" 포함할 수 있습니다.
과거 맥락(인과관계)이 이해에 필요한 기사는 과거 내용을 추가 리서치해 [배경·과거] 태그로 표시하고, 최신 뉴스에는 [최신] 태그를 다세요.
정말 새 주제가 없어 과거 내용을 써야 한다면 반드시 [과거뉴스] 태그로 표시하세요.

## 2단계: 뉴스레터 작성
아래 구성으로, 내용을 최대한 알차고 풍부하게 작성하세요. 각 기사마다 핵심 수치와 배경, 의미(So What)를 포함하세요.
1️⃣ 주요 시중은행 동향
2️⃣ 금융 정책·규제
3️⃣ 금융권 채용·취업 (마감일 명시)
4️⃣ 오늘의 면접포인트 — 오늘 뉴스 중 면접에 나올 만한 주제 1개를 골라 답변 프레임과 예상 꼬리질문까지 제시

## 출력 형식 (텔레그램용)
- 제목: 📬 금융 데일리 브리핑 — {today}
- 텔레그램에서 읽기 좋게 이모지와 짧은 단락 사용
- 마크다운 특수문자(*, _, #, [ ] 등)는 사용하지 말고 일반 텍스트로 작성 (이모지는 OK, [태그]는 예외)
- 각 기사 제목 끝에 그 기사가 속한 거시 트렌드를 [트렌드명] 태그로 붙이세요.
  아래 3단계의 거시 트렌드 목록에서 고르고, 3단계에서 분류할 trend 값과 반드시 일치시키세요.
  어느 트렌드에도 해당하지 않는 기사(예: 채용 공고)는 트렌드 태그를 붙이지 마세요.
  예: 한은, 기준금리 동결 결정 [금리 사이클 전환]
- 맨 마지막 줄에, 오늘 다룬 주제들을 아래 형식으로 정리 (이 부분은 발송 전 자동 제거됨):
<topics>
주제1
주제2
...
</topics>

## 3단계: 기사 분류 (웹사이트 아카이브용 — 발송 전 자동 제거됨)
<topics> 블록 다음 줄에, 오늘 리서치에서 실제로 참고한 기사들을 아래 JSON 형식으로 정리하세요.
각 기사의 trend 값은 아래 거시 트렌드 목록 중 가장 잘 맞는 것의 제목을 "그대로" 쓰고,
어디에도 맞지 않으면(예: 개별 채용 공고) "기타"라고 쓰세요.

거시 트렌드 목록:
{trends_block}

<articles>
[
  {{"title": "기사 제목", "url": "https://...", "press": "언론사 도메인(예: yna.co.kr)", "trend": "트렌드 제목 또는 기타"}}
]
</articles>"""


def generate_newsletter(client: anthropic.Anthropic, prompt: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # Sonnet 5부터는 이 설정을 생략하면 '생각하기'가 자동으로 켜져서
        # MAX_TOKENS를 생각에 써버리고 본문이 잘릴 수 있어 명시적으로 끕니다.
        # (대신 프롬프트 1단계에서 검색을 반드시 하도록 지시)
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
        tools=[{
            # _20260209 버전은 검색 결과를 대화에 넣기 전에 자동으로 걸러내
            # 토큰 사용량을 줄여줍니다 (Sonnet 4.6 이상에서 사용 가능).
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": MAX_WEB_SEARCHES,
            "user_location": {
                "type": "approximate",
                "country": "KR",
                "timezone": "Asia/Seoul",
            },
        }],
    )
    # 텍스트 블록만 이어붙임 (검색 결과 블록 등은 제외)
    text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    if not text.strip():
        raise RuntimeError(f"모델 응답에 텍스트가 없습니다: {response}")
    return text


def extract_topics(newsletter: str) -> tuple[str, list[str], list[dict]]:
    """<topics>·<articles> 블록을 분리해 (발송용 본문, 주제 리스트, 기사 리스트)를 반환."""
    articles: list[dict] = []
    articles_match = re.search(r"<articles>(.*?)</articles>", newsletter, re.DOTALL)
    if articles_match:
        try:
            parsed = json.loads(articles_match.group(1).strip())
            if isinstance(parsed, list):
                articles = [a for a in parsed if isinstance(a, dict)]
        except json.JSONDecodeError:
            print("⚠️  <articles> 블록 파싱 실패 — 오늘은 기사 축적을 건너뜁니다")

    topics: list[str] = []
    topics_match = re.search(r"<topics>(.*?)</topics>", newsletter, re.DOTALL)
    if topics_match:
        topics = [
            t.strip() for t in topics_match.group(1).strip().splitlines() if t.strip()
        ]

    # 본문 = 첫 번째 블록이 시작되기 전까지
    cut_positions = [m.start() for m in (topics_match, articles_match) if m]
    body = newsletter[: min(cut_positions)] if cut_positions else newsletter
    return body.strip(), topics, articles


# ────────────────────────── 트렌드별 기사 축적 ──────────────────────────
def load_trend_titles() -> list[str]:
    """data/trends.json에서 트렌드 제목 목록을 읽는다 (분류 기준으로 사용)."""
    try:
        data = json.loads(TRENDS_FILE.read_text(encoding="utf-8"))
        return [t["title"] for t in data["trends"]]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def merge_articles_into_trends(articles: list[dict]) -> None:
    """분류된 기사를 data/trends.json의 각 트렌드에 추가 (URL 중복 제거, 개수 제한)."""
    if not articles:
        return
    try:
        data = json.loads(TRENDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("⚠️  data/trends.json을 읽을 수 없어 기사 축적을 건너뜁니다")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    trends_by_title = {t["title"]: t for t in data["trends"]}
    data.setdefault("unclassified", [])
    added = 0

    for article in articles:
        title = str(article.get("title", "")).strip()
        url = str(article.get("url", "")).strip()
        if not title or not url.startswith("http"):
            continue
        entry = {
            "title": title,
            "press": str(article.get("press", "")).strip() or "언론사",
            "url": url,
            "date": today,
        }
        trend = trends_by_title.get(str(article.get("trend", "")).strip())
        bucket = trend["articles"] if trend else data["unclassified"]
        if any(a.get("url") == url for a in bucket):
            continue  # 이미 축적된 기사
        bucket.insert(0, entry)
        added += 1

    for trend in data["trends"]:
        trend["articles"] = trend["articles"][:MAX_ARTICLES_PER_TREND]
    data["unclassified"] = data["unclassified"][:MAX_UNCLASSIFIED]

    TRENDS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"   기사 {added}건을 트렌드 아카이브에 추가")


# ────────────────────────── 텔레그램 발송 ──────────────────────────
def split_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """4096자 제한에 맞춰 단락(빈 줄) 기준으로 분할."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # 단락 하나가 제한보다 길면 강제 분할
            while len(paragraph) > limit:
                chunks.append(paragraph[:limit])
                paragraph = paragraph[limit:]
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def send_to_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for i, chunk in enumerate(split_message(text), start=1):
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(
                f"텔레그램 발송 실패 (메시지 {i}): {resp.status_code} {resp.text}"
            )
        print(f"텔레그램 발송 완료 ({i}번째 메시지, {len(chunk)}자)")


# ────────────────────────── 메인 ──────────────────────────
def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [
        name for name, value in [
            ("ANTHROPIC_API_KEY", api_key),
            ("TELEGRAM_BOT_TOKEN", bot_token),
            ("TELEGRAM_CHAT_ID", chat_id),
        ] if not value
    ]
    if missing:
        sys.exit(f"환경변수가 설정되지 않았습니다: {', '.join(missing)}")

    client = anthropic.Anthropic(api_key=api_key)

    print("1) 과거 주제·트렌드 목록 로드 중...")
    recent_topics = load_recent_topics()
    trend_titles = load_trend_titles()
    print(f"   최근 {HISTORY_DAYS}일 주제 {len(recent_topics)}건, 트렌드 {len(trend_titles)}개")

    print("2) 뉴스레터 생성 중... (리서치 포함, 1~3분 소요)")
    raw = generate_newsletter(client, build_prompt(recent_topics, trend_titles))

    body, topics, articles = extract_topics(raw)
    print(f"   생성 완료: 본문 {len(body)}자, 주제 {len(topics)}건, 기사 {len(articles)}건")

    print("3) 텔레그램 발송 중...")
    send_to_telegram(bot_token, chat_id, body + SITE_FOOTER)

    if topics:
        save_topics(topics)
        print("4) 주제 기록 저장 완료 (data/history.json)")

    print("5) 기사를 트렌드별로 축적 중... (data/trends.json)")
    merge_articles_into_trends(articles)

    print("✅ 모든 작업 완료")


if __name__ == "__main__":
    main()
