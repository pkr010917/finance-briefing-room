// ===== 상단 날짜 + 실시간 시계 =====
const DAYS = ["일", "월", "화", "수", "목", "금", "토"];

function pad(n) {
  return String(n).padStart(2, "0");
}

function tick() {
  const now = new Date();
  document.getElementById("clock").textContent =
    `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  document.getElementById("today-date").textContent =
    `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())} (${DAYS[now.getDay()]}) KST`;
}
tick();
setInterval(tick, 1000);

// ===== 데이터 불러오기 (data.json) =====
let TRENDS = [];
let selectedIndex = 0;
const list = document.getElementById("trend-list");

async function init() {
  const res = await fetch("data.json");
  const data = await res.json();
  TRENDS = data.trends;

  document.getElementById("trend-count").textContent = `[${TRENDS.length}]`;
  document.getElementById("status-left").textContent =
    `${TRENDS.length}개 트렌드 추적 중 · 데이터 갱신 ${data.lastUpdated}`;

  TRENDS.forEach((trend, index) => {
    const row = document.createElement("button");
    row.className = "row";
    row.dataset.index = index;
    row.innerHTML = `
      <span class="row__top">
        <span class="row__index">${pad(index + 1)}</span>
        <span class="row__title">${trend.title}</span>
        <span class="row__since">${trend.since}</span>
      </span>
      <span class="row__summary">${trend.summary}</span>
      <span class="row__category">${trend.category}</span>
    `;
    row.addEventListener("click", () => select(index));
    list.appendChild(row);
  });

  // 첫 화면부터 상세가 채워져 있도록 첫 번째 트렌드 선택
  select(0);
}

// ===== 오른쪽 상세 패널 채우기 =====
function select(index) {
  selectedIndex = index;
  const trend = TRENDS[index];

  list.querySelectorAll(".row").forEach((row, i) => {
    row.classList.toggle("is-selected", i === index);
  });

  document.getElementById("detail-category").textContent = trend.category;
  document.getElementById("detail-title").textContent = trend.title;
  document.getElementById("detail-since").textContent = `이 흐름의 시작: ${trend.since}`;
  document.getElementById("detail-desc").textContent = trend.desc;

  const watchList = document.getElementById("detail-watch");
  watchList.innerHTML = "";
  trend.watch.forEach((point) => {
    const li = document.createElement("li");
    li.textContent = point;
    watchList.appendChild(li);
  });

  const articleList = document.getElementById("detail-articles");
  articleList.innerHTML = "";
  trend.articles.forEach((article) => {
    const li = document.createElement("li");
    const link = document.createElement("a");
    link.href = article.url;
    link.target = "_blank";
    link.rel = "noopener";

    const title = document.createElement("span");
    title.className = "article-title";
    title.textContent = article.title;

    const press = document.createElement("span");
    press.className = "article-press";
    press.textContent = article.press;

    link.appendChild(title);
    link.appendChild(press);
    li.appendChild(link);
    articleList.appendChild(li);
  });

  document.getElementById("detail-more").href =
    `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(trend.query)}`;

  if (window.matchMedia("(max-width: 860px)").matches) {
    document.getElementById("detail").scrollIntoView({ behavior: "smooth" });
  }
}

// ===== 키보드 탐색: ↑↓로 이동 =====
document.addEventListener("keydown", (e) => {
  if (!TRENDS.length) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    select(Math.min(selectedIndex + 1, TRENDS.length - 1));
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    select(Math.max(selectedIndex - 1, 0));
  }
});

init();
