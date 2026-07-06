/* NewsPulse dashboard — plain JS, no build step. Reads docs/data/*.json. */
"use strict";

const app = document.getElementById("app");
const state = { stats: null, index: null, charts: {} };

const C = {
  green: "#2ecc80",
  red: "#f6626c",
  blue: "#4f8ff7",
  amber: "#e3a63b",
  muted: "#8a94a6",
  grid: "#222b3a",
};

/* ------------------------------------------------------------ utilities */
async function fetchJSON(path) {
  const resp = await fetch(path, { cache: "no-store" });
  if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
  return resp.json();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function pct(rate) {
  return rate && rate.pct !== null && rate.pct !== undefined ? `${rate.pct.toFixed(1)}%` : "—";
}

function ratio(rate) {
  return rate && rate.total ? `${rate.success}/${rate.total}` : "0/0";
}

function inr(value, signed = false) {
  const sign = value < 0 ? "−" : signed && value > 0 ? "+" : "";
  return `${sign}₹${Math.abs(value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function signedPct(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(digits)}%`;
}

function mark(flag) {
  if (flag === true) return "✓";
  if (flag === false) return "✗";
  return "–";
}

function chipCls(flag) {
  if (flag === true) return "chip pass";
  if (flag === false) return "chip fail";
  return "chip";
}

function hhmm(ts) {
  const s = String(ts ?? "");
  return s.length >= 16 ? s.slice(11, 16) : s;
}

function destroyCharts() {
  Object.values(state.charts).forEach((c) => c && c.destroy());
  state.charts = {};
}

function setNav(active) {
  for (const id of ["nav-home", "nav-history"]) {
    document.getElementById(id).classList.toggle("active", id === active);
  }
}

function demoBanner() {
  if (!state.stats || !state.stats.demo) return "";
  return `<div class="demo-banner">🧪 <span><strong>Sample data.</strong>
    These are canned example days so you can see the layout. Deploy the repo to GitHub
    (free — see README) and real predictions replace this after the first verified session.
    </span></div>`;
}

/* --------------------------------------------------------------- charts */
function barChart(canvasId, labels, values, colors, horizontal = false) {
  return new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderRadius: 7,
        maxBarThickness: 44,
      }],
    },
    options: {
      maintainAspectRatio: false,
      indexAxis: horizontal ? "y" : "x",
      scales: {
        [horizontal ? "x" : "y"]: {
          min: 0, max: 100,
          ticks: { callback: (v) => v + "%" },
          grid: { color: C.grid },
        },
        [horizontal ? "y" : "x"]: { grid: { display: false } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => ` ${(horizontal ? c.parsed.x : c.parsed.y).toFixed(1)}% success`,
          },
        },
      },
    },
  });
}

/* ----------------------------------------------------------------- home */
function renderHome() {
  setNav("nav-home");
  destroyCharts();
  const s = state.stats || {};
  const m3 = s.metrics ? s.metrics.m3_tradeable.overall : null;
  const sim = s.simulated || {};
  const streak = s.streak || { type: "none", count: 0 };
  const streakTxt = streak.type === "none" ? "—"
    : `${streak.type === "win" ? "W" : "L"}${streak.count}`;
  const pnl = sim.total_pnl_inr ?? 0;
  const latest = (state.index || [])[0];

  app.innerHTML = `
    ${demoBanner()}
    <div class="cards">
      <div class="stat-card ${m3 && m3.pct >= 50 ? "c-green" : ""}">
        <div class="label">Primary success (m3)</div>
        <div class="value">${pct(m3)}</div>
        <div class="sub">${ratio(m3)} — held direction 09:30 → 15:10</div>
      </div>
      <div class="stat-card">
        <div class="label">Total predictions</div>
        <div class="value">${s.total_predictions ?? 0}</div>
        <div class="sub">${s.sessions ?? 0} sessions scored</div>
      </div>
      <div class="stat-card ${pnl >= 0 ? "c-green" : "c-red"}">
        <div class="label">Simulated P&amp;L</div>
        <div class="value ${pnl >= 0 ? "pos" : "neg"}">${inr(pnl, true)}</div>
        <div class="sub">${sim.wins ?? 0}W / ${sim.losses ?? 0}L / ${sim.no_entry ?? 0} skipped ·
          ₹${(s.notional_per_trade_inr ?? 10000).toLocaleString("en-IN")} per trade</div>
      </div>
      <div class="stat-card ${streak.type === "win" ? "c-green" : streak.type === "loss" ? "c-red" : "c-amber"}">
        <div class="label">Current streak</div>
        <div class="value ${streak.type === "win" ? "pos" : streak.type === "loss" ? "neg" : ""}">${streakTxt}</div>
        <div class="sub">Rolling last 20 sessions: ${s.rolling_last20 ? pct(s.rolling_last20.m3) : "—"}</div>
      </div>
    </div>

    ${latest ? `
    <div class="latest-strip">
      <span>Latest session <strong>${esc(latest.date)}</strong></span>
      <span class="badge ${esc(latest.bias)}">${esc(latest.bias)}</span>
      <span>${latest.picks} picks · m3 ${latest.m3_success}/${latest.m3_total}</span>
      ${latest.day_pnl_inr !== undefined
        ? `<span class="${latest.day_pnl_inr >= 0 ? "pos" : "neg"}">
             ${inr(latest.day_pnl_inr, true)}</span>` : ""}
      <a class="go" href="#/day/${esc(latest.date)}">View day →</a>
    </div>` : ""}

    <h2>Simulated equity curve <span class="badge no_entry">₹${(s.notional_per_trade_inr ?? 10000).toLocaleString("en-IN")} notional / trade</span></h2>
    <div class="panel"><div class="chart-wrap"><canvas id="equityChart"></canvas></div></div>

    <div class="grid-2">
      <div>
        <h2>m3 success by catalyst</h2>
        <div class="panel"><div class="chart-wrap"><canvas id="catalystChart"></canvas></div></div>
      </div>
      <div>
        <h2>m3 success by direction</h2>
        <div class="panel"><div class="chart-wrap"><canvas id="directionChart"></canvas></div></div>
      </div>
      <div>
        <h2>m3 success by confidence</h2>
        <div class="panel"><div class="chart-wrap"><canvas id="confidenceChart"></canvas></div></div>
      </div>
      <div>
        <h2>Metric comparison (overall)</h2>
        <div class="panel"><div class="chart-wrap"><canvas id="metricChart"></canvas></div></div>
      </div>
    </div>`;

  if (!s.metrics) {
    app.innerHTML = `${demoBanner()}
      <div class="panel loading">No verified sessions yet — the scoreboard fills in
      automatically after the first 16:00 IST <code>verify</code> run.</div>`;
    return;
  }

  Chart.defaults.color = C.muted;
  Chart.defaults.borderColor = C.grid;
  Chart.defaults.font.family = "'Inter', sans-serif";

  const curve = sim.equity_curve || [];
  state.charts.equity = new Chart(document.getElementById("equityChart"), {
    type: "line",
    data: {
      labels: curve.map((p) => p.date),
      datasets: [{
        label: "Cumulative P&L (₹)",
        data: curve.map((p) => p.cum_pnl_inr),
        borderColor: C.blue,
        borderWidth: 2.5,
        pointRadius: 3,
        pointBackgroundColor: C.blue,
        fill: true,
        tension: 0.3,
        backgroundColor: (ctx) => {
          const { ctx: canvas, chartArea } = ctx.chart;
          if (!chartArea) return "rgba(79,143,247,0.10)";
          const g = canvas.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, "rgba(79,143,247,0.32)");
          g.addColorStop(1, "rgba(79,143,247,0.01)");
          return g;
        },
      }],
    },
    options: {
      maintainAspectRatio: false,
      scales: {
        y: { grid: { color: C.grid }, ticks: { callback: (v) => "₹" + v } },
        x: { grid: { display: false } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => {
              const point = curve[c.dataIndex] || {};
              return ` cumulative ${inr(c.parsed.y, true)} (day ${inr(point.day_pnl_inr ?? 0, true)})`;
            },
          },
        },
      },
    },
  });

  const byCatalyst = s.metrics.m3_tradeable.by_catalyst || {};
  state.charts.catalyst = barChart(
    "catalystChart",
    Object.keys(byCatalyst),
    Object.values(byCatalyst).map((r) => r.pct ?? 0),
    C.green,
  );

  const byDir = s.metrics.m3_tradeable.by_direction || {};
  state.charts.direction = barChart(
    "directionChart",
    ["LONG", "SHORT"],
    [byDir.LONG?.pct ?? 0, byDir.SHORT?.pct ?? 0],
    [C.green, C.red],
    true,
  );

  const byConf = s.metrics.m3_tradeable.by_confidence || {};
  state.charts.confidence = barChart(
    "confidenceChart",
    Object.keys(byConf),
    Object.values(byConf).map((r) => r.pct ?? 0),
    C.blue,
  );

  state.charts.metric = barChart(
    "metricChart",
    ["m1 gap", "m2 day direction", "m3 tradeable"],
    [
      s.metrics.m1_gap.overall.pct ?? 0,
      s.metrics.m2_day_direction.overall.pct ?? 0,
      s.metrics.m3_tradeable.overall.pct ?? 0,
    ],
    [C.blue, C.amber, C.green],
  );
}

/* -------------------------------------------------------------- history */
function renderHistory() {
  setNav("nav-history");
  destroyCharts();
  const rows = (state.index || []).map((d) => {
    const scoreBadge = d.m3_total
      ? `<span class="badge ${d.m3_success / d.m3_total >= 0.5 ? "win" : "loss"}">
           m3 ${d.m3_success}/${d.m3_total}</span>`
      : '<span class="badge no_entry">unscored</span>';
    const pnlChip = d.day_pnl_inr !== undefined
      ? `<span class="pnl-chip ${d.day_pnl_inr >= 0 ? "pos" : "neg"}">${inr(d.day_pnl_inr, true)}</span>`
      : "";
    return `
      <a class="history-row" href="#/day/${esc(d.date)}">
        <span class="date">${esc(d.date)}</span>
        <span class="badge ${esc(d.bias)}">${esc(d.bias)}</span>
        <span class="muted">${d.picks} pick${d.picks === 1 ? "" : "s"}</span>
        <span class="spacer"></span>
        ${scoreBadge}
        ${pnlChip}
        <span class="muted">${esc(d.status)}</span>
      </a>`;
  });
  app.innerHTML = `${demoBanner()}<h2>History</h2>${rows.join("") ||
    '<div class="loading">No sessions recorded yet.</div>'}`;
}

/* ------------------------------------------------------------- day view */
function grossMove(trade, direction) {
  if (trade.gross_pct !== undefined && trade.gross_pct !== null) return trade.gross_pct;
  const e = trade.entry_price, x = trade.exit_price;
  if (!e || !x) return null;
  const raw = direction === "LONG" ? (x - e) / e * 100 : (e - x) / e * 100;
  return Math.round(raw * 100) / 100;
}

function tradeHTML(p) {
  const t = (p.verification || {}).trade;
  if (!t) return "";
  if (t.outcome === "NO_ENTRY") {
    const reasons = {
      no_breakout: "price never broke the opening range in the entry window",
      gap_too_large: "open gap exceeded ±4% — too risky to chase, trade skipped",
      no_data: "intraday data unavailable",
    };
    return `
      <div class="trade-box">
        <div class="trade-head">
          <span class="title">Simulated trade</span>
          <span class="badge no_entry">NO ENTRY</span>
          <span class="pnl" style="color:var(--muted)">₹0</span>
        </div>
        <div class="trade-grid"><div class="kv"><div class="k">Why</div>
          <div class="v">${esc(reasons[t.reason] || t.reason || "n/a")}</div></div></div>
      </div>`;
  }

  const gross = grossMove(t, p.direction);
  const costs = t.costs_pct ?? 0.1;
  const win = t.outcome === "WIN";
  const notes = [];
  if (t.slippage_pct) {
    notes.push(`price gapped through the level — filled at candle open,
      slippage ${signedPct(t.slippage_pct, 3)}`);
  }
  if (t.exit_quality && t.exit_quality !== "normal") {
    notes.push(`exit quality: ${esc(t.exit_quality)} (circuit-limit candles near the exit)`);
  }

  return `
    <div class="trade-box ${win ? "win" : "loss"}">
      <div class="trade-head">
        <span class="title">Simulated trade</span>
        <span class="badge ${win ? "win" : "loss"}">${t.outcome}</span>
        <span class="pnl ${win ? "pos" : "neg"}">${signedPct(t.pnl_pct)} · ${inr(t.pnl_inr, true)}</span>
      </div>
      <div class="trade-grid">
        <div class="kv"><div class="k">Entry</div>
          <div class="v">${t.entry_price} @ ${hhmm(t.entry_time)}</div></div>
        <div class="kv"><div class="k">Exit</div>
          <div class="v">${t.exit_price} @ ${hhmm(t.exit_time)} (${esc(t.exit_reason)})</div></div>
        <div class="kv"><div class="k">Stop / target</div>
          <div class="v">${t.stop} / ${t.target}</div></div>
        <div class="kv"><div class="k">Price move</div>
          <div class="v ${gross >= 0 ? "pos" : "neg"}">${signedPct(gross)}</div></div>
        <div class="kv"><div class="k">Costs</div>
          <div class="v">−${costs.toFixed(2)}%</div></div>
        <div class="kv"><div class="k">Net on ₹10,000</div>
          <div class="v ${win ? "pos" : "neg"}">${signedPct(t.pnl_pct)} = ${inr(t.pnl_inr, true)}</div></div>
      </div>
      ${notes.length ? `<div class="trade-note">⚠ ${notes.join(" · ")}</div>` : ""}
    </div>`;
}

function renderPrediction(p) {
  const v = p.verification || {};
  const g = p.gap_check || {};
  const gap = v.gap_pct ?? g.gap_pct;
  const gapKnown = gap !== undefined && gap !== null;
  const gapAgrees = g.gap_agrees;

  return `
    <div class="pred-card ${p.direction === "LONG" ? "long" : "short"}">
      <div class="pred-top">
        <span class="sym">${esc(p.symbol)}</span>
        ${p.sector ? `<span class="sector-chip">${esc(p.sector)}</span>` : ""}
        <span class="badge ${p.direction === "LONG" ? "long" : "short"}">
          ${p.direction === "LONG" ? "long bias" : "short bias"}</span>
        <span class="catalyst">${esc(p.catalyst_type)}</span>
        ${p.priced_in ? '<span class="catalyst">⚠ priced-in risk</span>' : ""}
        <div class="conf-wrap">
          <div class="conf-meter"><i style="width:${Math.max(0, Math.min(100, p.confidence))}%"></i></div>
          <span class="num">${p.confidence}%</span>
        </div>
      </div>
      <div class="rationale">${esc(p.rationale)}</div>
      <div class="source">“${esc(p.source_headline)}”</div>
      <div class="metrics-row">
        <span class="chip ${!gapKnown ? "" : gapAgrees === false ? "fail" : "pass"}">
          open gap <strong>${gapKnown ? signedPct(gap) : "—"}</strong></span>
        <span class="${chipCls(v.m1_gap)}">m1 gap <strong>${mark(v.m1_gap)}</strong></span>
        <span class="${chipCls(v.m2_day_direction)}">m2 day <strong>${mark(v.m2_day_direction)}</strong></span>
        <span class="${chipCls(v.m3_tradeable)}">m3 tradeable <strong>${mark(v.m3_tradeable)}</strong></span>
        ${v.mfe_pct !== undefined && v.mfe_pct !== null
          ? `<span class="chip">best ${signedPct(v.mfe_pct)} · worst ${signedPct(v.mae_pct)}</span>` : ""}
      </div>
      ${tradeHTML(p)}
    </div>`;
}

async function renderDay(date) {
  setNav("");
  destroyCharts();
  app.innerHTML = '<div class="loading">Loading day…</div>';
  let day;
  try {
    day = await fetchJSON(`data/predictions/${encodeURIComponent(date)}.json`);
  } catch (err) {
    app.innerHTML = `<div class="error">Could not load ${esc(date)}: ${esc(err.message)}</div>`;
    return;
  }
  const ctx = day.market_context || {};
  const cues = ctx.cues || {};
  const cueChips = Object.values(cues)
    .filter((c) => c && c.name)
    .map((c) => `<span class="cue">${esc(c.name)}
      <b class="${c.change_pct >= 0 ? "pos" : "neg"}">${signedPct(c.change_pct)}</b></span>`)
    .join("");
  const reasons = (ctx.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");

  app.innerHTML = `
    ${demoBanner()}
    <a class="back-link" href="#/history">← back to history</a>
    <h2>${esc(day.date)} — morning bias
      <span class="badge ${esc(ctx.bias || "neutral")}">${esc(ctx.bias || "neutral")}</span>
      <span class="badge no_entry">${esc(day.status || "")}</span></h2>
    ${cueChips ? `<div class="cue-chips">${cueChips}</div>` : ""}
    ${reasons ? `<div class="panel" style="margin-top:12px"><ul class="reasons">${reasons}</ul></div>` : ""}
    <h2>Predictions (${(day.predictions || []).length})</h2>
    ${(day.predictions || []).map(renderPrediction).join("") ||
      '<div class="loading">No picks passed the filters this day.</div>'}
    ${day.note ? `<div class="panel" style="color:var(--muted)">${esc(day.note)}</div>` : ""}`;
}

/* --------------------------------------------------------------- router */
function router() {
  const hash = location.hash || "#/";
  if (hash.startsWith("#/day/")) renderDay(decodeURIComponent(hash.slice(6)));
  else if (hash === "#/history") renderHistory();
  else renderHome();
}

async function init() {
  try {
    [state.stats, state.index] = await Promise.all([
      fetchJSON("data/stats.json"),
      fetchJSON("data/index.json"),
    ]);
  } catch (err) {
    const hint = location.protocol === "file:"
      ? "You opened this file directly from disk — browsers block data loading on file://.<br>" +
        "Serve it over HTTP instead: <code>python -m http.server 8000 --directory docs</code> " +
        "then open <code>http://localhost:8000</code> (GitHub Pages does this for you)."
      : "Run the <code>verify</code> workflow once to publish data.";
    app.innerHTML = `<div class="error">Failed to load dashboard data: ${esc(err.message)}<br>${hint}</div>`;
    return;
  }
  window.addEventListener("hashchange", router);
  router();
}

init();
