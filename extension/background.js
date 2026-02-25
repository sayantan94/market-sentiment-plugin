/**
 * Market Sentiment Plugin — Background service worker
 * Tracks badge count + handles tab screenshot capture + scan coordination.
 * Supports multi-ticker scanning: each ticker runs Top → Latest phases.
 */

let savedCount = 0;
let scanPollTimer = null;

// Open side panel when extension icon is clicked
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

// Poll backend for pending scans every 2 seconds
function startScanPolling() {
  if (scanPollTimer) return;
  scanPollTimer = setInterval(async () => {
    try {
      const resp = await fetch("http://localhost:5050/scan");
      const scan = await resp.json();
      
      if (scan.status === "pending") {
        // Pick up the scan
        clearInterval(scanPollTimer);
        scanPollTimer = null;
        
        // Check if scan already active
        chrome.storage.local.get("mspScan", (result) => {
          if (result.mspScan && result.mspScan.active) {
            console.log("Scan already active, ignoring pending scan");
            startScanPolling(); // Resume polling
            return;
          }
          
          // Start the scan
          const tickerList = Array.isArray(scan.tickers) ? scan.tickers : [scan.tickers];
          const maxPosts = scan.maxPosts || 50;
          const perTicker = Math.max(Math.floor(maxPosts / tickerList.length), 4);
          const perTab = Math.ceil(perTicker / 2);

          const scanState = {
            active: true,
            tickers: tickerList,
            tickerIndex: 0,
            currentTicker: tickerList[0],
            maxPosts,
            perTicker,
            perTab,
            totalCount: 0,
            count: 0,
            phase: "top",
          };
          
          chrome.storage.local.set({ mspScan: scanState }, () => {
            // Update backend status to running
            fetch("http://localhost:5050/scan", {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ status: "running" }),
            }).catch(() => {});
            startNextTicker();
          });
        });
      }
    } catch (e) {
      // Backend offline, keep polling
    }
  }, 2000);
}

// Start polling on extension load
startScanPolling();

function navigateAndStartScan(url, ticker, maxPosts) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return;
    const tabId = tabs[0].id;
    chrome.tabs.update(tabId, { url }, () => {
      const onUpdated = (updatedTabId, changeInfo) => {
        if (updatedTabId === tabId && changeInfo.status === "complete") {
          chrome.tabs.onUpdated.removeListener(onUpdated);
          chrome.tabs.sendMessage(tabId, { type: "start_scan", ticker, maxPosts });
        }
      };
      chrome.tabs.onUpdated.addListener(onUpdated);
    });
  });
}

function startNextTicker() {
  chrome.storage.local.get("mspScan", (result) => {
    const s = result.mspScan;
    if (!s || !s.active) return;

    if (s.tickerIndex >= s.tickers.length) {
      // All tickers done — notify backend
      fetch("http://localhost:5050/scan", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "done", count: s.totalCount || 0 }),
      }).catch(() => {});
      chrome.storage.local.remove("mspScan");
      savedCount += s.totalCount || 0;
      chrome.action.setBadgeText({ text: savedCount > 0 ? String(savedCount) : "" });
      chrome.action.setBadgeBackgroundColor({ color: "#00ba7c" });
      startScanPolling(); // Resume polling for next scan
      return;
    }

    const ticker = s.tickers[s.tickerIndex];
    s.currentTicker = ticker;
    s.phase = "top";
    chrome.storage.local.set({ mspScan: s }, () => {
      const url = `https://x.com/search?q=%24${encodeURIComponent(ticker)}&src=typed_query`;
      navigateAndStartScan(url, ticker, s.perTab);
    });
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "post_saved") {
    savedCount++;
    chrome.action.setBadgeText({ text: String(savedCount) });
    chrome.action.setBadgeBackgroundColor({ color: "#00ba7c" });
    return;
  }

  if (msg.type === "capture_tab") {
    chrome.tabs.captureVisibleTab(
      sender.tab.windowId,
      { format: "png" },
      (dataUrl) => {
        if (chrome.runtime.lastError) {
          sendResponse({ error: chrome.runtime.lastError.message });
        } else {
          sendResponse({ dataUrl });
        }
      }
    );
    return true;
  }

  // --- Scan mode ---

  if (msg.type === "start_scan") {
    // Guard: don't re-initialize if a scan is already running
    chrome.storage.local.get("mspScan", (result) => {
      if (result.mspScan && result.mspScan.active) {
        sendResponse({ ok: true, already_active: true });
        return;
      }

      const { tickers, maxPosts } = msg;
      const tickerList = Array.isArray(tickers) ? tickers : [tickers];
      const perTicker = Math.max(Math.floor((maxPosts || 50) / tickerList.length), 4);
      const perTab = Math.ceil(perTicker / 2);

      const scanState = {
        active: true,
        tickers: tickerList,
        tickerIndex: 0,
        currentTicker: tickerList[0],
        maxPosts: maxPosts || 50,
        perTicker,
        perTab,
        totalCount: 0,
        count: 0,        // count for current ticker phase
        phase: "top",
      };
      chrome.storage.local.set({ mspScan: scanState }, () => {
        startNextTicker();
      });
      sendResponse({ ok: true });
    });
    return true;
  }

  if (msg.type === "scan_phase_done") {
    chrome.storage.local.get("mspScan", (result) => {
      if (!result.mspScan || !result.mspScan.active) return;
      const s = result.mspScan;
      s.totalCount = msg.count || s.totalCount || 0;
      s.count = msg.count || s.count || 0;

      if (s.phase === "top") {
        // Switch to latest for same ticker
        s.phase = "latest";
        const remaining = s.perTicker - (s.count - (s.totalCount - s.count));
        chrome.storage.local.set({ mspScan: s }, () => {
          const url = `https://x.com/search?q=%24${encodeURIComponent(s.currentTicker)}&src=typed_query&f=live`;
          navigateAndStartScan(url, s.currentTicker, Math.max(remaining, s.perTab));
        });
      } else {
        // Latest done — move to next ticker
        s.tickerIndex++;
        chrome.storage.local.set({ mspScan: s }, () => {
          startNextTicker();
        });
      }
    });
    return;
  }

  if (msg.type === "stop_scan") {
    chrome.storage.local.remove("mspScan", () => {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
          chrome.tabs.sendMessage(tabs[0].id, { type: "stop_scan" });
        }
      });
      chrome.action.setBadgeText({ text: savedCount > 0 ? String(savedCount) : "" });
      startScanPolling(); // Resume polling
    });
    sendResponse({ ok: true });
    return true;
  }

  if (msg.type === "scan_progress") {
    const { count } = msg;
    chrome.action.setBadgeText({ text: `${count}` });
    chrome.action.setBadgeBackgroundColor({ color: "#1d9bf0" });
    chrome.storage.local.get("mspScan", (result) => {
      if (result.mspScan) {
        result.mspScan.totalCount = count;
        result.mspScan.count = count;
        chrome.storage.local.set({ mspScan: result.mspScan });
      }
    });
    return;
  }

  if (msg.type === "scan_done") {
    chrome.storage.local.remove("mspScan");
    savedCount += msg.count || 0;
    chrome.action.setBadgeText({ text: savedCount > 0 ? String(savedCount) : "" });
    chrome.action.setBadgeBackgroundColor({ color: "#00ba7c" });
    return;
  }
});
