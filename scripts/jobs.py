"""채용 공고 대장 (data/jobs.json)

뉴스는 하루짜리 사건이지만 채용 공고는 '기간'이다. 그런데 이전에는 공고도 뉴스처럼
매일 백지에서 검색해 백지에 썼기 때문에, 어제 찾은 공고가 오늘도 '새 공고'로 나갔다.
그래서 공고는 대장에 쌓아두고, 검색은 '대장에 없는 것'만 찾게 한다.

D-day는 반드시 여기서 마감일로부터 계산한다. 모델이 직접 쓰게 하면 검색 결과 조각을 보고
추측한 값이 그대로 발송되는데, 틀려도 아무도 알아채지 못한다.

이 모듈은 3️⃣ 섹션 텍스트를 통째로 만들어 낸다(모델이 쓰지 않는다). 덕분에 정렬·묶음·
D-day·서식이 매일 똑같이 나오고, 진행 중인 공고를 모델이 매번 다시 쓰지 않아도 된다.
"""

# 타입 표기를 실행 시점에 해석하지 않게 한다 (GitHub Actions는 3.12지만
# 맥에 기본 설치된 파이썬은 3.9라, 없으면 로컬에서 오프라인 검증을 못 돌린다)
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs.json"

# 뉴스레터에 묶어서 보여줄 분류. 순서가 곧 출력 순서다.
# 보험·카드·핀테크는 일단 '기타'에 넣고, 쌓이는 양을 보고 나중에 분리한다.
GROUPS = ["은행", "금융공기업", "증권", "기타"]

MAX_JOBS = 60          # 대장 보관 상한 (마감일이 가까운 것부터 유지)
MAX_FUTURE_DAYS = 180  # 이보다 먼 마감일은 모델이 연도를 잘못 읽은 것으로 본다
DIVIDER_RE = re.compile("━+")


# ────────────────────────── 저장소 ──────────────────────────
def _key(company: str, title: str) -> str:
    """중복 판정용 열쇠. 띄어쓰기·대소문자 차이는 같은 공고로 본다."""
    return re.sub(r"\s+", "", f"{company}{title}").lower()


def _parse_deadline(value) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def load() -> list[dict]:
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [j for j in data.get("jobs", []) if isinstance(j, dict)]


def save(jobs: list[dict], today: date) -> None:
    JOBS_FILE.write_text(
        json.dumps(
            {"lastUpdated": today.strftime("%Y.%m.%d"), "jobs": jobs},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# ────────────────────────── 대장 갱신 ──────────────────────────
def prune(jobs: list[dict], today: date) -> tuple[list[dict], list[str]]:
    """마감일이 지난 공고를 걷어낸다. (남은 대장, 걷어낸 공고명)을 돌려준다."""
    kept, closed = [], []
    for job in jobs:
        deadline = _parse_deadline(job.get("deadline"))
        if deadline is None or deadline < today:
            closed.append(f"{job.get('company', '')} {job.get('title', '')}".strip())
        else:
            kept.append(job)
    kept.sort(key=lambda j: j["deadline"])
    return kept[:MAX_JOBS], closed


def merge(
    jobs: list[dict], found: list[dict], today: date
) -> tuple[list[dict], set[str], list[str]]:
    """모델이 찾아온 공고를 대장에 합친다.

    돌려주는 값: (갱신된 대장, 오늘 새로 추가된 공고의 열쇠들, 경고 문구들)

    마감일 검사가 이 함수의 핵심이다. 모델이 날짜를 잘못 뽑으면 틀린 D-day가
    조용히 매일 발송되므로, 형식·과거·너무 먼 미래를 모두 걸러 낸다.
    """
    known = {_key(j.get("company", ""), j.get("title", "")) for j in jobs}
    added: set[str] = set()
    warnings: list[str] = []

    for item in found:
        if not isinstance(item, dict):
            continue
        company = str(item.get("company", "")).strip()
        title = str(item.get("title", "")).strip()
        label = f"{company} {title}".strip() or str(item)[:60]

        if not company or not title:
            warnings.append(f"회사명 또는 공고명이 비어 있어 건너뜀 — {label}")
            continue

        deadline = _parse_deadline(item.get("deadline"))
        if deadline is None:
            warnings.append(
                f"마감일을 못 읽어 건너뜀 — {label} → {item.get('deadline')!r}"
            )
            continue
        if deadline < today:
            warnings.append(f"이미 지난 마감일이라 건너뜀 — {label} → {deadline}")
            continue
        if (deadline - today).days > MAX_FUTURE_DAYS:
            warnings.append(
                f"마감일이 {MAX_FUTURE_DAYS}일보다 멀어 건너뜀 (연도 오독 의심) — {label} → {deadline}"
            )
            continue

        key = _key(company, title)
        if key in known:
            continue  # 이미 대장에 있음 — 오늘의 '새 공고'가 아니다

        group = str(item.get("group", "")).strip()
        if group not in GROUPS:
            group = "기타"
        url = str(item.get("url", "")).strip()

        known.add(key)
        added.add(key)
        jobs.append(
            {
                "company": company,
                "title": title,
                "group": group,
                "deadline": deadline.isoformat(),
                "url": url if url.startswith("http") else "",
                "note": str(item.get("note", "")).strip(),
                "found": today.isoformat(),
            }
        )

    jobs.sort(key=lambda j: j["deadline"])
    return jobs[:MAX_JOBS], added, warnings


# ────────────────────────── 출력 ──────────────────────────
def dday(deadline: str, today: date) -> str:
    days = (date.fromisoformat(deadline) - today).days
    return "D-DAY" if days == 0 else f"D-{days}"


def _short(deadline: str) -> str:
    d = date.fromisoformat(deadline)
    return f"{d.month}/{d.day}"


def known_block(jobs: list[dict]) -> str:
    """프롬프트에 넣을 '이미 아는 공고' 목록. 모델은 이 목록에 없는 것만 찾는다."""
    if not jobs:
        return "(아직 없음 — 오늘이 첫 수집입니다)"
    return "\n".join(f"- {j['company']} {j['title']}" for j in jobs)


def render_section(jobs: list[dict], added: set[str], today: date) -> str:
    """3️⃣ 섹션 전체를 만든다. 표준 서식과 어긋날 여지가 없도록 여기서 조립한다."""
    lines = ["3️⃣ 금융권 채용·취업", ""]

    new = [j for j in jobs if _key(j["company"], j["title"]) in added]
    if new:
        lines.append("오늘 새로 확인된 공고")
        for j in new:
            lines.append(
                f"▪️ [{j['group']}] {j['company']} {j['title']} — "
                f"{dday(j['deadline'], today)} ({_short(j['deadline'])} 마감)"
            )
            if j["note"]:
                lines.append(f"   {j['note']}")
    else:
        lines.append("오늘 새로 확인된 공고는 없습니다.")

    ongoing = [j for j in jobs if _key(j["company"], j["title"]) not in added]
    if ongoing:
        lines += ["", "접수 중인 공고"]
        for group in GROUPS:
            members = [j for j in ongoing if j["group"] == group]
            if not members:
                continue
            lines.append(f"[{group}]")
            for j in members:
                lines.append(
                    f"  · {j['company']} {j['title']} — "
                    f"{dday(j['deadline'], today)} ({_short(j['deadline'])})"
                )

    return "\n".join(lines)


def _divider_before(body: str, idx: int, after: int = 0) -> int:
    """idx 앞에 있는 마지막 구분선의 시작 위치. 없으면 idx 자체."""
    found = list(DIVIDER_RE.finditer(body, after, idx))
    return found[-1].start() if found else idx


def _drop_model_section(body: str) -> str:
    """모델이 지시를 어기고 직접 쓴 3️⃣ 섹션을 통째로 들어낸다."""
    start = _divider_before(body, body.find("3️⃣"))
    idx4 = body.find("4️⃣", start)
    end = _divider_before(body, idx4, start + 1) if idx4 > 0 else len(body)
    return body[:start] + body[end:]


def insert_section(body: str, section: str) -> tuple[str, list[str]]:
    """뉴스레터 본문의 4️⃣ 바로 앞에 3️⃣ 섹션을 끼워 넣는다.

    모델은 3️⃣을 쓰지 않기로 되어 있다(프롬프트에서 금지). 그래도 쓴 경우에는
    들어내고 대장으로 만든 것으로 바꾼다 — D-day가 정확한 쪽을 남겨야 하기 때문이다.
    4️⃣ 앞의 구분선 자리에 넣어야 구분선이 겹치거나 모자라지 않는다.
    """
    warnings: list[str] = []
    if "3️⃣" in body:
        body = _drop_model_section(body)
        warnings.append(
            "모델이 3️⃣ 섹션을 직접 썼습니다 — 대장으로 만든 것으로 교체했습니다"
        )

    divider = "━" * 18
    idx = body.find("4️⃣")
    if idx < 0:
        warnings.append("본문에 4️⃣가 없어 3️⃣ 섹션을 맨 뒤에 붙였습니다")
        return f"{body.rstrip()}\n\n{divider}\n{section}", warnings

    cut = _divider_before(body, idx)
    return f"{body[:cut]}{divider}\n{section}\n\n{body[cut:]}", warnings
