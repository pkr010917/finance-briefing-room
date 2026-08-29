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

# ── 발굴 기준 (2026-08-18 대폭 강화) ──────────────────────────
# 하루에 5개가 한꺼번에 추가돼 8개 → 13개가 된 적이 있다. 그때 들어온 것들은
# 폭우 대응(계절성), 종부세 특례(은행업과 무관), 공공기관 지방이전(행정 이슈)처럼
# 원래 8개와 성격이 달랐다. 아래 문턱은 그 재발을 막기 위한 것 — 낮추지 말 것.
MIN_UNCLASSIFIED = 25            # 미분류가 이만큼 쌓여야 발굴 시도
MIN_ARTICLES_PER_TREND = 8       # 새 트렌드를 뒷받침해야 하는 최소 기사 수
MAX_NEW_PER_RUN = 1              # 한 번에 최대 1개만 추가
MAX_TOTAL_TRENDS = 12            # 전체 트렌드 상한 (넘으면 발굴 중단)
MAX_TITLE_LENGTH = 20            # 원래 8개처럼 짧은 명사형 제목만
DISCOVERY_INTERVAL_DAYS = 7      # 마지막 시도로부터 이 기간이 지나야 재시도

KST = timezone(timedelta(hours=9))
TRENDS_FILE = Path(__file__).parent.parent / "data" / "trends.json"

# 발굴 기준을 모델에게 보여줄 때 쓰는 '합격선' 예시.
# 추상적인 규칙보다 실제 통과 사례를 보여주는 편이 훨씬 잘 먹힌다.
REFERENCE_TRENDS = """- 금리 사이클 전환 (2022~): 4년째 이어지는 통화정책 사이클. 은행 이자이익의 출발점
- 가계대출 총량규제 (2023~): 은행의 전통 수익공식(주담대)을 막은 3년째 규제 기조
- 비이자이익 확대 (2022~): 이자 장사 한계에 대응한 수익구조 다변화
- AI 대전환 (AX) (2025~): 업무·점포·인력 구조를 다시 짜는 그룹 차원의 전환
- 원화 스테이블코인 (2026~): 돈의 형태와 은행 조달구조를 바꿀 수 있는 판
- 밸류업·주주환원 (2024~): 은행주 재평가. 순이익만큼 환원율이 중요해진 흐름
- 생산적 금융 508조 (2026~): 자산 구성을 가계·부동산에서 기업으로 옮기는 정책 프레임
- 글로벌 진출 가속 (2023~): 국내 포화에 대응한 해외 이익 비중 확대"""

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
    return f"""'금융 브리핑 룸'은 한국 금융업의 **거시 트렌드**를 추적하는 사이트입니다.
새 트렌드를 추가할지 판단해주세요. **기본 답은 "추가하지 않음"입니다.**

## 합격선 — 이 사이트가 트렌드로 인정하는 수준
{REFERENCE_TRENDS}

위 목록의 공통점을 보세요:
- **은행·금융지주가 어떻게 돈을 버는지**(수익구조·사업모델)에 직접 영향을 준다
- 최소 1년, 대개 2~4년 이어지는 구조적 흐름이다. 계절성 이벤트나 단발 사건이 아니다
- 제목이 짧은 명사형이다 ({MAX_TITLE_LENGTH}자 이내). 문장형 제목은 트렌드가 아니라 기사 제목이다
- 카테고리가 단순하다 (금리·정책 / 은행 / 금융지주 / 디지털자산 / 은행·증권)

## 트렌드가 아닌 것 (실제로 잘못 추가됐던 사례)
- ❌ "기후재해 금융지원 상시화" — 폭우철에만 나오는 계절성 대응
- ❌ "종부세 비거주 1주택 특례 확대" — 부동산 세제. 은행 수익구조와 거리가 멀다
- ❌ "금융 공공기관 지방이전 갈등" — 행정·조직 이슈지 산업 구조 변화가 아니다
- ❌ "원화 강세 구조화: 수출기업 달러 매도에 환율 하락 압력 지속" — 제목이 문장형이고,
  환율 등락은 오르내리는 시장 변수지 은행이 몇 년간 대응하는 구조적 흐름이 아니다
- ❌ 채용 공고, 인사, 행사, 실적 발표, 개별 회사의 단일 사건

## 현재 추적 중인 트렌드 (겹치면 제안 금지)
{existing}

## 미분류 기사 {len(unclassified)}건
{articles}

## 판단
- 위 합격선에 **확실히** 부합하고, 미분류 기사 중 **{MIN_ARTICLES_PER_TREND}건 이상**이
  뒷받침하는 흐름이 있을 때만 제안하세요.
- 최대 {MAX_NEW_PER_RUN}개만 제안하세요. 애매하면 제안하지 마세요.
- **대부분의 실행에서는 빈 배열이 정답입니다.** 억지로 만들면 사이트가 망가집니다.
  "그럴듯한 묶음"이 아니라 "몇 년간 추적할 가치가 있는 흐름"인지로 판단하세요.

형식: title({MAX_TITLE_LENGTH}자 이내 명사형), category(위 5개 중 하나),
since("{datetime.now(KST).year}~"), summary(한 줄 요약),
desc(금융 입문자를 위한 설명 5~6문장 — 이 설명은 앞으로 바뀌지 않으므로 시의성 표현 대신
구조적 배경·의미·관전 포인트를 담을 것), query(네이버 뉴스 검색어),
article_urls(근거가 된 미분류 기사 URL — {MIN_ARTICLES_PER_TREND}건 이상)"""


def save(data: dict) -> None:
    data["lastUpdated"] = datetime.now(KST).strftime("%Y.%m.%d")
    TRENDS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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

    # ── 발굴을 시도할지부터 판단 (여기서 걸러지면 API 호출 0) ──
    if len(data["trends"]) >= MAX_TOTAL_TRENDS:
        print(f"트렌드가 이미 {len(data['trends'])}개로 상한({MAX_TOTAL_TRENDS})에 도달 — 발굴 중단")
        return

    last = data.get("lastDiscoveryAt")
    if last:
        days = (datetime.now(KST) - datetime.fromisoformat(last)).days
        if days < DISCOVERY_INTERVAL_DAYS:
            print(f"마지막 발굴 시도 {days}일 전 — {DISCOVERY_INTERVAL_DAYS}일마다 시도합니다 (API 호출 없음)")
            return

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

    # 시도했다는 사실 자체를 기록 — 결과와 무관하게 다음 시도까지 간격을 둔다
    data["lastDiscoveryAt"] = datetime.now(KST).isoformat()

    text = next(b.text for b in response.content if b.type == "text")
    new_trends = json.loads(text)["new_trends"]
    if not new_trends:
        print("새 트렌드 없음 — 미분류 기사가 아직 흐름을 이루지 않습니다")
        save(data)
        return

    # 모델이 기준을 넘겨 제안하더라도 코드에서 다시 거른다.
    # 프롬프트만으로는 과잉 생성을 막지 못했던 전례가 있다 (2026-08-17, 하루에 5개).
    existing_titles = {t["title"] for t in data["trends"]}
    added = []
    for nt in new_trends:
        if len(added) >= MAX_NEW_PER_RUN:
            print(f"   '{nt['title']}' — 1회 추가 상한({MAX_NEW_PER_RUN}개) 초과로 보류")
            break
        if nt["title"] in existing_titles:
            continue
        if len(nt["title"]) > MAX_TITLE_LENGTH:
            print(f"   '{nt['title'][:30]}…' — 제목이 {len(nt['title'])}자로 너무 김 (문장형 제목은 트렌드가 아님)")
            continue
        urls = set(nt.pop("article_urls", []))
        moved = [a for a in unclassified if a["url"] in urls]
        if len(moved) < MIN_ARTICLES_PER_TREND:
            print(f"   '{nt['title']}' — 근거 기사 {len(moved)}건뿐 (최소 {MIN_ARTICLES_PER_TREND}건) 이라 보류")
            continue
        nt["articles"] = moved
        data["trends"].append(nt)
        unclassified = [a for a in unclassified if a["url"] not in urls]
        added.append((nt["title"], len(moved)))

    if not added:
        print("기준을 통과한 트렌드 없음")
        save(data)
        return

    data["unclassified"] = unclassified
    save(data)

    for title, n in added:
        print(f"✅ 새 트렌드 추가: {title} (기사 {n}건 이동)")
    names = ", ".join(t for t, _ in added)
    notify_telegram(
        f"🌱 브리핑 룸에 새 트렌드가 발굴됐습니다: {names}\n"
        "https://pkr010917.github.io/finance-briefing-room/"
    )


if __name__ == "__main__":
    main()
