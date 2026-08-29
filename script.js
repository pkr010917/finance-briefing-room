// ─────────────────────────────────────────────
// 금융 브리핑 룸
// 최신 브리핑의 뉴스를 테마(트렌드)별 카드로 정리해,
// 아침에 텔레그램에서 본 기사가 어떤 흐름에 속하는지
// 한눈에 다시 떠올릴 수 있게 한다.
// (옵시디언식 점 그래프는 2026-08-29 폐기 — 익명의 점은
//  마우스를 올리기 전까지 아무 정보도 주지 못했다)
// ─────────────────────────────────────────────

const KST_OFFSET = 9 * 60; // 분
const HOT_DAYS = 7;        // '지금 활발' 판단 기간

const panel = document.getElementById("panel");
let TRENDS = [];

// ── 날짜 도우미 ─────────────────────────────
function todayKST() {
  const now = new Date(Date.now() + (KST_OFFSET + new Date().getTimezoneOffset()) * 60000);
  return now.toISOString().slice(0, 10); // "YYYY-MM-DD"
}

function daysAgo(dateStr) {
  if (!dateStr) return Infinity;
  return (new Date(todayKST()) - new Date(dateStr)) / 86400000;
}

function dateLabel(iso) { // "2026-08-28" → "8월 28일"
  const [, m, d] = iso.split("-").map(Number);
  return `${m}월 ${d}일`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── 초기화 ──────────────────────────────────
async function init() {
  const res = await fetch("data/trends.json");
  const data = await res.json();
  TRENDS = data.trends;

  const all = TRENDS.flatMap((t) => t.articles);
  // 최신 브리핑 날짜 — 주말에는 마지막 평일 브리핑이 보이도록 '가장 최근 기사일'을 쓴다
  const latest = all.reduce((m, a) => (a.date && a.date > m ? a.date : m), "");

  document.getElementById("meta").textContent =
    `${TRENDS.length}개 트렌드 · 뉴스 ${all.length}건 축적`;
  document.getElementById("foot").textContent =
    `평일 아침 7시, 데일리 브리핑의 뉴스가 자동으로 연결됩니다 · 마지막 갱신 ${data.lastUpdated}`;

  renderBoard(latest);
  renderAllTrends(latest);
}

// ── 최신 브리핑 보드 ────────────────────────
function renderBoard(latest) {
  const isToday = latest === todayKST();
  document.getElementById("board-title").textContent =
    isToday ? "오늘의 브리핑" : `${dateLabel(latest)} 브리핑`;

  const groups = TRENDS
    .map((t) => ({ t, arts: t.articles.filter((a) => a.date === latest) }))
    .filter((g) => g.arts.length > 0)
    .sort((a, b) => b.arts.length - a.arts.length);

  const total = groups.reduce((n, g) => n + g.arts.length, 0);
  document.getElementById("board-sub").textContent =
    `뉴스 ${total}건이 ${groups.length}개 테마에 연결됐습니다`;

  const grid = document.getElementById("board-grid");
  grid.innerHTML = "";
  for (const { t, arts } of groups) {
    const card = el("article", "card");

    // 카드 머리: 트렌드 이름 — 누르면 설명·아카이브 패널
    const head = el("button", "card__head");
    head.append(
      el("span", "card__cat", t.category),
      el("span", "card__name", t.title),
      el("span", "card__count", `+${arts.length}`),
    );
    head.addEventListener("click", () => selectTrend(t));
    card.appendChild(head);

    // 기사 목록: 제목이 그대로 보인다 — 이것이 이 화면의 본질
    const list = el("ul", "card__list");
    for (const a of arts) {
      const li = el("li");
      const link = el("a");
      link.href = a.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.append(el("span", "a-dot"), el("span", "a-title", a.title), el("span", "a-press", a.press));
      li.appendChild(link);
      list.appendChild(li);
    }
    card.appendChild(list);
    grid.appendChild(card);
  }
}

// ── 모든 트렌드 목록 ────────────────────────
function renderAllTrends(latest) {
  const rows = TRENDS
    .map((t) => ({ t, recent: t.articles.filter((a) => daysAgo(a.date) < HOT_DAYS).length }))
    .sort((a, b) => b.recent - a.recent || b.t.articles.length - a.t.articles.length);
  const hottest = rows[0] && rows[0].recent > 0 ? rows[0].t : null;

  const box = document.getElementById("trend-list");
  box.innerHTML = "";
  for (const { t, recent } of rows) {
    const row = el("button", "trow");
    if (t === hottest) row.appendChild(el("span", "trow__hot"));
    row.append(el("span", "trow__name", t.title), el("span", "trow__cat", t.category));
    const stat = recent > 0 ? `이번 주 +${recent} · ${t.articles.length}건` : `${t.articles.length}건`;
    row.appendChild(el("span", "trow__stat", stat));
    row.addEventListener("click", () => selectTrend(t));
    box.appendChild(row);
  }
}

// ── 패널 ────────────────────────────────────
function selectTrend(trend) {
  document.getElementById("panel-category").textContent = trend.category;
  document.getElementById("panel-title").textContent = trend.title;
  document.getElementById("panel-since").textContent = `이 흐름의 시작 ${trend.since}`;
  document.getElementById("panel-desc").textContent = trend.desc;
  document.getElementById("panel-more").href =
    `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(trend.query)}`;

  const list = document.getElementById("panel-articles");
  list.innerHTML = "";
  const today = todayKST();
  const sorted = [...trend.articles].sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  for (const article of sorted) {
    const li = el("li");
    const a = el("a");
    a.href = article.url;
    a.target = "_blank";
    a.rel = "noopener";

    const isNew = article.date === today;
    const date = el("span", "a-date" + (isNew ? " a-date--new" : ""),
      isNew ? "오늘" : (article.date || "").slice(5).replace("-", "."));

    a.append(date, el("span", "a-title", article.title), el("span", "a-press", article.press));
    li.appendChild(a);
    list.appendChild(li);
  }
  panel.hidden = false;
}

function closePanel() { panel.hidden = true; }

document.getElementById("panel-close").addEventListener("click", closePanel);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

init();
