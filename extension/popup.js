const API_URL = "http://localhost:5050";

const healthDot = document.getElementById("health-dot");
const healthText = document.getElementById("health-text");
const countEl = document.getElementById("count");
const clearBtn = document.getElementById("clear-btn");

async function refresh() {
  // Health check
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

  // Post count
  try {
    const r = await fetch(`${API_URL}/posts`);
    const data = await r.json();
    countEl.textContent = data.count;
  } catch {
    countEl.textContent = "—";
  }
}

clearBtn.addEventListener("click", async () => {
  try {
    await fetch(`${API_URL}/posts`, { method: "DELETE" });
    countEl.textContent = "0";
  } catch {
    // ignore
  }
});

refresh();
