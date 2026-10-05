/* global LightweightCharts, IndCalc */
const APP_BASE = String(window.APP_BASE || "")
  .trim()
  .replace(/\/$/, "");

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${APP_BASE}${p}`;
}
const cardsEl = document.getElementById("cards");
const feedEl = document.getElementById("feed");
const symbolEl = document.getElementById("symbol");
const tfEl = document.getElementById("tf");
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("detail");
const livePriceEl = document.getElementById("livePrice");
const quoteStatsGrid = document.getElementById("quoteStatsGrid");
const addForm = document.getElementById("addForm");
const delBtn = document.getElementById("delBtn");
const customModal = document.getElementById("customModal");
const customForm = document.getElementById("customForm");
const customType = document.getElementById("customType");
const customFields = document.getElementById("customFields");
const customToggle = document.getElementById("customToggle");

const C_UP = "#ef5350";
const C_DOWN = "#26a69a";
const C_EMA5 = "#f1a340";
const C_EMA10 = "#2166ac";
const C_EMA20 = "#9970ab";
const C_BOLL_UP = "#c9a227";
const C_BOLL_MID = "#8b98a8";
const C_BOLL_LO = "#5b8def";

const CHART_BASE = {
  layout: {
    background: { color: "#121821" },
    textColor: "#c5d0dc",
    attributionLogo: false,
  },
  grid: { vertLines: { color: "#1e2733" }, horzLines: { color: "#1e2733" } },
  rightPriceScale: { borderColor: "#2a3441" },
  timeScale: {
    borderColor: "#2a3441",
    timeVisible: true,
    secondsVisible: false,
  },
  localization: { locale: "zh-CN" },
};

let chart, candleSeries, volumeSeries;
let fsPriceSeries = null; // 分时价
let fsAvgSeries = null; // 分时均价
let emaSeries = { 5: null, 10: null, 20: null };
let bollSeries = { upper: null, mid: null, lower: null };
let panes = {}; // macd/kdj/rsi/custom -> { chart, series... }
let syncing = false;
let fsMode = false;
let fsBaseTs = null; // 分时连续轴起点（当日 09:30 显示时）
let currentCode = null;
let lastSnap = null;
let lastBars = [];
let prevCloseLine = null;

const indEnabled = {
  volume: true,
  boll: false,
  macd: false,
  kdj: false,
  rsi: false,
  custom: false,
};

let customCfg = loadCustomCfg();

function loadCustomCfg() {
  try {
    const raw = localStorage.getItem("juli_custom_ind");
    if (raw) return JSON.parse(raw);
  } catch (_) {}
  return {
    name: "我的指标",
    type: "rsi",
    enabled: false,
    params: { period: 14, fast: 12, slow: 26, signal: 9, emaFast: 5, emaSlow: 20 },
  };
}

function saveCustomCfg() {
  localStorage.setItem("juli_custom_ind", JSON.stringify(customCfg));
}

function actionClass(a) {
  if (a === "买入") return "buy";
  if (a === "卖出") return "sell";
  return "hold";
}

function fmt(p, d = 3) {
  if (p == null || Number.isNaN(Number(p))) return "-";
  return Number(p).toFixed(d);
}

function lineOpts(color) {
  return {
    color,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  };
}

function createBaseChart(el, height) {
  const c = LightweightCharts.createChart(el, {
    ...CHART_BASE,
    height,
    width: el.clientWidth,
  });
  new ResizeObserver(() => {
    c.applyOptions({ width: el.clientWidth, height: el.clientHeight || height });
  }).observe(el);
  return c;
}

function bindTimeSync(source, targets) {
  source.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    for (const t of targets) {
      try { t.timeScale().setVisibleLogicalRange(range); } catch (_) {}
    }
    syncing = false;
  });
}

function syncPanesToMain() {
  if (!chart) return;
  const range = chart.timeScale().getVisibleLogicalRange();
  if (!range) return;
  syncing = true;
  for (const id of ["macd", "kdj", "rsi", "custom"]) {
    const p = panes[id]?.chart;
    if (!p) continue;
    try {
      p.timeScale().setVisibleLogicalRange(range);
    } catch (_) {}
  }
  syncing = false;
}

function dashedLineStyle() {
  const ls = LightweightCharts && LightweightCharts.LineStyle;
  return ls && ls.Dashed != null ? ls.Dashed : 2;
}

function formatFsClock(time) {
  if (fsBaseTs == null || time == null) return "";
  const idx = Math.round((Number(time) - fsBaseTs) / 60);
  if (idx < 0) return "";
  let mins;
  if (idx <= 120) mins = 9 * 60 + 30 + idx;
  else mins = 13 * 60 + (idx - 121);
  const hh = String(Math.floor(mins / 60)).padStart(2, "0");
  const mm = String(mins % 60).padStart(2, "0");
  return `${hh}:${mm}`;
}

function applyTimeScaleMode(isFs, baseTs) {
  fsMode = !!isFs;
  fsBaseTs = isFs ? baseTs : null;
  const opts = isFs && baseTs != null
    ? {
        localization: {
          locale: "zh-CN",
          timeFormatter: (t) => formatFsClock(t),
        },
        timeScale: {
          borderColor: "#2a3441",
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: (t) => formatFsClock(t),
        },
      }
    : {
        localization: { locale: "zh-CN" },
        timeScale: {
          borderColor: "#2a3441",
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: undefined,
        },
      };
  if (chart) chart.applyOptions(opts);
  for (const id of ["macd", "kdj", "rsi", "custom"]) {
    if (panes[id]?.chart) panes[id].chart.applyOptions(opts);
  }
}

function applyMainMargins() {
  if (!chart) return;
  const volOn = !!(indEnabled && indEnabled.volume);
  // 主图价格轴：有成交量时给底部留空，避免量柱盖住 K 线
  try {
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.06, bottom: volOn ? 0.24 : 0.06 },
    });
  } catch (_) {}
  if (volumeSeries) {
    try {
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.78, bottom: 0 },
      });
    } catch (_) {}
  }
}

function priceLineHost() {
  if (fsMode && fsPriceSeries) return fsPriceSeries;
  return candleSeries;
}

function initMainChart() {
  if (typeof LightweightCharts === "undefined") {
    statusEl.textContent = "图表库加载失败，请刷新重试";
    throw new Error("LightweightCharts missing");
  }
  const el = document.getElementById("chart");
  chart = createBaseChart(el, 420);

  candleSeries = chart.addCandlestickSeries({
    upColor: C_UP,
    downColor: C_DOWN,
    borderVisible: false,
    wickUpColor: C_UP,
    wickDownColor: C_DOWN,
  });
  fsPriceSeries = chart.addAreaSeries({
    lineColor: C_UP,
    topColor: "rgba(239,83,80,0.28)",
    bottomColor: "rgba(239,83,80,0.02)",
    lineWidth: 2,
    crosshairMarkerVisible: true,
    lastValueVisible: true,
    priceLineVisible: false,
  });
  fsAvgSeries = chart.addLineSeries({
    color: C_EMA5,
    lineWidth: 1,
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });
  emaSeries[5] = chart.addLineSeries(lineOpts(C_EMA5));
  emaSeries[10] = chart.addLineSeries(lineOpts(C_EMA10));
  emaSeries[20] = chart.addLineSeries(lineOpts(C_EMA20));
  bollSeries.upper = chart.addLineSeries(lineOpts(C_BOLL_UP));
  bollSeries.mid = chart.addLineSeries(lineOpts(C_BOLL_MID));
  bollSeries.lower = chart.addLineSeries(lineOpts(C_BOLL_LO));

  // 成交量画在主图底部：K 线与时间轴之间
  volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
  });
  applyMainMargins();
}

function removePriceLineEverywhere(line) {
  if (!line) return;
  try {
    candleSeries && candleSeries.removePriceLine(line);
  } catch (_) {}
  try {
    fsPriceSeries && fsPriceSeries.removePriceLine(line);
  } catch (_) {}
}

function updatePrevCloseLine(prevClose) {
  const host = priceLineHost();
  if (!host) return;
  removePriceLineEverywhere(prevCloseLine);
  prevCloseLine = null;
  const px = Number(prevClose);
  if (!px || Number.isNaN(px)) return;
  try {
    prevCloseLine = host.createPriceLine({
      price: px,
      color: "#8b98a8",
      lineWidth: 1,
      lineStyle: dashedLineStyle(),
      axisLabelVisible: true,
      title: "昨收",
    });
  } catch (_) {
    prevCloseLine = null;
  }
}

function clearMainOverlays() {
  for (const period of [5, 10, 20]) {
    if (emaSeries[period]) emaSeries[period].setData([]);
  }
  if (bollSeries.upper) {
    bollSeries.upper.setData([]);
    bollSeries.mid.setData([]);
    bollSeries.lower.setData([]);
  }
}

function visibleSubPanes() {
  const ids = [];
  if (indEnabled.macd) ids.push("macd");
  if (indEnabled.kdj) ids.push("kdj");
  if (indEnabled.rsi) ids.push("rsi");
  if (indEnabled.custom && customCfg.enabled) ids.push("custom");
  return ids;
}

/** 只保留最底部一条时间轴，避免每个指标各带一根 */
function layoutTimeAxes() {
  const subs = visibleSubPanes();
  const showMainAxis = subs.length === 0;
  if (chart) {
    chart.timeScale().applyOptions({
      visible: showMainAxis,
      borderVisible: showMainAxis,
    });
  }
  for (const id of ["macd", "kdj", "rsi", "custom"]) {
    const p = panes[id];
    if (!p?.chart) continue;
    const isLast = subs.length > 0 && id === subs[subs.length - 1];
    p.chart.timeScale().applyOptions({
      visible: isLast,
      borderVisible: isLast,
    });
  }
}

function initPane(id, builder) {
  const el = document.getElementById(`pane-${id}`);
  const c = createBaseChart(el, 120);
  const series = builder(c);
  panes[id] = { chart: c, ...series };
  return panes[id];
}

function ensurePanes() {
  if (!panes.macd) {
    initPane("macd", (c) => ({
      dif: c.addLineSeries(lineOpts("#f1a340")),
      dea: c.addLineSeries(lineOpts("#2166ac")),
      hist: c.addHistogramSeries({ priceFormat: { type: "price", precision: 3, minMove: 0.001 } }),
    }));
  }
  if (!panes.kdj) {
    initPane("kdj", (c) => ({
      k: c.addLineSeries(lineOpts("#f1a340")),
      d: c.addLineSeries(lineOpts("#2166ac")),
      j: c.addLineSeries(lineOpts("#ef5350")),
    }));
  }
  if (!panes.rsi) {
    initPane("rsi", (c) => ({
      rsi: c.addLineSeries(lineOpts("#9970ab")),
    }));
  }
  if (!panes.custom) {
    initPane("custom", (c) => ({
      line: c.addLineSeries(lineOpts("#5b8def")),
      hist: c.addHistogramSeries({ priceFormat: { type: "price", precision: 3, minMove: 0.001 } }),
      dif: c.addLineSeries(lineOpts("#f1a340")),
      dea: c.addLineSeries(lineOpts("#2166ac")),
    }));
  }

  const all = [chart, panes.macd.chart, panes.kdj.chart, panes.rsi.chart, panes.custom.chart];
  if (!chart.__syncBound) {
    for (const src of all) {
      bindTimeSync(src, all.filter((x) => x !== src));
    }
    chart.__syncBound = true;
  }
}

function setPaneVisible(name, visible) {
  const wrap = document.querySelector(`.ind-pane-wrap[data-pane="${name}"]`);
  if (wrap) wrap.hidden = !visible;
}

/** 副图增减时，主图高度跟随伸缩（关掉副图后主图变高） */
function layoutChartHeights() {
  const subs = visibleSubPanes();
  const paneCount = subs.length;
  // 成交量在主图内，不再占独立副图高度
  const stackTarget = Math.max(560, Math.min(820, window.innerHeight - 220));
  const paneUnit = 118;
  const mainH = Math.max(320, stackTarget - paneCount * paneUnit);
  const paneH = 108;

  const mainEl = document.getElementById("chart");
  if (mainEl) {
    mainEl.style.height = `${mainH}px`;
    if (chart) chart.applyOptions({ height: mainH, width: mainEl.clientWidth });
  }
  for (const id of ["macd", "kdj", "rsi", "custom"]) {
    const el = document.getElementById(`pane-${id}`);
    if (!el) continue;
    el.style.height = `${paneH}px`;
    if (panes[id]?.chart) {
      panes[id].chart.applyOptions({ height: paneH, width: el.clientWidth });
    }
  }
  const board = document.getElementById("detail");
  if (board) {
    board.style.maxHeight = `${mainH + paneCount * paneUnit + 56}px`;
  }
  layoutTimeAxes();
  applyMainMargins();
}

function applyIndVisibility() {
  // 成交量在主图内，隐藏独立 volume 副图容器
  setPaneVisible("volume", false);
  setPaneVisible("macd", indEnabled.macd);
  setPaneVisible("kdj", indEnabled.kdj);
  setPaneVisible("rsi", indEnabled.rsi);
  setPaneVisible("custom", indEnabled.custom && customCfg.enabled);
  customToggle.checked = indEnabled.custom && customCfg.enabled;
  document.getElementById("customPaneTitle").textContent = customCfg.name || "自定义";
  if (!indEnabled.boll) {
    bollSeries.upper.setData([]);
    bollSeries.mid.setData([]);
    bollSeries.lower.setData([]);
  }
  if (!indEnabled.volume && volumeSeries) {
    volumeSeries.setData([]);
  }
  layoutChartHeights();
}

function renderLivePrice(quote) {
  const val = livePriceEl.querySelector(".lp-value");
  const chg = livePriceEl.querySelector(".lp-chg");
  if (!quote) {
    val.textContent = "--";
    val.className = "lp-value";
    chg.textContent = "";
    chg.className = "lp-chg";
    renderQuoteStats(null);
    return;
  }
  const pct = Number(quote.change_pct || 0);
  const dir = pct > 0 ? "up" : pct < 0 ? "down" : "";
  val.textContent = fmt(quote.price, 2);
  val.className = "lp-value" + (dir ? " " + dir : "");
  chg.textContent = `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
  chg.className = "lp-chg" + (dir ? " " + dir : "");
  renderQuoteStats(quote);
}

function renderQuoteStats(quote) {
  if (!quoteStatsGrid) return;
  if (!quote) {
    quoteStatsGrid.textContent = "等待行情…";
    return;
  }
  const rows = [
    ["今开", fmt(quote.open, 2)],
    ["最高", fmt(quote.high, 2)],
    ["最低", fmt(quote.low, 2)],
    ["昨收", fmt(quote.prev_close, 2)],
    ["涨跌额", quote.change == null ? "-" : fmt(quote.change, 2)],
    ["涨跌幅", `${Number(quote.change_pct || 0).toFixed(2)}%`],
    ["成交量", quote.volume == null ? "-" : `${fmt(quote.volume, 0)}手`],
    ["成交额", quote.amount == null ? "-" : `${fmt(quote.amount, 0)}万`],
    ["换手率", quote.turnover == null ? "-" : `${fmt(quote.turnover, 2)}%`],
    ["振幅", quote.amplitude == null ? "-" : `${fmt(quote.amplitude, 2)}%`],
  ];
  quoteStatsGrid.innerHTML = rows
    .map(([k, v]) => `<div class="qs-item"><span>${k}</span><b>${v}</b></div>`)
    .join("");
}

function renderBoard(sit, timeframe, timeframeLabel) {
  if (!sit) {
    detailEl.innerHTML = `<div class="kv">等待局面数据…</div>`;
    return;
  }
  const rs = (sit.resistances || []).slice(0, 3);
  const ss = (sit.supports || []).slice(0, 3);
  const tfLabel =
    timeframeLabel ||
    ({ fs: "分时", "1m": "1 分钟", "5m": "5 分钟", day: "日线" }[timeframe] || timeframe);
  detailEl.innerHTML = `
    <div class="head">
      <div class="kv"><span>建议</span><b><span class="badge ${actionClass(sit.action)}">${sit.action}</span></b></div>
      <div class="kv"><span>趋势</span><b>${sit.trend}</b></div>
      <div class="kv"><span>周期</span><b>${tfLabel}</b></div>
      <div class="kv"><span>置信度</span><b>${sit.confidence}</b></div>
    </div>
    <div class="ladder">
      <div class="row r"><span class="lab">第三压力</span><span class="val">${fmt(rs[2], 2)}</span></div>
      <div class="row r"><span class="lab">第二压力</span><span class="val">${fmt(rs[1], 2)}</span></div>
      <div class="row r">
        <span class="lab">第一压力</span>
        <span class="val"><b class="lv">${fmt(rs[0], 2)}</b>
          <span class="tag">突破确认 ${sit.breakout_confirmed ? "是" : "否"}</span>
          <span class="tag ${sit.breakout_failed ? "warn" : "no"}">突破失败 ${sit.breakout_failed ? "是" : "否"}</span>
        </span>
      </div>
      <div class="row px"><span class="lab">现价</span><span class="val">${fmt(sit.price, 2)}</span></div>
      <div class="row s">
        <span class="lab">第一支撑</span>
        <span class="val"><b class="lv">${fmt(ss[0], 2)}</b>
          <span class="tag ${sit.holding_support ? "yes" : "no"}">站稳 ${sit.holding_support ? "是" : "否"}</span>
          <span class="tag ${sit.support_broken ? "warn" : "no"}">跌破 ${sit.support_broken ? "是" : "否"}</span>
        </span>
      </div>
      <div class="row s"><span class="lab">第二支撑</span><span class="val">${fmt(ss[1], 2)}</span></div>
      <div class="row s"><span class="lab">第三支撑</span><span class="val">${fmt(ss[2], 2)}</span></div>
      <div class="lock-hint">本周期三档已锁定${sit.levels_version ? ` v${sit.levels_version}` : ""}；换周期用另一套；仅突破压3/跌破撑3 才重置</div>
    </div>
    <div class="foot">
      <div>止损 ${fmt(sit.stop_loss)}　｜　止盈 ${fmt(sit.take_profit)}</div>
      <div>${sit.reason || ""}</div>
      <div style="margin-top:6px">EMA5 <span style="color:${C_EMA5}">●</span>
        EMA10 <span style="color:${C_EMA10}">●</span>
        EMA20 <span style="color:${C_EMA20}">●</span>
        ${indEnabled.boll ? `BOLL <span style="color:${C_BOLL_UP}">●</span>` : ""}</div>
    </div>
  `;
}

function paintIndicators(bars) {
  if (!bars.length) return;
  ensurePanes();
  applyIndVisibility();

  const times = bars.map((b) => b.time);
  const closes = bars.map((b) => b.close);

  const fill = { fillLeading: true };

  // 分时主图用价线，不叠 EMA/BOLL（避免假蜡烛均线干扰）
  if (fsMode) {
    clearMainOverlays();
  } else {
    for (const period of [5, 10, 20]) {
      emaSeries[period].setData(IndCalc.toLine(times, IndCalc.ema(closes, period), fill));
    }
    if (indEnabled.boll) {
      const b = IndCalc.boll(closes, 20, 2);
      bollSeries.upper.setData(IndCalc.toLine(times, b.upper, fill));
      bollSeries.mid.setData(IndCalc.toLine(times, b.mid, fill));
      bollSeries.lower.setData(IndCalc.toLine(times, b.lower, fill));
    }
  }

  if (indEnabled.volume && volumeSeries) {
    volumeSeries.setData(
      bars.map((b) => ({
        time: b.time,
        value: b.volume,
        color: b.close >= b.open ? "rgba(239,83,80,0.55)" : "rgba(38,166,154,0.55)",
      }))
    );
  } else if (volumeSeries) {
    volumeSeries.setData([]);
  }

  if (indEnabled.macd && panes.macd) {
    const m = IndCalc.macd(closes, 12, 26, 9);
    panes.macd.dif.setData(IndCalc.toLine(times, m.dif, fill));
    panes.macd.dea.setData(IndCalc.toLine(times, m.dea, fill));
    panes.macd.hist.setData(IndCalc.toHist(times, m.hist, "rgba(239,83,80,0.55)", "rgba(38,166,154,0.55)", fill));
  }

  if (indEnabled.kdj && panes.kdj) {
    const k = IndCalc.kdj(bars, 9, 3, 3);
    panes.kdj.k.setData(IndCalc.toLine(times, k.k, fill));
    panes.kdj.d.setData(IndCalc.toLine(times, k.d, fill));
    panes.kdj.j.setData(IndCalc.toLine(times, k.j, fill));
  }

  if (indEnabled.rsi && panes.rsi) {
    panes.rsi.rsi.setData(IndCalc.toLine(times, IndCalc.rsi(closes, 14), fill));
  }

  if (indEnabled.custom && customCfg.enabled && panes.custom) {
    paintCustom(times, closes, bars);
  }
}

function paintCustom(times, closes, bars) {
  const p = customCfg.params || {};
  const fill = { fillLeading: true };
  panes.custom.line.setData([]);
  panes.custom.hist.setData([]);
  panes.custom.dif.setData([]);
  panes.custom.dea.setData([]);

  if (customCfg.type === "rsi") {
    panes.custom.line.setData(IndCalc.toLine(times, IndCalc.rsi(closes, Number(p.period) || 14), fill));
  } else if (customCfg.type === "macd") {
    const m = IndCalc.macd(closes, Number(p.fast) || 12, Number(p.slow) || 26, Number(p.signal) || 9);
    panes.custom.dif.setData(IndCalc.toLine(times, m.dif, fill));
    panes.custom.dea.setData(IndCalc.toLine(times, m.dea, fill));
    panes.custom.hist.setData(IndCalc.toHist(times, m.hist, "rgba(239,83,80,0.55)", "rgba(38,166,154,0.55)", fill));
  } else if (customCfg.type === "ema_diff") {
    const diff = IndCalc.emaDiff(closes, Number(p.emaFast) || 5, Number(p.emaSlow) || 20);
    panes.custom.hist.setData(IndCalc.toHist(times, diff, "rgba(239,83,80,0.55)", "rgba(38,166,154,0.55)", fill));
    panes.custom.line.setData(IndCalc.toLine(times, diff, fill));
  }
}

async function loadWatchlist(preferCode) {
  const r = await fetch(apiUrl("/api/watchlist"));
  const data = await r.json();
  const prev = preferCode || currentCode || symbolEl.value;
  symbolEl.innerHTML = "";
  for (const s of data.stocks) {
    const opt = document.createElement("option");
    opt.value = s.code;
    opt.textContent = `${s.code} ${s.name || ""}`;
    symbolEl.appendChild(opt);
  }
  if (!data.stocks.length) {
    currentCode = null;
    return data;
  }
  currentCode = data.stocks.some((s) => s.code === prev) ? prev : data.stocks[0].code;
  symbolEl.value = currentCode;
  return data;
}

async function loadKline() {
  if (!currentCode) {
    renderLivePrice(null);
    detailEl.innerHTML = `<div class="kv">请先添加自选股</div>`;
    return;
  }
  const tf = tfEl.value;
  try {
    const r = await fetch(apiUrl(`/api/kline/${currentCode}?tf=${encodeURIComponent(tf)}`));
    if (!r.ok) {
      statusEl.textContent = "等待 K 线…";
      return;
    }
    const data = await r.json();
    renderLivePrice(data.quote);
    renderBoard(data.situation, data.timeframe, data.timeframe_label);

    lastBars = data.bars || [];
    const isFs = String(data.timeframe || tf).toLowerCase() === "fs";
    const baseTs = lastBars.length ? lastBars[0].time : null;
    applyTimeScaleMode(isFs, baseTs);

    if (isFs) {
      if (candleSeries) candleSeries.setData([]);
      clearMainOverlays();
      const prev = Number(data.quote && data.quote.prev_close);
      const lastClose = lastBars.length ? lastBars[lastBars.length - 1].close : prev;
      const up = !(prev > 0) || lastClose >= prev;
      if (fsPriceSeries) {
        fsPriceSeries.applyOptions({
          lineColor: up ? C_UP : C_DOWN,
          topColor: up ? "rgba(239,83,80,0.28)" : "rgba(38,166,154,0.28)",
          bottomColor: up ? "rgba(239,83,80,0.02)" : "rgba(38,166,154,0.02)",
        });
        fsPriceSeries.setData(lastBars.map((b) => ({ time: b.time, value: b.close })));
      }
      if (fsAvgSeries) {
        fsAvgSeries.setData(
          lastBars
            .filter((b) => b.avg != null && !Number.isNaN(Number(b.avg)))
            .map((b) => ({ time: b.time, value: Number(b.avg) }))
        );
      }
    } else {
      if (fsPriceSeries) fsPriceSeries.setData([]);
      if (fsAvgSeries) fsAvgSeries.setData([]);
      if (!candleSeries) throw new Error("candleSeries missing");
      candleSeries.setData(
        lastBars.map((b) => ({
          time: b.time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        }))
      );
    }

    updatePrevCloseLine(data.quote && data.quote.prev_close);
    paintIndicators(lastBars);

    statusEl.textContent = `已更新 ${new Date().toLocaleTimeString()}`;
    chart.timeScale().fitContent();
    syncPanesToMain();
  } catch (e) {
    console.error(e);
    statusEl.textContent = "图表更新失败";
  }
}

function renderCards(snapshot) {
  const sits = snapshot.situations || {};
  const codes = Object.keys(sits);
  if (!codes.length) {
    cardsEl.innerHTML = `<div class="meta">等待行情…</div>`;
    return;
  }
  cardsEl.innerHTML = "";
  for (const code of codes) {
    const s = sits[code];
    const div = document.createElement("div");
    div.className = "card" + (code === currentCode ? " active" : "");
    div.innerHTML = `
      <div class="row">
        <div class="name">${s.name || code} <span style="color:#8b98a8;font-weight:400">${code}</span></div>
        <span class="badge ${actionClass(s.action)}">${s.action}</span>
      </div>
      <div class="meta">
        现价 ${fmt(s.price)}｜趋势 ${s.trend}<br/>
        R1 ${fmt(s.resistances?.[0])}｜S1 ${fmt(s.supports?.[0])}
      </div>`;
    div.onclick = () => {
      currentCode = code;
      symbolEl.value = code;
      loadKline();
      renderCards(snapshot);
    };
    cardsEl.appendChild(div);
  }
}

function stripLeadingAction(title, action) {
  let t = String(title || "").trim();
  if (!t) return "";
  const acts = action ? [action, "买入", "卖出", "观望"] : ["买入", "卖出", "观望"];
  const seen = new Set();
  for (const a of acts) {
    if (!a || seen.has(a)) continue;
    seen.add(a);
    if (t.startsWith(a + "：") || t.startsWith(a + ":")) {
      t = t.slice(a.length + 1).trim();
      break;
    }
    if (t.startsWith(a + " ")) {
      t = t.slice(a.length).trim();
      break;
    }
    if (t === a) {
      t = "";
      break;
    }
  }
  return t;
}

function renderFeed(snapshot) {
  const events = (snapshot.events || []).slice().reverse();
  feedEl.innerHTML = "";
  for (const ev of events.slice(0, 8)) {
    const div = document.createElement("div");
    const action = ev.action || "";
    let tone = "hold";
    if (action === "买入" || ev.kind === "buy") tone = "buy";
    else if (action === "卖出" || ev.kind === "sell") tone = "sell";
    else if (ev.kind === "checklist") tone = "checklist";
    div.className = "ev " + tone;
    const t = new Date(ev.ts * 1000).toLocaleTimeString();
    const actLabel = action ? `<span class="ev-action">${action}</span>` : "";
    const titleText = stripLeadingAction(ev.title || "", action);
    let bodyHtml = "";
    if (ev.html) {
      // 去掉正文里的「指令：xxx」，避免与左侧指令标签重复
      bodyHtml = String(ev.html).replace(
        /<div class="chk-act[^"]*">[\s\S]*?<\/div>/g,
        ""
      );
    } else if (ev.detail && ev.detail !== ev.title) {
      const detail = String(ev.detail)
        .replace(/\n?建议：(?:买入|卖出|观望)\s*$/m, "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "<br/>");
      bodyHtml = detail ? `<div class="ev-body">${detail}</div>` : "";
    }
    if (ev.html && bodyHtml) {
      bodyHtml = `<div class="ev-body">${bodyHtml}</div>`;
    }
    div.innerHTML = `<div><span class="ev-time">${t}</span>${actLabel}<span class="ev-title">${
      titleText
    }</span></div>${bodyHtml}`;
    feedEl.appendChild(div);
  }
}

async function tick() {
  try {
    const r = await fetch(apiUrl("/api/snapshot"));
    lastSnap = await r.json();
    renderCards(lastSnap);
    renderFeed(lastSnap);
    const q = lastSnap.quotes?.[currentCode];
    if (q) renderLivePrice(q);
    await loadKline();
  } catch (e) {
    statusEl.textContent = "拉取失败";
  }
}

function renderCustomFields() {
  const p = customCfg.params || {};
  const type = customType.value;
  if (type === "rsi") {
    customFields.innerHTML = `<label>RSI 周期 <input name="period" type="number" min="2" max="100" value="${p.period || 14}" /></label>`;
  } else if (type === "macd") {
    customFields.innerHTML = `
      <label>快线 <input name="fast" type="number" min="2" value="${p.fast || 12}" /></label>
      <label>慢线 <input name="slow" type="number" min="3" value="${p.slow || 26}" /></label>
      <label>信号 <input name="signal" type="number" min="2" value="${p.signal || 9}" /></label>`;
  } else {
    customFields.innerHTML = `
      <label>EMA 快 <input name="emaFast" type="number" min="2" value="${p.emaFast || 5}" /></label>
      <label>EMA 慢 <input name="emaSlow" type="number" min="3" value="${p.emaSlow || 20}" /></label>`;
  }
}

function openCustomModal() {
  customForm.name.value = customCfg.name || "我的指标";
  customType.value = customCfg.type || "rsi";
  customForm.enabled.checked = !!customCfg.enabled;
  renderCustomFields();
  customModal.hidden = false;
}

function closeCustomModal() {
  customModal.hidden = true;
}

// indicator toggles
document.getElementById("indBar").addEventListener("change", (e) => {
  const t = e.target;
  if (!(t instanceof HTMLInputElement) || !t.dataset.ind) return;
  const key = t.dataset.ind;
  indEnabled[key] = t.checked;
  if (key === "custom") {
    customCfg.enabled = t.checked;
    saveCustomCfg();
  }
  applyIndVisibility();
  if (lastBars.length) paintIndicators(lastBars);
});

document.getElementById("customCfgBtn").addEventListener("click", openCustomModal);
document.getElementById("customModalClose").addEventListener("click", closeCustomModal);
document.getElementById("customCancel").addEventListener("click", closeCustomModal);
customType.addEventListener("change", renderCustomFields);
customModal.addEventListener("click", (e) => {
  if (e.target === customModal) closeCustomModal();
});

customForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const fd = new FormData(customForm);
  customCfg.name = String(fd.get("name") || "我的指标");
  customCfg.type = String(fd.get("type") || "rsi");
  customCfg.enabled = !!fd.get("enabled");
  customCfg.params = {
    ...customCfg.params,
    period: Number(fd.get("period") || 14),
    fast: Number(fd.get("fast") || 12),
    slow: Number(fd.get("slow") || 26),
    signal: Number(fd.get("signal") || 9),
    emaFast: Number(fd.get("emaFast") || 5),
    emaSlow: Number(fd.get("emaSlow") || 20),
  };
  indEnabled.custom = customCfg.enabled;
  saveCustomCfg();
  closeCustomModal();
  applyIndVisibility();
  if (lastBars.length) paintIndicators(lastBars);
});

symbolEl.addEventListener("change", () => {
  currentCode = symbolEl.value;
  loadKline();
});
tfEl.addEventListener("change", loadKline);

addForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = document.getElementById("addCode").value.trim();
  const body = { code };
  const r = await fetch(apiUrl("/api/watchlist"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    alert(data.detail || "添加失败");
    return;
  }
  document.getElementById("addCode").value = "";
  await loadWatchlist(data.stock?.code || code);
  await tick();
});

delBtn.addEventListener("click", async () => {
  if (!currentCode) return;
  if (!confirm(`确认删除自选 ${currentCode}？`)) return;
  const r = await fetch(apiUrl(`/api/watchlist/${currentCode}`), { method: "DELETE" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    alert(data.detail || "删除失败");
    return;
  }
  await loadWatchlist();
  await tick();
});

// sync checkbox UI
for (const key of ["volume", "boll", "macd", "kdj", "rsi"]) {
  const el = document.querySelector(`input[data-ind="${key}"]`);
  if (el) el.checked = !!indEnabled[key];
}
indEnabled.custom = !!customCfg.enabled;
customToggle.checked = indEnabled.custom;

try {
  initMainChart();
  ensurePanes();
  applyIndVisibility();
  window.addEventListener("resize", () => layoutChartHeights());
  loadWatchlist()
    .then(tick)
    .catch((e) => {
      console.error(e);
      statusEl.textContent = "初始化失败";
    });
  setInterval(tick, 5000);
} catch (e) {
  console.error(e);
  statusEl.textContent = "图表初始化失败，请刷新";
}
