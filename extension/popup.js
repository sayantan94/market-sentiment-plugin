const API_URL = "http://localhost:5050";

const healthDot = document.getElementById("health-dot");
const healthText = document.getElementById("health-text");
const countEl = document.getElementById("count");
const clearBtn = document.getElementById("clear-btn");
const scanTickerInput = document.getElementById("scan-ticker");
const scanBtn = document.getElementById("scan-btn");
const scanProgress = document.getElementById("scan-progress");
const queueSection = document.getElementById("queue-section");
const queueItems = document.getElementById("queue-items");
const queueDivider = document.getElementById("queue-divider");

let scanActive = false;
let scanPollInterval = null;

async function refresh() {
  try {
    const r = await fetch(`${API_URL}/health`);
    if (r.ok) {
      healthDot.className = "status-dot ok";
      healthText.textContent = "Connected";
    } else {
      throw new Error();
    }
  } catch {
    healthDot.className = "status-dot err";
    healthText.textContent = "Offline";
  }

  try {
    const r = await fetch(`${API_URL}/posts`);
    const data = await r.json();
    countEl.textContent = data.count;
  } catch {
    countEl.textContent = "—";
  }
}

function setScanUI(active, tickerDisplay, count, maxPosts, currentTicker) {
  scanActive = active;
  if (active) {
    scanTickerInput.value = tickerDisplay || "";
    scanTickerInput.disabled = true;
    scanBtn.textContent = "■ Stop Scan";
    scanBtn.classList.add("stop");
    scanBtn.disabled = false;
    const cur = currentTicker ? ` ($${currentTicker})` : "";
    scanProgress.textContent = `Scanning${cur}… ${count || 0}/${maxPosts || 50} posts`;
  } else {
    scanTickerInput.disabled = false;
    scanBtn.textContent = "▶ Start Scan";
    scanBtn.classList.remove("stop");
    scanBtn.disabled = false;
    scanProgress.textContent = "";
  }
}

async function renderQueue() {
  try {
    const resp = await fetch(`${API_URL}/scan`);
    if (!resp.ok) return;
    const scan = await resp.json();

    if (!scan || scan.status === "none" || !scan.tickers || scan.tickers.length === 0) {
      queueSection.style.display = "none";
      queueDivider.style.display = "none";
      return;
    }

    queueSection.style.display = "";
    queueDivider.style.display = "";

    // Get active scan state from chrome storage for per-ticker progress
    const storage = await chrome.storage.local.get("mspScan");
    const msp = storage.mspScan || {};
    const currentTicker = msp.currentTicker || scan.currentTicker || "";
    const tickerIndex = msp.tickerIndex || 0;
    const phase = msp.phase || scan.phase || "";
    const count = msp.totalCount || scan.count || 0;

    let html = "";
    const tickers = scan.tickers || [];
    for (let i = 0; i < tickers.length; i++) {
      const t = tickers[i];
      let statusText = "";
      let tickerClass = "";
      let statusClass = "";

      if (scan.status === "done") {
        statusText = "done";
        tickerClass = "done";
      } else if (t === currentTicker) {
        const phaseLabel = phase === "latest" ? "Latest" : "Top";
        statusText = `scanning ${phaseLabel}… ${count}`;
        tickerClass = "active";
        statusClass = "active";
      } else if (msp.active && i < tickerIndex) {
        statusText = "done";
        tickerClass = "done";
      } else {
        statusText = "queued";
      }

      html += `<div class="queue-item">
        <span class="queue-ticker ${tickerClass}">$${t}</span>
        <span class="queue-status ${statusClass}">${statusText}</span>
      </div>`;
    }
    queueItems.innerHTML = html;
  } catch {
    // backend offline
    queueSection.style.display = "none";
    queueDivider.style.display = "none";
  }
}

function startScanPolling() {
  if (scanPollInterval) return;
  scanPollInterval = setInterval(() => {
    chrome.storage.local.get("mspScan", (result) => {
      if (result.mspScan && result.mspScan.active) {
        const s = result.mspScan;
        setScanUI(true, s.tickers.join(", "), s.totalCount || s.count, s.maxPosts, s.currentTicker);
      } else {
        setScanUI(false);
        stopScanPolling();
      }
    });
    renderQueue();
  }, 1000);
}

function stopScanPolling() {
  if (scanPollInterval) {
    clearInterval(scanPollInterval);
    scanPollInterval = null;
  }
}

scanBtn.addEventListener("click", () => {
  if (scanActive) {
    chrome.runtime.sendMessage({ type: "stop_scan" });
    setScanUI(false);
    stopScanPolling();
  } else {
    const raw = scanTickerInput.value.trim().toUpperCase();
    const tickers = raw.split(/[,\s]+/).filter((t) => t.length > 0 && t.length <= 5);
    if (tickers.length === 0) {
      scanTickerInput.focus();
      return;
    }
    chrome.runtime.sendMessage({ type: "start_scan", tickers, maxPosts: 50 });
    setScanUI(true, tickers.join(", "), 0, 50);
    startScanPolling();
  }
});

// Force uppercase input, allow commas and spaces as separators
scanTickerInput.addEventListener("input", () => {
  scanTickerInput.value = scanTickerInput.value.toUpperCase().replace(/[^A-Z,\s]/g, "");
});

clearBtn.addEventListener("click", async () => {
  try {
    await fetch(`${API_URL}/posts`, { method: "DELETE" });
    countEl.textContent = "0";
  } catch {
    // ignore
  }
});

// Init: check for active scan + render queue
chrome.storage.local.get("mspScan", (result) => {
  if (result.mspScan && result.mspScan.active) {
    const s = result.mspScan;
    setScanUI(true, s.tickers.join(", "), s.totalCount || s.count, s.maxPosts, s.currentTicker);
    startScanPolling();
  }
});

refresh();
renderQueue();
