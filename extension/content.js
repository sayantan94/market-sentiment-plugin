/**
 * Market Sentiment Plugin — Content script for x.com
 * Injects a "$" save button into each post's action bar.
 * On click: extracts text + screenshot, POSTs to local backend.
 */

const API_URL = "http://localhost:5050";
const TICKER_RE = /\$([A-Z]{1,5})\b/g;

function extractPostData(article) {
  const textEl = article.querySelector('div[data-testid="tweetText"]');
  const text = textEl ? textEl.innerText.trim() : "";

  // Handle from status link: /username/status/...
  let handle = "";
  const statusLink = article.querySelector('a[href*="/status/"]');
  if (statusLink) {
    const parts = statusLink.getAttribute("href").split("/");
    handle = parts[1] || "";
  }

  // Timestamp
  const timeEl = article.querySelector("time[datetime]");
  const timestamp = timeEl ? timeEl.getAttribute("datetime") : "";

  // URL
  let url = "";
  if (statusLink) {
    url = "https://x.com" + statusLink.getAttribute("href");
  }

  // Tickers — reset lastIndex since TICKER_RE is global
  TICKER_RE.lastIndex = 0;
  const tickers = [];
  let match;
  while ((match = TICKER_RE.exec(text)) !== null) {
    const t = match[1];
    if (t && !tickers.includes(t)) tickers.push(t);
  }

  return { text, handle, timestamp, tickers, url };
}

/**
 * Capture a screenshot of the visible tab, then crop to the article element.
 * Returns a base64 PNG string (no data URL prefix).
 */
async function capturePostScreenshot(article) {
  try {
    // Hide scan banner during capture so it doesn't appear in screenshot
    const banner = document.getElementById("msp-scan-banner");
    if (banner) banner.style.display = "none";

    const dpr = window.devicePixelRatio || 1;

    // Try to find the inner tweet content for a tighter crop
    // The cellInnerDiv wraps just the tweet content within the article
    const tweetContent = article.querySelector('[data-testid="tweetText"]')?.closest('[data-testid="tweet"]')
      || article;
    const rect = tweetContent.getBoundingClientRect();

    // Ask background to capture the visible tab
    const response = await chrome.runtime.sendMessage({ type: "capture_tab" });

    // Restore banner
    if (banner) banner.style.display = "";

    if (!response || response.error) {
      console.warn("[MSP] Tab capture failed:", response?.error);
      return null;
    }

    // Load full-tab screenshot into an image
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = reject;
      i.src = response.dataUrl;
    });

    // Crop to the tweet element with some padding
    const pad = 4 * dpr;
    const sx = Math.max(0, rect.left * dpr - pad);
    const sy = Math.max(0, rect.top * dpr - pad);
    const sw = Math.min(rect.width * dpr + pad * 2, img.width - sx);
    const sh = rect.height * dpr + pad * 2;

    // Cap height to avoid huge screenshots
    const maxH = 900 * dpr;
    const cropH = Math.min(sh, maxH, img.height - sy);

    if (sw <= 0 || cropH <= 0) return null;

    const canvas = document.createElement("canvas");
    canvas.width = sw;
    canvas.height = cropH;

    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, sx, sy, sw, cropH, 0, 0, sw, cropH);

    // Convert to base64 PNG (strip the "data:image/png;base64," prefix)
    const dataUrl = canvas.toDataURL("image/png", 0.85);
    return dataUrl.split(",")[1];
  } catch (err) {
    console.warn("[MSP] Screenshot failed:", err);
    return null;
  }
}

async function savePost(article, btn) {
  const data = extractPostData(article);
  if (!data.text) return;

  btn.classList.add("msp-saving");
  btn.textContent = "...";

  try {
    // Capture screenshot in parallel with data extraction
    const screenshot = await capturePostScreenshot(article);
    if (screenshot) {
      data.screenshot = screenshot;
    }

    const resp = await fetch(`${API_URL}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });

    if (resp.ok) {
      const result = await resp.json();
      btn.classList.remove("msp-saving");
      btn.classList.add("msp-saved");
      btn.textContent = result.duplicate ? "=" : "$";
      if (!result.duplicate) {
        chrome.runtime.sendMessage({ type: "post_saved" });
      }
    } else {
      throw new Error(`HTTP ${resp.status}`);
    }
  } catch (err) {
    btn.classList.remove("msp-saving");
    btn.classList.add("msp-error");
    btn.textContent = "!";
    console.error("[MSP] Save failed:", err);
    setTimeout(() => {
      btn.classList.remove("msp-error");
      btn.textContent = "$";
    }, 2000);
  }
}

function injectButton(article) {
  if (article.hasAttribute("data-msp-injected")) return;
  article.setAttribute("data-msp-injected", "true");

  const actionBar = article.querySelector('div[role="group"]');
  if (!actionBar) return;

  const btn = document.createElement("button");
  btn.className = "msp-save-btn";
  btn.textContent = "$";
  btn.title = "Save for sentiment analysis";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    e.preventDefault();
    savePost(article, btn);
  });

  actionBar.appendChild(btn);
}

function processArticles() {
  const articles = document.querySelectorAll('article[data-testid="tweet"]');
  articles.forEach(injectButton);
}

// Initial pass
processArticles();

// Watch for new posts (infinite scroll)
const observer = new MutationObserver(() => processArticles());
observer.observe(document.body, { childList: true, subtree: true });

// --- Auto-Scan Mode ---

let scanning = false;
let scanLoopRunning = false;
let scanCount = 0;
let scanTicker = "";
let scanMaxPosts = 50;
let scanBanner = null;

function createScanBanner(ticker) {
  const banner = document.createElement("div");
  banner.id = "msp-scan-banner";
  banner.innerHTML = `
    <span id="msp-scan-text">Scanning $${ticker} — 0 posts saved</span>
    <button id="msp-scan-stop">■ Stop</button>
  `;
  document.body.appendChild(banner);
  document.getElementById("msp-scan-stop").addEventListener("click", () => stopScan());
  return banner;
}

function updateScanBanner() {
  const textEl = document.getElementById("msp-scan-text");
  if (textEl) {
    const phase = window.location.search.includes("f=live") ? "Latest" : "Top";
    textEl.textContent = `Scanning $${scanTicker} [${phase}] — ${scanCount} posts saved`;
  }
}

function removeScanBanner() {
  const banner = document.getElementById("msp-scan-banner");
  if (banner) banner.remove();
  scanBanner = null;
}

async function scanLoop() {
  if (scanLoopRunning) return;
  scanLoopRunning = true;

  // Wait for initial page content
  await new Promise((r) => setTimeout(r, 1500));

  while (scanning && scanCount < scanMaxPosts) {
    // Grab unscanned articles currently in the DOM
    const articles = document.querySelectorAll('article[data-testid="tweet"]:not([data-msp-scanned])');

    if (articles.length === 0) {
      // No new articles — scroll down and wait for more to load
      window.scrollBy(0, 800);
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }

    // Process each article one at a time, in DOM order (top to bottom)
    for (const article of articles) {
      if (!scanning || scanCount >= scanMaxPosts) break;

      article.setAttribute("data-msp-scanned", "true");
      const data = extractPostData(article);
      if (!data.text) continue;

      if (!data.tickers.includes(scanTicker)) {
        data.tickers.push(scanTicker);
      }
      // Safety: filter out any null/undefined from tickers
      data.tickers = data.tickers.filter((t) => typeof t === "string" && t.length > 0);

      // Scroll into view, wait for render, then screenshot
      article.scrollIntoView({ block: "center", behavior: "instant" });
      await new Promise((r) => setTimeout(r, 800));
      const screenshot = await capturePostScreenshot(article);
      if (screenshot) {
        data.screenshot = screenshot;
      }

      try {
        const body = JSON.stringify(data);
        const resp = await fetch(`${API_URL}/save`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        if (resp.ok) {
          const result = await resp.json();
          if (result.duplicate) {
            // Already seen — skip silently
            continue;
          }
          scanCount++;
          const btn = article.querySelector(".msp-save-btn");
          if (btn) btn.classList.add("msp-saved");
          updateScanBanner();
          chrome.runtime.sendMessage({ type: "scan_progress", count: scanCount });
          patchScan({ status: "running", count: scanCount, currentTicker: scanTicker, phase: window.location.search.includes("f=live") ? "latest" : "top" });
        } else {
          const errBody = await resp.text();
          console.warn(`[MSP] Scan save HTTP ${resp.status}:`, errBody, "\nPayload keys:", Object.keys(data), "text length:", data.text.length, "handle:", data.handle);
        }
      } catch (err) {
        console.warn("[MSP] Scan save failed:", err);
      }
    }

    // Scroll down to load more articles
    window.scrollBy(0, 800);
    await new Promise((r) => setTimeout(r, 2000));
  }

  scanLoopRunning = false;
  if (scanning) {
    // Phase limit hit — tell background to move to next phase (top → latest)
    scanning = false;
    removeScanBanner();
    patchScan({ status: "running", count: scanCount, currentTicker: scanTicker, phase: "switching" });
    chrome.runtime.sendMessage({ type: "scan_phase_done", count: scanCount });
  }
}

function startScan(ticker, maxPosts, resumeCount) {
  if (scanning) return;

  // Fallback: extract ticker from X search URL if not provided
  if (!ticker) {
    const urlMatch = decodeURIComponent(window.location.search).match(/q=\$([A-Z]{1,5})\b/i);
    if (urlMatch) ticker = urlMatch[1].toUpperCase();
  }
  if (!ticker) return; // still nothing, bail out

  scanning = true;
  scanCount = resumeCount || 0;
  scanTicker = ticker;
  scanMaxPosts = (resumeCount || 0) + (maxPosts || 50);

  scanBanner = createScanBanner(ticker);
  updateScanBanner();
  scanLoop();
}

function patchScan(body) {
  fetch(`${API_URL}/scan`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {});
}

function stopScan() {
  if (!scanning) return;
  scanning = false;
  scanLoopRunning = false;
  removeScanBanner();
  patchScan({ status: "done", count: scanCount });
  chrome.runtime.sendMessage({ type: "scan_done", count: scanCount });
  chrome.storage.local.remove("mspScan");
}

// Listen for scan commands from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "start_scan") {
    // Get current count from storage for phase resumption
    chrome.storage.local.get("mspScan", (result) => {
      const resumeCount = result.mspScan ? result.mspScan.count || 0 : 0;
      startScan(msg.ticker, msg.maxPosts, resumeCount);
    });
  }
  if (msg.type === "stop_scan") {
    stopScan();
  }
});

// On load: resume scan if one was active (e.g. page re-inject after navigation)
chrome.storage.local.get("mspScan", (result) => {
  if (result.mspScan && result.mspScan.active) {
    startScan(result.mspScan.currentTicker, result.mspScan.perTab);
  }
});

// --- Poll for CLI-queued scans ---
(function pollForScans() {
  const POLL_INTERVAL = 3000;

  async function checkScan() {
    if (scanning) return; // already scanning, skip
    try {
      // Don't re-trigger if background already has an active scan
      const storage = await chrome.storage.local.get("mspScan");
      if (storage.mspScan && storage.mspScan.active) return;

      const resp = await fetch(`${API_URL}/scan`);
      if (!resp.ok) return;
      const scan = await resp.json();
      if (scan.status === "pending") {
        // Await the PATCH so it completes before navigation kills this script
        await fetch(`${API_URL}/scan`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "running" }),
        });
        const tickers = scan.tickers || [];
        const maxPosts = scan.maxPosts || 50;
        chrome.runtime.sendMessage({ type: "start_scan", tickers, maxPosts });
      }
    } catch {
      // backend offline, ignore
    }
  }

  // Check immediately on page load, then every 3s
  checkScan();
  setInterval(checkScan, POLL_INTERVAL);
})();
