# ============================================================
# 📰 금융 브리핑 자동 업데이트 스크립트
#
# 하는 일:
#   1. data.json의 각 트렌드에 대해 네이버 뉴스 API로 최신 기사 검색
#   2. Claude API에게 기사들을 읽히고 트렌드 설명·주목할 점을 갱신
#   3. data.json을 새 내용으로 저장
#
# 실행에 필요한 환경변수 (GitHub Secrets에 등록):
#   NAVER_CLIENT_ID      네이버 개발자센터에서 발급
#   NAVER_CLIENT_SECRET  네이버 개발자센터에서 발급
#   ANTHROPIC_API_KEY    Anthropic Console에서 발급
#
# 직접 실행해보기:
#   pip install anthropic
#   NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... ANTHROPIC_API_KEY=... \
#     python3 scripts/update_briefing.py
# ============================================================

import json
import os
import re
import sys
import html
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import anthropic

# 사용할 Claude 모델. 비용을 줄이고 싶으면 GitHub Secrets/환경변수
# BRIEFING_MODEL을 "claude-haiku-4-5"로 설정하세요 (품질↓ 비용↓).
MODEL = os.environ.get("BRIEFING_MODEL") or "claude-opus-4-8"  # 빈 값이면 기본 모델 사용

KST = timezone(timedelta(hours=9))
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data.json")


def require_env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"❌ 환경변수 {name}이(가) 없습니다. 설정 가이드를 확인하세요.")
    return value


def clean_text(text):
    """네이버 API 응답의 <b> 태그와 HTML 특수문자를 제거"""
    return html.unescape(re.sub(r"</?b>", "", text)).strip()


def fetch_naver_news(query, client_id, client_secret):
    """네이버 뉴스 검색 API로 관련 기사를 가져와 최근 90일 내 기사 최대 3건 반환"""
    params = urllib.parse.urlencode({"query": query, "display": 15, "sort": "sim"})
    req = urllib.request.Request(
        f"https://openapi.naver.com/v1/search/news.json?{params}",
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        items = json.load(res).get("items", [])

    cutoff = datetime.now(KST) - timedelta(days=90)
    articles, seen_titles = [], set()
    for item in items:
        title = clean_text(item["title"])
        if title in seen_titles:
            continue
        try:
            pub_date = parsedate_to_datetime(item["pubDate"])
        except (TypeError, ValueError):
            continue
        if pub_date < cutoff:
            continue
        link = item.get("originallink") or item["link"]
        press = urllib.parse.urlparse(link).hostname or "언론사"
        if press.startswith("www."):
            press = press[4:]
        articles.append({
            "title": title,
            "press": press,
            "url": link,
            "description": clean_text(item.get("description", "")),
            "date": pub_date.strftime("%Y-%m-%d"),
        })
        seen_titles.add(title)
        if len(articles) >= 3:
            break
    return articles


def build_prompt(trends, fresh_articles_by_title):
    """Claude에게 보낼 요청 본문을 만든다"""
    payload = []
    for trend in trends:
        payload.append({
            "title": trend["title"],
            "category": trend["category"],
            "since": trend["since"],
            "현재_요약": trend["summary"],
            "현재_설명": trend["desc"],
            "현재_주목할점": trend["watch"],
            "오늘_수집된_기사": fresh_articles_by_title.get(trend["title"], []),
        })
    return (
        "다음은 '금융 브리핑 룸' 웹사이트가 추적 중인 한국 금융업의 거시 트렌드 목록과, "
        "오늘 수집된 최신 기사들입니다.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n"
        "각 트렌드에 대해 summary(한 줄 요약), desc(설명), watch(주목할 점 3개)를 "
        "최신 기사 내용을 반영해 갱신해주세요. 규칙:\n"
        "- 독자는 금융 입문자입니다. 쉬운 한국어로, 개별 뉴스를 큰 흐름 안에서 해석해주세요.\n"
        "- 기사에 새로운 내용이 없으면 기존 문구를 거의 그대로 유지하세요. 억지로 바꾸지 마세요.\n"
        "- 기사에 없는 사실을 지어내지 마세요.\n"
        "- desc는 3~5문장, watch 항목은 각각 한 문장으로.\n"
        "- title은 절대 바꾸지 말고 그대로 돌려주세요 (매칭 키입니다)."
    )


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "trends": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "desc": {"type": "string"},
                    "watch": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "summary", "desc", "watch"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["trends"],
    "additionalProperties": False,
}


def main():
    naver_id = require_env("NAVER_CLIENT_ID")
    naver_secret = require_env("NAVER_CLIENT_SECRET")
    require_env("ANTHROPIC_API_KEY")  # anthropic SDK가 자동으로 읽음

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # 1) 트렌드별 최신 기사 수집
    fresh = {}
    for trend in data["trends"]:
        try:
            articles = fetch_naver_news(trend["query"], naver_id, naver_secret)
        except Exception as e:
            print(f"⚠️  '{trend['title']}' 기사 수집 실패: {e} — 기존 기사 유지")
            articles = []
        if articles:
            fresh[trend["title"]] = articles
            print(f"📰 {trend['title']}: 새 기사 {len(articles)}건")
        else:
            print(f"📰 {trend['title']}: 새 기사 없음 — 기존 기사 유지")

    # 2) Claude에게 브리핑 재작성 요청
    client = anthropic.Anthropic()
    print(f"🤖 Claude({MODEL})에게 브리핑 갱신 요청 중...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=(
            "당신은 한국 금융업 전문 브리핑 에디터입니다. "
            "매일 아침 금융 입문자를 위해 거시 트렌드 브리핑을 갱신합니다."
        ),
        messages=[{"role": "user", "content": build_prompt(data["trends"], fresh)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )

    if response.stop_reason == "refusal":
        sys.exit("❌ 모델이 응답을 거부했습니다. 다음 실행 때 다시 시도됩니다.")

    text = next(b.text for b in response.content if b.type == "text")
    updated = {t["title"]: t for t in json.loads(text)["trends"]}

    # 3) data.json 갱신 (글은 Claude 결과로, 기사는 수집 결과로)
    for trend in data["trends"]:
        new = updated.get(trend["title"])
        if new:
            trend["summary"] = new["summary"]
            trend["desc"] = new["desc"]
            trend["watch"] = new["watch"]
        if trend["title"] in fresh:
            trend["articles"] = [
                {"title": a["title"], "press": a["press"], "url": a["url"]}
                for a in fresh[trend["title"]]
            ]

    data["lastUpdated"] = datetime.now(KST).strftime("%Y.%m.%d")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    usage = response.usage
    print(f"✅ 완료! 입력 {usage.input_tokens} / 출력 {usage.output_tokens} 토큰 사용")


if __name__ == "__main__":
    main()
