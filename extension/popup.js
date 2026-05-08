let currentUrl   = "";
let currentTitle = "";

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    currentUrl   = tab.url   || "";
    currentTitle = tab.title || "";

    document.getElementById("page-title").textContent =
      currentTitle || currentUrl;

    const domain = new URL(currentUrl).hostname.replace("www.", "");
    document.getElementById("tags-input").value = domain;

  } catch (e) {
    setStatus("Could not read current tab", "error");
  }
});

async function savePage() {
  if (!currentUrl) {
    setStatus("No URL to save", "error");
    return;
  }

  if (!currentUrl.startsWith("http")) {
    setStatus("Cannot save this page type", "error");
    return;
  }

  const tagsRaw = document.getElementById("tags-input").value.trim();
  const tags    = tagsRaw
    ? tagsRaw.split(",").map(t => t.trim()).filter(Boolean)
    : [];

  const btn = document.getElementById("save-btn");
  btn.disabled = true;
  setStatus("Saving...", "loading");

  chrome.runtime.sendMessage(
    { type: "SAVE_PAGE", payload: { url: currentUrl, tags } },
    (response) => {
      btn.disabled = false;

      if (chrome.runtime.lastError) {
        setStatus("Extension error — is Flask running?", "error");
        return;
      }

      if (response.success) {
        setStatus(`✓ Saved: ${response.data.title}`, "success");
        setTimeout(() => window.close(), 1500);
      } else {
        setStatus(response.error || "Save failed", "error");
      }
    }
  );
}

function openDashboard() {
  chrome.tabs.create({ url: "http://localhost:5000" });
}

function setStatus(text, type) {
  const el       = document.getElementById("status");
  el.textContent = text;
  el.className   = `status status-${type}`;
}
