/**
 * Market Sentiment Plugin — Background service worker
 * Tracks badge count + handles tab screenshot capture.
 */

let savedCount = 0;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "post_saved") {
    savedCount++;
    chrome.action.setBadgeText({ text: String(savedCount) });
    chrome.action.setBadgeBackgroundColor({ color: "#00ba7c" });
    return;
  }

  if (msg.type === "capture_tab") {
    // Capture the visible tab as a PNG data URL
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
    // Return true to keep the message channel open for async sendResponse
    return true;
  }
});
