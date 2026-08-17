// ─────────────────────────────────────────────
// 금융 브리핑 룸 — 트렌드 그래프
// 트렌드(허브) 주위에 뉴스 기사(점)가 연결되는
// 옵시디언식 그래프. 오늘 새로 붙은 기사는 초록색.
// 외부 라이브러리 없이 가벼운 물리 시뮬레이션으로 배치.
// ─────────────────────────────────────────────

const KST_OFFSET = 9 * 60; // 분
const HOT_DAYS = 7;        // '지금 활발' 판단 기간

// 좁은 화면에서는 점을 적게 보여준다. 155개를 다 찍으면 겹쳐서 오히려
// 아무것도 기억에 남지 않는다 — 최근 기사 위주로 줄이는 편이 낫다.
function isNarrow() { return window.innerWidth < 720; }
function maxLeaves() { return isNarrow() ? 8 : 20; }

const svg = document.getElementById("graph");
const tooltip = document.getElementById("tooltip");
const panel = document.getElementById("panel");

let hubs = [];   // {trend, x, y, vx, vy, r, recent, el...}
let leaves = []; // {article, hub, x, y, vx, vy, r, isNew, el...}
let selectedHub = null;
let animId = null;

// ── 날짜 도우미 ─────────────────────────────
function todayKST() {
  const now = new Date(Date.now() + (KST_OFFSET + new Date().getTimezoneOffset()) * 60000);
  return now.toISOString().slice(0, 10); // "YYYY-MM-DD"
}

function daysAgo(dateStr) {
  if (!dateStr) return Infinity;
  return (new Date(todayKST()) - new Date(dateStr)) / 86400000;
}

// ── 데이터 로드 ─────────────────────────────
function whenStageSized() {
  // 스타일시트가 늦게 적용되면 SVG가 기본 크기(300×150)로 측정되어
  // 그래프가 구석에 뭉개진다. 실제 크기가 잡힐 때까지 기다린다.
  return new Promise((resolve) => {
    const check = () => {
      const r = svg.getBoundingClientRect();
      if (r.width > 320 && r.height > 260) resolve();
      else requestAnimationFrame(check);
    };
    check();
  });
}

async function init() {
  const res = await fetch("data/trends.json");
  const data = await res.json();
  await whenStageSized();
  const today = todayKST();

  hubs = data.trends.map((trend) => {
    const articles = trend.articles.slice(0, maxLeaves());
    const recent = trend.articles.filter((a) => daysAgo(a.date) < HOT_DAYS).length;
    return {
      trend, articles, recent,
      x: 0, y: 0, vx: 0, vy: 0,
      r: (isNarrow() ? 20 : 24) + Math.min(recent * 2.2, 16),
      // 라벨이 화면 밖으로 잘리지 않도록 필요한 좌우 여백 (13px 폰트 근사)
      pad: Math.max(60, trend.title.length * 6.6),
    };
  });

  leaves = [];
  for (const hub of hubs) {
    for (const article of hub.articles) {
      leaves.push({
        article, hub,
        isNew: article.date === today,
        x: 0, y: 0, vx: 0, vy: 0, r: article.date === today ? 6 : 4.5,
      });
    }
  }

  // 좁은 화면에서는 점을 일부만 그리므로, 집계는 화면이 아니라 데이터 전체로 낸다
  const allArticles = data.trends.flatMap((t) => t.articles);
  const newCount = allArticles.filter((a) => a.date === today).length;
  document.getElementById("meta").textContent =
    `${hubs.length}개 트렌드 · 뉴스 ${allArticles.length}건` +
    (newCount ? ` · 오늘 +${newCount}` : "");
  document.getElementById("foot").textContent =
    `평일 아침 7시, 데일리 브리핑의 뉴스가 자동으로 연결됩니다 · 마지막 갱신 ${data.lastUpdated}`;

  renderHotChips();
  buildGraph();
  runSimulation(true);
}

// ── '지금 활발' 칩 ──────────────────────────
function renderHotChips() {
  const box = document.getElementById("hot");
  box.innerHTML = "";
  const ranked = hubs.filter((h) => h.recent > 0).sort((a, b) => b.recent - a.recent).slice(0, 3);
  ranked.forEach((hub, i) => {
    const chip = document.createElement("button");
    chip.className = "hot__chip" + (i === 0 ? " hot__chip--first" : "");
    chip.innerHTML = `${hub.trend.title} <span class="n">${hub.recent}</span>`;
    chip.title = `지난 ${HOT_DAYS}일간 뉴스 ${hub.recent}건`;
    chip.addEventListener("click", () => selectHub(hub));
    box.appendChild(chip);
  });
}

// ── SVG 구성 ────────────────────────────────
function buildGraph() {
  svg.innerHTML = "";
  const gLinks = mk("g");
  const gLeaves = mk("g");
  const gHubs = mk("g");
  svg.append(gLinks, gLeaves, gHubs);

  for (const leaf of leaves) {
    leaf.line = mk("line");
    gLinks.appendChild(leaf.line);

    leaf.el = mk("circle", { r: leaf.r, class: "leaf" + (leaf.isNew ? " leaf--new" : "") });
    leaf.el.addEventListener("mouseenter", (e) => showTip(e, leafTip(leaf)));
    leaf.el.addEventListener("mousemove", moveTip);
    leaf.el.addEventListener("mouseleave", hideTip);
    leaf.el.addEventListener("click", () => window.open(leaf.article.url, "_blank", "noopener"));
    gLeaves.appendChild(leaf.el);
  }

  // 지난 7일간 가장 활발했던 트렌드 하나에만 초록 배광
  const hottest = hubs.reduce((a, b) => (b.recent > a.recent ? b : a), hubs[0]);
  for (const hub of hubs) {
    if (hub === hottest && hub.recent > 0) {
      hub.halo = mk("circle", { r: hub.r + 10, class: "hub-halo" });
      gHubs.appendChild(hub.halo);
    }
    hub.el = mk("circle", { r: hub.r, class: "hub" });
    hub.el.addEventListener("click", () => selectHub(hub));
    hub.el.addEventListener("mouseenter", (e) => showTip(e, hubTip(hub)));
    hub.el.addEventListener("mousemove", moveTip);
    hub.el.addEventListener("mouseleave", hideTip);
    gHubs.appendChild(hub.el);

    hub.label = mk("text", { class: "hub-label" });
    hub.label.textContent = hub.trend.title;
    hub.sub = mk("text", { class: "hub-sub" });
    hub.sub.textContent = hub.recent > 0 ? `이번 주 +${hub.recent}` : `${hub.trend.articles.length}건`;
    gHubs.append(hub.label, hub.sub);
  }
}

function mk(tag, attrs = {}) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// ── 물리 시뮬레이션 ─────────────────────────
function placeInitial() {
  const { width: W, height: H } = svg.getBoundingClientRect();
  const cx = W / 2, cy = H / 2;
  // 세로로 긴 화면(모바일)에서는 타원으로 시작해 위아래 공간까지 쓰게 한다
  const rx = Math.min(W, H) * 0.30;
  const ry = Math.min(H * 0.34, Math.min(W, H) * 0.54);
  hubs.forEach((hub, i) => {
    const a = (i / hubs.length) * Math.PI * 2 - Math.PI / 2;
    hub.x = cx + Math.cos(a) * rx;
    hub.y = cy + Math.sin(a) * ry;
    hub.vx = hub.vy = 0;
  });
  for (const leaf of leaves) {
    const a = Math.random() * Math.PI * 2;
    const d = leaf.hub.r + 30 + Math.random() * 30;
    leaf.x = leaf.hub.x + Math.cos(a) * d;
    leaf.y = leaf.hub.y + Math.sin(a) * d;
    leaf.vx = leaf.vy = 0;
  }
}

function tick() {
  const { width: W, height: H } = svg.getBoundingClientRect();
  const cx = W / 2, cy = H / 2;

  // 허브 1개가 쓸 수 있는 면적에서 간격을 역산 — 화면이 크면 넓게, 좁으면 촘촘히
  const cell = Math.sqrt((W * H) / hubs.length);
  const gap = Math.min(140, Math.max(66, cell * 0.55));
  const orbit = isNarrow() ? 26 : 34;

  // 허브: 서로 밀어내고, 중심으로 약하게 당김
  for (const a of hubs) {
    a.vx += (cx - a.x) * 0.004;
    a.vy += (cy - a.y) * 0.004;
    for (const b of hubs) {
      if (a === b) continue;
      let dx = a.x - b.x, dy = a.y - b.y;
      let d = Math.hypot(dx, dy) || 1;
      const min = a.r + b.r + gap;
      if (d < min) {
        const f = ((min - d) / d) * 0.045;
        a.vx += dx * f;
        a.vy += dy * f;
      }
    }
  }

  // 잎: 자기 허브에 스프링으로 묶이고, 형제끼리 밀어냄
  for (const leaf of leaves) {
    const hub = leaf.hub;
    let dx = leaf.x - hub.x, dy = leaf.y - hub.y;
    let d = Math.hypot(dx, dy) || 1;
    const rest = hub.r + orbit;
    const f = ((rest - d) / d) * 0.07;
    leaf.vx += dx * f;
    leaf.vy += dy * f;

    for (const other of leaves) {
      if (other === leaf || other.hub !== hub) continue;
      let ox = leaf.x - other.x, oy = leaf.y - other.y;
      let od = Math.hypot(ox, oy) || 1;
      if (od < 17) {
        const of_ = ((17 - od) / od) * 0.22;
        leaf.vx += ox * of_;
        leaf.vy += oy * of_;
      }
    }
    // 남의 허브 안으로 들어가지 않게
    for (const other of hubs) {
      if (other === hub) continue;
      let ox = leaf.x - other.x, oy = leaf.y - other.y;
      let od = Math.hypot(ox, oy) || 1;
      const min = other.r + 16;
      if (od < min) {
        const of_ = ((min - od) / od) * 0.3;
        leaf.vx += ox * of_;
        leaf.vy += oy * of_;
      }
    }
  }

  for (const n of [...hubs, ...leaves]) {
    n.vx *= 0.82;
    n.vy *= 0.82;
    n.x += n.vx;
    n.y += n.vy;
    // 허브는 라벨 폭만큼, 잎은 반지름만큼 여백을 두고 화면 안에 가둔다
    const px = n.pad ? n.pad / 2 : 16;
    const below = n.pad ? 40 : 16; // 허브 아래 두 줄 라벨 공간
    n.x = Math.max(px, Math.min(W - px, n.x));
    n.y = Math.max(24, Math.min(H - below, n.y));
  }
}

function draw() {
  for (const leaf of leaves) {
    leaf.el.setAttribute("cx", leaf.x);
    leaf.el.setAttribute("cy", leaf.y);
    leaf.line.setAttribute("x1", leaf.hub.x);
    leaf.line.setAttribute("y1", leaf.hub.y);
    leaf.line.setAttribute("x2", leaf.x);
    leaf.line.setAttribute("y2", leaf.y);
  }
  for (const hub of hubs) {
    hub.el.setAttribute("cx", hub.x);
    hub.el.setAttribute("cy", hub.y);
    if (hub.halo) {
      hub.halo.setAttribute("cx", hub.x);
      hub.halo.setAttribute("cy", hub.y);
    }
    hub.label.setAttribute("x", hub.x);
    hub.label.setAttribute("y", hub.y + hub.r + 18);
    hub.sub.setAttribute("x", hub.x);
    hub.sub.setAttribute("y", hub.y + hub.r + 33);
  }
}

function runSimulation(animate) {
  if (animId) cancelAnimationFrame(animId);
  placeInitial();
  if (!animate) {
    for (let i = 0; i < 260; i++) tick();
    draw();
    return;
  }
  let step = 0;
  const loop = () => {
    for (let i = 0; i < 3; i++) tick(); // 프레임당 3틱 — 빠르게 안정화
    draw();
    if (++step < 90) animId = requestAnimationFrame(loop);
  };
  animId = requestAnimationFrame(loop);
}

// ── 툴팁 ────────────────────────────────────
function leafTip(leaf) {
  const a = leaf.article;
  const tag = leaf.isNew ? " · 오늘" : "";
  return `${esc(a.title)}<span class="sub">${esc(a.press)} · ${esc(a.date || "")}${tag}</span>`;
}

function hubTip(hub) {
  return `${esc(hub.trend.title)}<span class="sub">${esc(hub.trend.summary)}</span>`;
}

function showTip(e, html) {
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  moveTip(e);
}

function moveTip(e) {
  const rect = svg.getBoundingClientRect();
  let x = e.clientX - rect.left + 14;
  let y = e.clientY - rect.top + 14;
  if (x + tooltip.offsetWidth > rect.width - 8) x -= tooltip.offsetWidth + 28;
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
}

function hideTip() { tooltip.hidden = true; }

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ── 패널 ────────────────────────────────────
function selectHub(hub) {
  selectedHub = hub;
  hubs.forEach((h) => h.el.classList.toggle("hub--selected", h === hub));

  document.getElementById("panel-category").textContent = hub.trend.category;
  document.getElementById("panel-title").textContent = hub.trend.title;
  document.getElementById("panel-since").textContent = `이 흐름의 시작 ${hub.trend.since}`;
  document.getElementById("panel-desc").textContent = hub.trend.desc;
  document.getElementById("panel-more").href =
    `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(hub.trend.query)}`;

  const list = document.getElementById("panel-articles");
  list.innerHTML = "";
  const today = todayKST();
  const sorted = [...hub.trend.articles].sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  for (const article of sorted) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = article.url;
    a.target = "_blank";
    a.rel = "noopener";

    const date = document.createElement("span");
    const isNew = article.date === today;
    date.className = "a-date" + (isNew ? " a-date--new" : "");
    date.textContent = isNew ? "오늘" : (article.date || "").slice(5).replace("-", ".");

    const title = document.createElement("span");
    title.className = "a-title";
    title.textContent = article.title;

    const press = document.createElement("span");
    press.className = "a-press";
    press.textContent = article.press;

    a.append(date, title, press);
    li.appendChild(a);
    list.appendChild(li);
  }
  panel.hidden = false;
}

function closePanel() {
  panel.hidden = true;
  selectedHub = null;
  hubs.forEach((h) => h.el.classList.remove("hub--selected"));
}

document.getElementById("panel-close").addEventListener("click", closePanel);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

// 리사이즈 시 다시 배치 (애니메이션 없이 즉시)
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => runSimulation(false), 200);
});

// 폰트·스타일이 모두 로드된 뒤 최종 배치를 한 번 더 (느린 회선 대비)
window.addEventListener("load", () => {
  if (hubs.length) runSimulation(false);
});

init();
