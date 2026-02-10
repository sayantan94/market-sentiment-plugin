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

  // Tickers
  const tickers = [];
  let match;
  while ((match = TICKER_RE.exec(text)) !== null) {
    const t = match[1];
    if (!tickers.includes(t)) tickers.push(t);
  }

  return { text, handle, timestamp, tickers, url };
}

/**
 * Capture a screenshot of the visible tab, then crop to the article element.
 * Returns a base64 PNG string (no data URL prefix).
 */
async function capturePostScreenshot(article) {
  try {
    const rect = article.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    // Ask background to capture the visible tab
    const response = await chrome.runtime.sendMessage({ type: "capture_tab" });
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

    // Crop to the article element
    const canvas = document.createElement("canvas");
    const sx = rect.left * dpr;
    const sy = rect.top * dpr;
    const sw = rect.width * dpr;
    const sh = rect.height * dpr;

    // Cap height to avoid huge screenshots for very long threads
    const maxH = 1200 * dpr;
    const cropH = Math.min(sh, maxH);

    canvas.width = sw;
    canvas.height = cropH;

    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, sx, sy, sw, cropH, 0, 0, sw, cropH);

    // Convert to base64 PNG (strip the "data:image/png;base64," prefix)
    const dataUrl = canvas.toDataURL("image/png", 0.9);
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
      btn.classList.remove("msp-saving");
      btn.classList.add("msp-saved");
      btn.textContent = "$";
      chrome.runtime.sendMessage({ type: "post_saved" });
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
