"""
RSS 뉴스 수집 모듈 (하이브리드 파이프라인의 1단계)

검색 대신 RSS로 뼈대를 모으는 이유:
  - 파이썬이 직접 받으므로 API 비용이 0원. 검색은 기사 본문이 대화에 누적돼
    매 호출마다 재전송되는데, 그게 우리 비용의 대부분이었다 (1회 87,506 토큰).
  - 소스가 고정이라 매일 커버리지가 예측 가능하다.

주의: 피드는 조용히 죽는다. 2026-08-17 테스트에서 10개 중 4개가 이미 404였고
매일경제는 403으로 봇을 차단한다. 그래서 fetch_all()은 실패한 피드를 삼키지 않고
결과에 담아 돌려주며, 호출자가 로그로 남기고 전부 실패하면 중단한다.

표준 라이브러리만 사용 (feedparser 등 추가 의존성 없음).
"""

import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; finance-briefing-room/1.0)"
TIMEOUT = 15
MAX_AGE_HOURS = 36        # 이보다 오래된 기사는 버림 (주말·연휴 감안)
MAX_PER_FEED = 40         # 피드 하나에서 가져올 최대 기사 수

# 2026-08-17 실측으로 살아있음을 확인한 피드만 등록.
# 매일경제(403 차단), 이데일리·서울경제·파이낸셜뉴스(404)는 제외.
FEEDS = [
    {"name": "한국경제 금융", "url": "https://www.hankyung.com/feed/finance", "focused": True},
    {"name": "한국경제 경제", "url": "https://www.hankyung.com/feed/economy", "focused": True},
    {"name": "연합뉴스 경제", "url": "https://www.yna.co.kr/rss/economy.xml", "focused": True},
    {"name": "연합인포맥스", "url": "https://news.einfomax.co.kr/rss/allArticle.xml", "focused": False},
    {"name": "머니투데이", "url": "https://rss.mt.co.kr/mt_news.xml", "focused": False},
]

# focused=False 피드(전체 기사)는 정치·사회 기사가 섞여 들어오므로 키워드로 걸러낸다.
FINANCE_KEYWORDS = [
    "은행", "금융", "금리", "대출", "예금", "증권", "보험", "카드", "신탁",
    "한국은행", "금융위", "금감원", "금통위", "환율", "채권", "펀드",
    "스테이블코인", "가상자산", "코인", "핀테크", "저축은행", "캐피탈",
    "지주", "주주환원", "배당", "자사주", "실적", "순이익", "NIM",
    "부동산", "가계부채", "DSR", "채용", "공시", "IPO", "상장",
]


def clean(text: str) -> str:
    """HTML 태그와 특수문자를 제거하고 공백을 정리."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def press_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# 추적용 파라미터만 골라 제거한다. 물음표 뒤를 통째로 자르면 안 된다 —
# 연합인포맥스처럼 ?idxno=4430402 로 기사를 구분하는 언론사는 모든 기사가
# 같은 URL로 뭉개져 1건만 남는다 (2026-08-17 실제 발생).
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "from", "sc", "cid",
}


def dedupe_key(url: str) -> str:
    parts = urlsplit(url)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
        urlencode(sorted(kept)), "",
    ))


def parse_when(item: ET.Element):
    """pubDate를 KST aware datetime으로. 형식이 언론사마다 달라 두 가지를 시도."""
    raw = (item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or "").strip()
    if not raw:
        return None
    try:  # "Mon, 17 Aug 2026 22:06:07 +0900"
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:  # "2026-08-17 21:12:21" (연합인포맥스)
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)


# 금융 키워드를 우연히 포함하지만 뉴스레터에 쓸 수 없는 기사들.
# 예: "[부고]김종민(메리츠증권 사장)씨 빙부상"은 '증권' 때문에 통과해버린다.
# focused 여부와 무관하게 항상 적용한다.
EXCLUDE_PATTERNS = [
    "부고", "빙부상", "빙모상", "[인사]", "인사]", "동정", "신간",
    "오늘의 운세", "포토", "화보", "[영상]", "부동산 매물", "골프",
]


def is_excluded(title: str) -> bool:
    return any(p in title for p in EXCLUDE_PATTERNS)


def is_finance(title: str, summary: str) -> bool:
    text = f"{title} {summary}"
    return any(k in text for k in FINANCE_KEYWORDS)


def fetch_feed(feed: dict) -> list[dict]:
    """피드 하나를 읽어 최근 기사 목록을 반환. 실패하면 예외를 그대로 올린다."""
    req = urllib.request.Request(feed["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        root = ET.fromstring(res.read())

    cutoff = datetime.now(KST) - timedelta(hours=MAX_AGE_HOURS)
    out = []
    for item in root.findall(".//item")[:MAX_PER_FEED * 2]:
        title = clean(item.findtext("title", ""))
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        if is_excluded(title):
            continue
        when = parse_when(item)
        if when and when < cutoff:
            continue
        summary = clean(item.findtext("description", ""))[:200]
        if not feed["focused"] and not is_finance(title, summary):
            continue  # 전체 기사 피드에서 정치·사회 기사 제거
        out.append({
            "title": title,
            "url": link,
            "press": press_of(link),
            "date": (when or datetime.now(KST)).strftime("%Y-%m-%d"),
            "time": (when or datetime.now(KST)).strftime("%H:%M"),
            "summary": summary,
            "source": feed["name"],
        })
        if len(out) >= MAX_PER_FEED:
            break
    return out


def fetch_all() -> tuple[list[dict], list[str]]:
    """모든 피드를 수집해 (기사 목록, 실패 메시지 목록)을 반환.

    실패를 조용히 삼키지 않는다 — 피드가 404로 죽으면 커버리지가 줄어든 걸
    알아야 하므로 호출자에게 넘겨 로그에 남긴다.
    """
    articles, failures, seen = [], [], set()
    for feed in FEEDS:
        try:
            got = fetch_feed(feed)
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError) as e:
            failures.append(f"{feed['name']}: {type(e).__name__} {str(e)[:60]}")
            continue
        added = 0
        for a in got:
            key = dedupe_key(a["url"])
            if key in seen:
                continue          # 같은 기사가 여러 피드에 뜨는 경우 제거
            seen.add(key)
            articles.append(a)
            added += 1
        print(f"   📰 {feed['name']}: {added}건")

    # 최신순 정렬 — 프롬프트에 넣을 때 중요한 것이 위에 오도록
    articles.sort(key=lambda a: (a["date"], a["time"]), reverse=True)
    return articles, failures


if __name__ == "__main__":  # python3 scripts/rss_feeds.py 로 단독 점검
    arts, fails = fetch_all()
    print(f"\n총 {len(arts)}건 수집, 실패 {len(fails)}건")
    for f in fails:
        print(f"   ⚠️  {f}")
    for a in arts[:8]:
        print(f"   [{a['date']} {a['time']}] {a['title'][:52]} ({a['press']})")
