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

import rss_feeds

# ────────────────────────── 설정 ──────────────────────────
MODEL = "claude-sonnet-5"        # 품질/비용 균형이 좋은 모델
MAX_TOKENS = 8000                # 뉴스레터가 길기 때문에 넉넉하게
# 하이브리드 구조: 은행·정책 뉴스는 RSS로 공짜로 모으고(rss_feeds.py),
# 검색은 RSS가 못 잡는 채용 공고·마감일에만 쓴다. 검색 1회마다 기사 본문이
# 대화에 쌓여 매 호출 재전송되므로 검색 횟수가 비용의 지배 요인이기 때문.
MAX_WEB_SEARCHES = 2             # 채용 정보 전용 검색 횟수
# ⚠️ web_fetch는 아낀 비용을 되돌려놓는다. 가져온 본문이 이후 모든 턴에 재전송되기
# 때문에, 3건×6000토큰을 허용하면 RSS로 줄인 만큼이 그대로 상쇄됐다(추정 $0.39 = 기존과 동일).
# 꼭 필요한 1~2건만, 짧게 읽도록 제한한다.
MAX_WEB_FETCHES = 2              # 핵심 기사 본문 읽기 횟수
FETCH_CONTENT_TOKENS = 2500      # 본문 1건당 가져올 최대 분량
MAX_RSS_IN_PROMPT = 70           # 프롬프트에 넣을 RSS 기사 수 (입력 토큰 통제)
MIN_RSS_ARTICLES = 10            # 이보다 적게 수집되면 피드 이상으로 보고 중단
DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"
TRENDS_FILE = DATA_DIR / "trends.json"
HISTORY_DAYS = 14                # 최근 14일 주제를 중복 방지에 사용
MAX_ARTICLES_PER_TREND = 20      # 트렌드당 축적할 기사 수 (오래된 것부터 삭제)
# 새 트렌드 발굴은 미분류가 25건 이상 쌓여야 시도하고 근거 기사 8건을 요구하므로,
# 보관량이 부족하면 판단할 재료 자체가 모이지 않는다.
MAX_UNCLASSIFIED = 80            # 미분류 기사 보관 수
MAX_RESUMES = 2                  # 검색 루프가 멈췄을 때 이어서 진행할 최대 횟수

# 발송 전 점검 기준 (아래를 못 채우면 실패 처리하고 발송하지 않음)
MIN_BODY_LENGTH = 500            # 정상 뉴스레터는 보통 2000자 이상
REQUIRED_SECTIONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
NEWSLETTER_START = "📬 금융 데일리 브리핑"  # 이 앞의 진행 멘트는 잘라냄
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
def build_prompt(
    recent_topics: list[str], trend_titles: list[str], rss_articles: list[dict]
) -> str:
    today = datetime.now(KST).strftime("%Y년 %m월 %d일 (%a)")
    topics_block = (
        "\n".join(f"- {t}" for t in recent_topics)
        if recent_topics
        else "(없음 — 오늘이 첫 호)"
    )
    trends_block = "\n".join(f"- {t}" for t in trend_titles)
    feed_block = "\n".join(
        f"- [{a['time']}] {a['title']} ({a['press']})\n  {a['summary']}\n  {a['url']}"
        for a in rss_articles
    )
    return f"""오늘은 {today}입니다. 한국 금융권 데일리 뉴스레터를 작성해주세요.

## 1단계: 오늘 수집된 기사 (한경·연합뉴스·연합인포맥스·머니투데이 실시간 피드)
아래는 방금 수집한 최신 기사 {len(rss_articles)}건입니다. 1️⃣2️⃣ 섹션은 **이 목록에서만** 고르세요.
같은 사안을 여러 언론사가 쓴 경우 하나로 합치고, 중요도가 높은 것부터 다루세요.
목록에 없는 내용을 기억으로 채워 넣지 마세요. url은 아래에 적힌 것을 그대로 쓰세요.

{feed_block}

## 2단계: 채용 정보만 웹 검색
위 피드에는 채용 공고가 잘 잡히지 않습니다. **채용 관련해서만** web_search를 최대 {MAX_WEB_SEARCHES}회 쓰세요.
(예: "은행 신입행원 채용 공고", "금융공기업 채용 마감") 은행·정책 뉴스는 검색하지 마세요 — 이미 위에 있습니다.
3️⃣ 섹션은 이 검색 결과로 작성하고, 마감일과 남은 기간(D-N)을 명시하세요.

도구를 쓸 때는 **같은 턴에 한꺼번에** 호출하세요. 한 번에 하나씩 나눠 부르면
그때까지의 대화가 매번 다시 전송되어 비용이 몇 배로 늘어납니다.

## 중복 방지 규칙 (중요)
아래는 최근 {HISTORY_DAYS}일간 이미 다룬 주제입니다. 동일한 주제는 다시 다루지 마세요.
{topics_block}

단, 지속적인 팔로업이 필요한 중요 주제(예: 진행 중인 입법, 금리 사이클)는 [팔로업] 태그를 달아 "새로운 진전이 있을 때만" 포함할 수 있습니다.
과거 맥락(인과관계)이 이해에 필요한 기사는 과거 내용을 추가 리서치해 [배경·과거] 태그로 표시하고, 최신 뉴스에는 [최신] 태그를 다세요.
정말 새 주제가 없어 과거 내용을 써야 한다면 반드시 [과거뉴스] 태그로 표시하세요.

## 3단계: 뉴스레터 작성
아래 구성으로, 내용을 최대한 알차고 풍부하게 작성하세요. 각 기사마다 핵심 수치와 배경, 의미(So What)를 포함하세요.
기본적으로 위 요약문만으로 작성하세요. 그것만으로 So What을 쓸 수 없는 결정적인 기사가 있을 때에만
web_fetch로 본문을 읽으세요 (최대 {MAX_WEB_FETCHES}건, 대개 0~1건이면 충분합니다).
1️⃣ 주요 시중은행 동향
2️⃣ 금융 정책·규제
3️⃣ 금융권 채용·취업 (마감일 명시)
4️⃣ 오늘의 면접포인트 — 오늘 뉴스 중 면접에 나올 만한 주제 1개를 골라 답변 프레임과 예상 꼬리질문까지 제시

## 출력 형식 (텔레그램용)
- 도구를 쓰는 동안 "리서치하겠습니다", "추가로 검색하겠습니다" 같은 진행 상황 설명을 쓰지 마세요.
  조용히 하고, 완성된 뉴스레터만 출력하세요. (독자가 그 과정을 그대로 받아보게 됩니다)
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
    messages = [{"role": "user", "content": prompt}]

    for attempt in range(MAX_RESUMES + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            # Sonnet 5부터는 이 설정을 생략하면 '생각하기'가 자동으로 켜져서
            # MAX_TOKENS를 생각에 써버리고 본문이 잘릴 수 있어 명시적으로 끕니다.
            # (대신 프롬프트 1단계에서 검색을 반드시 하도록 지시)
            thinking={"type": "disabled"},
            messages=messages,
            tools=[
                {
                    # ⚠️ _20260209(자동 필터링) 버전은 내부적으로 코드 실행을 쓰는데,
                    # 그 작업이 max_uses 한도를 함께 소모해 모델이 검색을 시작하기도 전에
                    # 한도에 걸려 헛도는 문제가 있었습니다(2026-08-16 사고). 기본 버전을 씁니다.
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": MAX_WEB_SEARCHES,
                    "user_location": {
                        "type": "approximate",
                        "country": "KR",
                        "timezone": "Asia/Seoul",
                    },
                },
                {
                    # RSS 요약만으로 So What을 쓰기 어려운 핵심 기사의 본문을 읽는 용도.
                    # 대화에 이미 있는 URL만 가져올 수 있어 남용될 여지가 적다.
                    "type": "web_fetch_20250910",
                    "name": "web_fetch",
                    "max_uses": MAX_WEB_FETCHES,
                    "max_content_tokens": FETCH_CONTENT_TOKENS,
                },
            ],
        )

        usage = response.usage
        print(
            f"   [{attempt + 1}회차] 종료사유={response.stop_reason} "
            f"입력 {usage.input_tokens:,} / 출력 {usage.output_tokens:,} 토큰"
        )

        # 서버측 검색 루프가 반복 한도에 걸려 잠시 멈춘 상태.
        # 지금까지의 응답을 붙여 다시 요청하면 이어서 진행됩니다.
        if response.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": response.content}]
    else:
        raise RuntimeError(
            f"검색 루프가 {MAX_RESUMES}번 이어붙인 뒤에도 끝나지 않았습니다. "
            "MAX_WEB_SEARCHES를 줄이거나 프롬프트를 단순화하세요."
        )

    # end_turn이 아니면 모델이 뉴스레터를 완성하지 못한 것 — 발송하지 않고 실패시킵니다.
    if response.stop_reason != "end_turn":
        raise RuntimeError(
            f"모델이 정상 종료하지 않았습니다 (stop_reason={response.stop_reason}). "
            "max_tokens면 MAX_TOKENS를 늘리고, refusal이면 프롬프트를 확인하세요."
        )

    # 텍스트 블록만 이어붙임 (검색 결과 블록 등은 제외)
    text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    if not text.strip():
        raise RuntimeError(f"모델 응답에 텍스트가 없습니다: {response}")
    return text


def validate_newsletter(body: str, topics: list[str]) -> None:
    """발송 전 최종 점검. 실패하면 텔레그램으로 보내지 않고 워크플로를 빨간 X로 끝냅니다.

    2026-08-16에 모델이 '다시 시도하겠습니다'만 반복한 응답이 그대로 발송된 적이
    있어서 추가했습니다. 아래 세 조건은 정상 뉴스레터라면 항상 만족합니다.
    """
    problems = []
    if len(body) < MIN_BODY_LENGTH:
        problems.append(f"본문이 너무 짧습니다 ({len(body)}자 < {MIN_BODY_LENGTH}자)")
    if not topics:
        problems.append("<topics> 블록이 없습니다 (모델이 끝까지 작성하지 못함)")
    found = [s for s in REQUIRED_SECTIONS if s in body]
    if len(found) < 2:
        problems.append(f"본문에 섹션 번호가 {len(found)}개뿐입니다 (최소 2개 필요)")

    if problems:
        raise RuntimeError(
            "뉴스레터 점검 실패 — 발송하지 않았습니다:\n  - "
            + "\n  - ".join(problems)
            + f"\n\n[본문 앞부분 300자]\n{body[:300]}"
        )


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
    return strip_preamble(body.strip()), topics, articles


def strip_preamble(body: str) -> str:
    """뉴스레터 제목 앞에 붙은 모델의 진행 상황 멘트를 제거한다.

    모델은 검색하는 동안 "리서치하겠습니다", "추가로 검색하겠습니다" 같은 텍스트를
    별도 블록으로 내보내는데, 텍스트 블록을 전부 이어붙이면 그 혼잣말이 발송 메시지
    맨 앞에 붙는다(2026-08-17 발견). 제목 마커부터 잘라내 본문만 남긴다.
    제목을 못 찾으면 원본을 그대로 두고, 이상하면 validate_newsletter가 걸러낸다.
    """
    idx = body.find(NEWSLETTER_START)
    if idx <= 0:
        return body
    print(f"   앞부분 진행 멘트 {idx}자 제거")
    return body[idx:].strip()


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
    data["lastUpdated"] = datetime.now(KST).strftime("%Y.%m.%d")

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

    print("2) RSS 피드에서 뉴스 수집 중... (API 비용 없음)")
    rss_articles, failures = rss_feeds.fetch_all()
    for f in failures:
        print(f"   ⚠️  피드 실패: {f}")
    print(f"   총 {len(rss_articles)}건 수집 (프롬프트에 상위 {MAX_RSS_IN_PROMPT}건 사용)")
    if len(rss_articles) < MIN_RSS_ARTICLES:
        # 피드가 대부분 죽었는데 그대로 진행하면 모델이 기억으로 지어낸다.
        # 비싼 검색으로 조용히 대체하지 않고 실패시켜 원인을 보게 한다.
        raise RuntimeError(
            f"RSS 수집이 {len(rss_articles)}건뿐입니다 (최소 {MIN_RSS_ARTICLES}건 필요). "
            "피드 주소가 바뀌었을 수 있습니다 — scripts/rss_feeds.py를 단독 실행해 확인하세요.\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    print("3) 뉴스레터 생성 중... (채용 검색 포함, 1~2분 소요)")
    raw = generate_newsletter(
        client,
        build_prompt(recent_topics, trend_titles, rss_articles[:MAX_RSS_IN_PROMPT]),
    )

    body, topics, articles = extract_topics(raw)
    print(f"   생성 완료: 본문 {len(body)}자, 주제 {len(topics)}건, 기사 {len(articles)}건")

    print("4) 발송 전 점검 중...")
    validate_newsletter(body, topics)
    print("   점검 통과")

    print("5) 텔레그램 발송 중...")
    send_to_telegram(bot_token, chat_id, body + SITE_FOOTER)

    save_topics(topics)  # 점검을 통과했으므로 topics는 비어 있지 않음
    print("6) 주제 기록 저장 완료 (data/history.json)")

    print("7) 기사를 트렌드별로 축적 중... (data/trends.json)")
    merge_articles_into_trends(articles)

    print("✅ 모든 작업 완료")


if __name__ == "__main__":
    main()
