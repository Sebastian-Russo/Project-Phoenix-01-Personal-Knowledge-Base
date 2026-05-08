/*
 * Popup logic — runs when the user clicks the extension icon.
 *
 * Gets the current tab's URL and title, shows them in the popup,
 * and sends a save request to background.js when the user clicks save.
 *
 * We delegate the actual API call to background.js because
 * popup scripts are destroyed when the popup closes — a fetch
 * started here would be cancelled if the user closes the popup
 * before it completes. The service worker survives popup close.
 */

let currentUrl   = "";
let currentTitle = "";

// ── Init ───────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    currentUrl   = tab.url   || "";
    currentTitle = tab.title || "";

    document.getElementById("page-title").textContent =
      currentTitle || currentUrl;

    // Pre-fill tags based on domain — helpful default
    const domain = new URL(currentUrl).hostname.replace("www.", "");
    document.getElementById("tags-input").value = domain;

  } catch (e) {
    setStatus("Could not read current tab", "error");
  }
});

// ── Save ───────────────────────────────────────────────────────────────────

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
        // Auto-close popup after success
        setTimeout(() => window.close(), 1500);
      } else {
        setStatus(response.error || "Save failed", "error");
      }
    }
  );
}

// ── Open dashboard ─────────────────────────────────────────────────────────

function openDashboard() {
  chrome.tabs.create({ url: "http://localhost:5000" });
}

// ── Helpers ────────────────────────────────────────────────────────────────

function setStatus(text, type) {
  const el       = document.getElementById("status");
  el.textContent = text;
  el.className   = `status status-${type}`;
}
