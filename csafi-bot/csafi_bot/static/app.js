const ACTIVE = new Set(["queued", "downloading", "analysing", "writing"]);

function localTimes(root = document) {
  for (const el of root.querySelectorAll("time[datetime]")) {
    const d = new Date(el.getAttribute("datetime"));
    if (!Number.isNaN(d.getTime())) el.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
}

function setupFilters() {
  const form = document.getElementById("filters");
  if (!form) return;
  let timer;
  form.q.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => form.submit(), 400);
  });
  form.status.addEventListener("change", () => form.submit());
}

function setupUpload() {
  const form = document.querySelector("[data-upload]");
  if (!form) return;
  const drop = form.querySelector("[data-drop]");
  const input = drop.querySelector("input[type=file]");
  const title = drop.querySelector("[data-drop-title]");
  const hint = drop.querySelector("[data-drop-hint]");
  const idle = { title: title.textContent, hint: hint.textContent };

  const show = () => {
    const file = input.files[0];
    drop.classList.toggle("has-file", Boolean(file));
    title.textContent = file ? file.name : idle.title;
    hint.textContent = file ? `${(file.size / 1048576).toFixed(1)} MB. Click to choose another file.` : idle.hint;
  };
  input.addEventListener("change", show);
  for (const name of ["dragenter", "dragover"]) {
    drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.add("is-over"); });
  }
  for (const name of ["dragleave", "drop"]) {
    drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.remove("is-over"); });
  }
  drop.addEventListener("drop", (e) => {
    if (!e.dataTransfer.files.length) return;
    input.files = e.dataTransfer.files;
    show();
  });
  form.addEventListener("submit", () => {
    const button = form.querySelector("[data-submit]");
    button.disabled = true;
    button.textContent = "Uploading…";
  });
}

async function refreshList() {
  const rows = document.getElementById("rows");
  if (!rows) return;
  try {
    const status = await (await fetch("/api/status", { headers: { Accept: "application/json" } })).json();
    const c = status.counts;
    const set = (k, v) => { const el = document.querySelector(`[data-count="${k}"]`); if (el) el.textContent = v; };
    set("filed", c.filed);
    set("active", c.queued + c.downloading + c.analysing + c.writing);
    set("failed", c.failed);
    set("duplicate", c.duplicate);
    const ai = document.getElementById("ai-status");
    if (ai) {
      ai.textContent = status.ai;
      ai.classList.toggle("ready", status.ai.startsWith("Ollama ready"));
    }
    const r = await fetch(rows.dataset.src);
    if (r.ok) rows.innerHTML = await r.text();
  } catch {
    /* offline or server restarting; try again next tick */
  }
}

function setupTabs() {
  const tabs = document.querySelectorAll("[role=tab]");
  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      for (const t of tabs) t.setAttribute("aria-selected", String(t === tab));
      for (const p of document.querySelectorAll("[data-panel]")) p.hidden = p.dataset.panel !== tab.dataset.tab;
    });
  }
}

function setupReelRefresh() {
  const head = document.querySelector("[data-reel]");
  if (!head || !ACTIVE.has(head.dataset.status)) return;
  setInterval(async () => {
    try {
      const r = await (await fetch(`/api/reels/${head.dataset.reel}`)).json();
      if (r.status !== head.dataset.status || !ACTIVE.has(r.status)) location.reload();
      const badge = head.querySelector(".badge");
      if (badge && r.stage) badge.textContent = `${r.stage}…`;
    } catch { /* ignore */ }
  }, 4000);
}

localTimes();
setupFilters();
setupUpload();
setupTabs();
setupReelRefresh();
if (document.getElementById("rows")) setInterval(refreshList, 5000);
