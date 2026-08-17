"""
새 카테고리(거시 트렌드) 발굴 스크립트

기존의 '브리핑 갱신'(매일 트렌드 설명을 다시 쓰는 작업)을 폐기하고 대신 도입.
카테고리 설명은 고정이므로, 토큰은 새 흐름을 발견하는 데만 씁니다.

동작:
  1. data/trends.json의 미분류(unclassified) 기사가 MIN_UNCLASSIFIED건 미만이면
     API를 호출하지 않고 그냥 종료 (비용 0)
  2. 그 이상 쌓였으면 Claude에게 "이 기사들이 기존 트렌드와 구별되는
     새 거시 트렌드를 이루는가"를 물음
  3. 새 트렌드가 발견되면 trends.json에 추가하고, 해당 기사들을 미분류에서
     새 트렌드로 이동. 텔레그램으로도 알림 (토큰 설정이 있을 때만)

필요 환경변수: ANTHROPIC_API_KEY (필수), TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID (선택)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import requests

MODEL = os.environ.get("DISCOVER_MODEL") or "claude-sonnet-5"
MIN_UNCLASSIFIED = 8             # 미분류 기사가 이만큼 쌓여야 발굴 시도
MIN_ARTICLES_PER_TREND = 3       # 새 트렌드는 최소 이만큼의 기사가 뒷받침해야 함
KST = timezone(timedelta(hours=9))
TRENDS_FILE = Path(__file__).parent.parent / "data" / "trends.json"

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "new_trends": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "since": {"type": "string"},
                    "summary": {"type": "string"},
                    "desc": {"type": "string"},
                    "query": {"type": "string"},
                    "article_urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "title", "category", "since", "summary",
                    "desc", "query", "article_urls",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["new_trends"],
    "additionalProperties": False,
}


def build_prompt(trends: list[dict], unclassified: list[dict]) -> str:
    existing = "\n".join(f"- {t['title']}: {t['summary']}" for t in trends)
    articles = "\n".join(
        f"- [{a.get('date', '?')}] {a['title']} ({a.get('press', '?')}) {a['url']}"
        for a in unclassified
    )
    return f"""'금융 브리핑 룸'은 한국 금융업의 거시 트렌드를 추적하는 사이트입니다.
아래는 현재 추적 중인 트렌드 목록과, 어느 트렌드에도 속하지 않아 미분류로 쌓인 기사들입니다.

## 현재 트렌드
{existing}

## 미분류 기사
{articles}

## 요청
미분류 기사들 중 {MIN_ARTICLES_PER_TREND}건 이상이 하나의 일관된 '새 거시 트렌드'를 이루는 경우에만
그 트렌드를 제안하세요. 규칙:
- 기존 트렌드와 겹치면 제안하지 마세요. 개별 사건이 아니라 몇 달 이상 이어질 구조적 흐름이어야 합니다.
- 채용 공고, 일회성 행사, 인사 소식은 트렌드가 아닙니다.
- 확신이 없으면 빈 배열을 반환하세요. 억지로 만들지 마세요.
- 새 트렌드 형식: title(간결한 이름), category(금리·정책/은행/금융지주/디지털자산 등),
  since("{datetime.now(KST).year}~" 형태), summary(한 줄 요약),
  desc(금융 입문자를 위한 포괄적 설명 5~6문장 — 이 설명은 앞으로 바뀌지 않으므로
  시의성 표현 대신 구조적 배경·의미·관전 포인트를 담을 것),
  query(네이버 뉴스 검색어), article_urls(근거가 된 미분류 기사 URL 목록)"""


def notify_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"⚠️  텔레그램 알림 실패 (발굴 결과는 저장됨): {e}")


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("환경변수 ANTHROPIC_API_KEY가 없습니다")

    data = json.loads(TRENDS_FILE.read_text(encoding="utf-8"))
    unclassified = data.get("unclassified", [])
    print(f"미분류 기사 {len(unclassified)}건 (발굴 기준: {MIN_UNCLASSIFIED}건)")
    if len(unclassified) < MIN_UNCLASSIFIED:
        print("아직 충분히 쌓이지 않아 발굴을 건너뜁니다 (API 호출 없음)")
        return

    client = anthropic.Anthropic()
    print(f"🔎 {MODEL}에게 새 트렌드 발굴 요청 중...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": build_prompt(data["trends"], unclassified)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        print("모델이 응답을 거부했습니다. 다음 실행 때 다시 시도됩니다.")
        return
    if response.stop_reason != "end_turn":
        sys.exit(f"비정상 종료 (stop_reason={response.stop_reason})")

    text = next(b.text for b in response.content if b.type == "text")
    new_trends = json.loads(text)["new_trends"]
    if not new_trends:
        print("새 트렌드 없음 — 미분류 기사가 아직 흐름을 이루지 않습니다")
        return

    existing_titles = {t["title"] for t in data["trends"]}
    added = []
    for nt in new_trends:
        if nt["title"] in existing_titles:
            continue
        urls = set(nt.pop("article_urls", []))
        moved = [a for a in unclassified if a["url"] in urls]
        if len(moved) < MIN_ARTICLES_PER_TREND:
            print(f"   '{nt['title']}' — 근거 기사 {len(moved)}건뿐이라 보류")
            continue
        nt["articles"] = moved
        data["trends"].append(nt)
        unclassified = [a for a in unclassified if a["url"] not in urls]
        added.append((nt["title"], len(moved)))

    if not added:
        print("추가된 트렌드 없음")
        return

    data["unclassified"] = unclassified
    data["lastUpdated"] = datetime.now(KST).strftime("%Y.%m.%d")
    TRENDS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for title, n in added:
        print(f"✅ 새 트렌드 추가: {title} (기사 {n}건 이동)")
    names = ", ".join(t for t, _ in added)
    notify_telegram(
        f"🌱 브리핑 룸에 새 트렌드가 발굴됐습니다: {names}\n"
        "https://pkr010917.github.io/finance-briefing-room/"
    )


if __name__ == "__main__":
    main()
