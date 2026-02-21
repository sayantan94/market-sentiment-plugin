const API_URL = "http://localhost:5050";

const healthDot = document.getElementById("health-dot");
const healthText = document.getElementById("health-text");
const countEl = document.getElementById("count");
const clearBtn = document.getElementById("clear-btn");
const scanTickerInput = document.getElementById("scan-ticker");
const scanBtn = document.getElementById("scan-btn");
const scanProgress = document.getElementById("scan-progress");

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

// Init: check for active scan
chrome.storage.local.get("mspScan", (result) => {
  if (result.mspScan && result.mspScan.active) {
    const s = result.mspScan;
    setScanUI(true, s.tickers.join(", "), s.totalCount || s.count, s.maxPosts, s.currentTicker);
    startScanPolling();
  }
});

refresh();
