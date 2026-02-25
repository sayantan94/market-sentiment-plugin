/**
 * Market Sentiment Plugin — Side Panel UI
 * Handles Dashboard, Feed, and Controls tabs.
 * Polls local backend at localhost:5050 for posts, analysis, and scan state.
 */

/* ------------------------------------------------------------------ */
/*  1. Constants & DOM refs                                           */
/* ------------------------------------------------------------------ */

const API = "http://localhost:5050";

const healthIndicator = document.getElementById("health-indicator");
const healthDot = document.getElementById("health-dot");
const healthText = document.getElementById("health-text");

const statPosts = document.getElementById("stat-posts");
const statScan = document.getElementById("stat-scan");
const statTickers = document.getElementById("stat-tickers");

const analysisCards = document.getElementById("analysis-cards");

const feedSearch = document.getElementById("feed-search");
const feedFooter = document.getElementById("feed-footer");

const scanInput = document.getElementById("scan-input");
const scanMax = document.getElementById("scan-max");
const scanBtn = document.getElementById("scan-btn");
const queueCard = document.getElementById("queue-card");

const quickScanBtn = document.getElementById("quick-scan-btn");
const clearBtn = document.getElementById("clear-btn");

let scanActive = false;

/* ------------------------------------------------------------------ */
/*  2. Tab switching                                                  */
/* ------------------------------------------------------------------ */

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    const target = document.getElementById("tab-" + btn.dataset.tab);
    if (target) target.classList.add("active");
  });
});

/* ------------------------------------------------------------------ */
/*  3. Health check                                                   */
/* ------------------------------------------------------------------ */

async function checkHealth() {
  try {
    const resp = await fetch(API + "/health");
    if (resp.ok) {
      healthDot.className = "health-dot ok";
      healthText.textContent = "Connected";
      healthIndicator.className = "health-indicator ok";
    } else {
      throw new Error("not ok");
    }
  } catch {
    healthDot.className = "health-dot err";
    healthText.textContent = "Offline";
    healthIndicator.className = "health-indicator err";
  }
}

/* ------------------------------------------------------------------ */
/*  4. Dashboard — status + analysis                                  */
/* ------------------------------------------------------------------ */

async function refreshDashboard() {
  // Post count
  try {
    const resp = await fetch(API + "/posts");
    const data = await resp.json();
    statPosts.textContent = data.count;
  } catch {
    statPosts.textContent = "\u2014";
  }

  // Scan status from chrome.storage
  try {
    const result = await new Promise((resolve) =>
      chrome.storage.local.get("mspScan", resolve)
    );
    if (result.mspScan && result.mspScan.active) {
      const s = result.mspScan;
      const count = s.totalCount || 0;
      const max = s.maxPosts || 50;
      statScan.textContent = count + " / " + max + " posts";
      statScan.style.color = "#00ba7c";
    } else {
      statScan.textContent = "Idle";
      statScan.style.color = "";
    }
  } catch {
    statScan.textContent = "Idle";
    statScan.style.color = "";
  }

  // Ticker count from analysis
  try {
    const resp = await fetch(API + "/analysis");
    const data = await resp.json();
    const entries = Object.entries(data.analysis || {});
    statTickers.textContent = entries.length;
  } catch {
    statTickers.textContent = "\u2014";
  }
}

function renderAnalysisCards(entries) {
  if (!entries || entries.length === 0) {
    analysisCards.innerHTML =
      '<div class="empty-state">' +
        '<div class="empty-state-icon">$</div>' +
        '<div class="empty-state-text">No analysis yet.<br>Run <code>msp-cli analyze</code> to see results.</div>' +
      "</div>";
    return;
  }

  analysisCards.innerHTML = entries
    .map(([ticker, a], idx) => {
      const sentiment = a.sentiment || "neutral";
      const confidence = a.confidence != null ? a.confidence : "\u2014";
      const summary = escapeHtml(a.summary || "");

      let bodyHtml = '<div class="analysis-summary">' + summary + "</div>";

      // Themes
      if (a.themes && a.themes.length > 0) {
        bodyHtml +=
          '<div class="analysis-meta-label">Themes</div>' +
          '<div class="analysis-themes">' +
          a.themes
            .map((t) => '<span class="analysis-theme-pill">' + escapeHtml(t) + "</span>")
            .join("") +
          "</div>";
      }

      // Catalysts
      if (a.catalysts) {
        const catalystsText = Array.isArray(a.catalysts)
          ? a.catalysts.join(", ")
          : String(a.catalysts);
        if (catalystsText) {
          bodyHtml +=
            '<div class="analysis-meta-label">Catalysts</div>' +
            '<div class="analysis-detail">' + escapeHtml(catalystsText) + "</div>";
        }
      }

      // Key Levels
      if (a.key_levels) {
        bodyHtml +=
          '<div class="analysis-meta-label">Key Levels</div>' +
          '<div class="analysis-detail">' + escapeHtml(String(a.key_levels)) + "</div>";
      }

      // Notable Accounts
      if (a.notable_accounts) {
        const accountsText = Array.isArray(a.notable_accounts)
          ? a.notable_accounts.join(", ")
          : String(a.notable_accounts);
        if (accountsText) {
          bodyHtml +=
            '<div class="analysis-meta-label">Notable Accounts</div>' +
            '<div class="analysis-accounts">' + escapeHtml(accountsText) + "</div>";
        }
      }

      // Signal / Noise stats
      const signal = a.signal_posts != null ? a.signal_posts : 0;
      const noise = a.noise_filtered != null ? a.noise_filtered : 0;
      bodyHtml +=
        '<div class="analysis-stats">' +
          '<span class="analysis-stat"><strong>' + signal + "</strong> signal</span>" +
          '<span class="analysis-stat"><strong>' + noise + "</strong> noise</span>" +
        "</div>";

      // Timestamp
      if (a.analyzed_at) {
        bodyHtml +=
          '<div class="analysis-timestamp">' +
            '<span>Analyzed ' + formatTimestamp(a.analyzed_at) + '</span>' +
          "</div>";
      }

      const expandedClass = idx === 0 ? " expanded" : "";

      return (
        '<div class="analysis-card' + expandedClass + '">' +
          '<div class="analysis-header" onclick="this.parentElement.classList.toggle(\'expanded\')">' +
            '<span class="analysis-ticker">$' + escapeHtml(ticker) + "</span>" +
            '<span class="analysis-sentiment ' + sentiment + '">' + sentiment + "</span>" +
            '<span class="analysis-confidence">' + confidence + "%</span>" +
            '<svg class="analysis-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>' +
          "</div>" +
          '<div class="analysis-body">' + bodyHtml + "</div>" +
        "</div>"
      );
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/*  5. Feed — analysis results                                        */
/* ------------------------------------------------------------------ */

let allAnalysisEntries = [];

async function refreshFeed() {
  try {
    const resp = await fetch(API + "/analysis");
    const data = await resp.json();
    allAnalysisEntries = Object.entries(data.analysis || {});
  } catch {
    allAnalysisEntries = [];
  }
  filterAndRenderFeed();
}

function filterAndRenderFeed() {
  const query = (feedSearch.value || "").toUpperCase().trim();
  const filtered = query
    ? allAnalysisEntries.filter(([ticker]) => ticker.toUpperCase().includes(query))
    : allAnalysisEntries;

  if (filtered.length === 0) {
    analysisCards.innerHTML =
      '<div class="empty-state">' +
        '<div class="empty-state-icon">$</div>' +
        '<div class="empty-state-text">' +
        (allAnalysisEntries.length === 0
          ? "No analysis yet.<br>Run <code>msp-cli analyze</code> to see results."
          : "No tickers match filter.") +
        "</div>" +
      "</div>";
    feedFooter.textContent = "";
    return;
  }

  renderAnalysisCards(filtered);
  feedFooter.textContent = "Showing " + filtered.length + " of " + allAnalysisEntries.length + " tickers";
}

feedSearch.addEventListener("input", filterAndRenderFeed);

function escapeHtml(s) {
  if (!s) return "";
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function formatTimestamp(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}

/* ------------------------------------------------------------------ */
/*  6. Controls — scan management                                     */
/* ------------------------------------------------------------------ */

scanInput.addEventListener("input", () => {
  scanInput.value = scanInput.value.toUpperCase().replace(/[^A-Z,\s]/g, "");
});

scanBtn.addEventListener("click", () => {
  if (scanActive) {
    chrome.runtime.sendMessage({ type: "stop_scan" });
    setScanUI(false);
  } else {
    const raw = scanInput.value.trim();
    const tickers = raw
      .split(/[,\s]+/)
      .filter((t) => t.length > 0 && t.length <= 5);
    if (tickers.length === 0) {
      scanInput.focus();
      return;
    }
    const maxPosts = Math.max(5, Math.min(200, parseInt(scanMax.value, 10) || 50));
    chrome.runtime.sendMessage({ type: "start_scan", tickers, maxPosts });
    setScanUI(true);
  }
});

function setScanUI(active) {
  scanActive = active;
  scanInput.disabled = active;
  scanMax.disabled = active;
  if (active) {
    scanBtn.textContent = "\u25A0 Stop Scan";
    scanBtn.classList.add("stop");
  } else {
    scanBtn.textContent = "\u25B6 Start Scan";
    scanBtn.classList.remove("stop");
  }
}

async function refreshQueue() {
  try {
    const result = await new Promise((resolve) =>
      chrome.storage.local.get("mspScan", resolve)
    );
    const s = result.mspScan;

    if (!s || !s.active) {
      queueCard.innerHTML =
        "";
      setScanUI(false);
      return;
    }

    // Keep scan UI in sync
    setScanUI(true);
    if (s.tickers && s.tickers.length > 0) {
      scanInput.value = s.tickers.join(", ");
    }

    const tickers = s.tickers || [];
    const tickerIndex = s.tickerIndex || 0;
    const currentTicker = s.currentTicker || "";
    const phase = s.phase || "";
    const totalCount = s.totalCount || 0;
    const perTicker = s.perTicker || 0;

    let html = "";

    for (let i = 0; i < tickers.length; i++) {
      const t = tickers[i];
      let statusText = "";
      let tickerClass = "";
      let fillClass = "pending";
      let pct = 0;

      if (i < tickerIndex) {
        statusText = "Done";
        tickerClass = "done";
        fillClass = "done";
        pct = 100;
      } else if (t === currentTicker && s.active) {
        const perTick = s.perTicker || s.maxPosts || 50;
        statusText = totalCount + " / " + perTick + " posts";
        tickerClass = "active";
        fillClass = "active";
        pct = Math.min(100, Math.round((totalCount / Math.max(perTick, 1)) * 100));
      } else {
        statusText = "Pending";
        tickerClass = "";
        fillClass = "pending";
        pct = 0;
      }

      html +=
        '<div class="queue-item">' +
          '<span class="queue-ticker ' + tickerClass + '">$' + escapeHtml(t) + "</span>" +
          '<div class="queue-progress-wrap">' +
            '<div class="queue-progress-bar"><div class="queue-progress-fill ' + fillClass + '" style="width:' + pct + '%"></div></div>' +
            '<div class="queue-status-text ' + tickerClass + '">' + statusText + "</div>" +
          "</div>" +
        "</div>";
    }

    queueCard.innerHTML = html;
  } catch {
    queueCard.innerHTML =
      "";
  }
}

/* ------------------------------------------------------------------ */
/*  7. Quick Scan & Clear All                                         */
/* ------------------------------------------------------------------ */

quickScanBtn.addEventListener("click", () => {
  // Switch to controls tab
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
  const controlsBtn = document.querySelector('.tab-btn[data-tab="controls"]');
  if (controlsBtn) controlsBtn.classList.add("active");
  const controlsTab = document.getElementById("tab-controls");
  if (controlsTab) controlsTab.classList.add("active");
  scanInput.focus();
});

clearBtn.addEventListener("click", async () => {
  try {
    await fetch(API + "/posts", { method: "DELETE" });
  } catch {
    // Backend offline — ignore
  }
  refreshDashboard();
  refreshFeed();
});

/* ------------------------------------------------------------------ */
/*  8. Init & polling                                                 */
/* ------------------------------------------------------------------ */

async function init() {
  await checkHealth();
  refreshDashboard();
  refreshFeed();
  refreshQueue();
}

// Poll every 5 seconds
setInterval(() => {
  checkHealth();
  refreshDashboard();
  refreshFeed();
  refreshQueue();
}, 5000);

// Listen for runtime messages from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "scan_progress" || msg.type === "scan_done" || msg.type === "scan_phase_done") {
    refreshQueue();
    refreshDashboard();
  }
  if (msg.type === "post_saved") {
    refreshDashboard();
    refreshFeed();
  }
});

init();
