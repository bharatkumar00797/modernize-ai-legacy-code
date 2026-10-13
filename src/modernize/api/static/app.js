"use strict";
// Dashboard for the Modernize AI API. All server data is rendered with textContent only.
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { config: null, examples: {}, current: null, cursor: 0, timer: null, tab: "smells" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function headers() {
    const h = { "Content-Type": "application/json" };
    const key = $("apikey").value.trim();
    if (key) h["X-API-Key"] = key;
    return h;
  }

  async function api(path, options) {
    const res = await fetch(path, Object.assign({ headers: headers() }, options || {}));
    const ctype = res.headers.get("content-type") || "";
    const body = ctype.includes("json") ? await res.json() : await res.text();
    if (!res.ok) {
      let detail = body && body.detail !== undefined ? body.detail : body;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      throw new Error(res.status + ": " + (typeof detail === "string" ? detail : JSON.stringify(detail)));
    }
    return body;
  }

  function setStatus(text) { $("status").textContent = text; }

  function writeHash() {
    if (!state.current) return;
    const hash = "#run=" + state.current.id + "&tab=" + state.tab;
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }

  function parseHash() {
    const run = /(?:^|[&#])run=([A-Za-z0-9_-]+)/.exec(location.hash);
    const tab = /(?:^|[&#])tab=([a-z]+)/.exec(location.hash);
    return { run: run ? run[1] : null, tab: tab ? tab[1] : null };
  }

  async function init() {
    try {
      state.config = await api("/api/config");
    } catch (err) { setStatus(err.message); return; }
    const cfg = state.config;
    $("mode").textContent = cfg.access_mode + " | v" + cfg.version + (cfg.sync_runs ? " | sync" : "");
    $("key-wrap").hidden = !cfg.auth_required;
    $("max-steps").max = String(cfg.max_steps_cap);
    $("max-steps").value = String(Math.min(20, cfg.max_steps_cap));
    $("apikey").value = sessionStorage.getItem("modernize-key") || "";
    if (cfg.auth_required && !$("apikey").value) { setStatus("Enter an API key to continue."); }
    await loadCatalog();
    const link = parseHash();
    if (link.tab && ["smells", "steps", "diff", "report"].indexOf(link.tab) >= 0) {
      switchTab(link.tab, false);
    }
    if (link.run) openRun(link.run);
  }

  async function loadCatalog() {
    try {
      const sc = await api("/api/examples");
      const select = $("example");
      select.replaceChildren();
      sc.examples.forEach((s) => {
        state.examples[s.id] = s;
        const opt = el("option", null, s.title);
        opt.value = s.id;
        select.appendChild(opt);
      });
      showExample();
      refreshHistory();
    } catch (err) { setStatus(err.message); }
  }

  function showExample() {
    const s = state.examples[$("example").value];
    $("description").textContent = s ? s.description : "";
    const list = $("targets");
    list.replaceChildren();
    if (s) s.targets.forEach((t) => list.appendChild(el("li", null, t)));
  }

  async function run() {
    const body = {
      example: $("example").value,
      max_steps: Number($("max-steps").value) || undefined,
    };
    sessionStorage.setItem("modernize-key", $("apikey").value.trim());
    $("run").disabled = true;
    setStatus("Modernizing...");
    try {
      const created = await api("/api/runs", { method: "POST", body: JSON.stringify(body) });
      await startView(created);
      if (created.state === "finished" || created.state === "error") {
        (created.events || []).forEach(addEvent);
        finish(created);
      } else {
        poll();
      }
    } catch (err) {
      setStatus(err.message);
      $("run").disabled = false;
    }
  }

  async function openRun(id) {
    try {
      const detail = await api("/api/runs/" + encodeURIComponent(id));
      await startView(detail);
      if (detail.state === "finished" || detail.state === "error") {
        const page = await api("/api/runs/" + id + "/events?since=0");
        page.events.forEach(addEvent);
        state.cursor = page.next_cursor;
        finish(detail);
      } else {
        poll();
      }
    } catch (err) { setStatus(err.message); }
  }

  async function startView(detail) {
    clearTimeout(state.timer);
    state.current = { id: detail.id, report: null };
    state.cursor = 0;
    writeHash();
    $("result").hidden = false;
    $("timeline").replaceChildren();
    $("diff").replaceChildren();
    $("report").replaceChildren();
    $("smells-before").querySelector("tbody").replaceChildren();
    $("smells-after").querySelector("tbody").replaceChildren();
    $("headline").textContent = "Running: " + detail.title;
    if (state.examples[detail.example] && $("example").value !== detail.example) {
      $("example").value = detail.example;
      showExample();
    }
  }

  async function poll() {
    const cur = state.current;
    if (!cur) return;
    try {
      const page = await api("/api/runs/" + cur.id + "/events?since=" + state.cursor);
      page.events.forEach(addEvent);
      state.cursor = page.next_cursor;
      if (page.done) {
        finish(await api("/api/runs/" + cur.id));
        return;
      }
    } catch (err) { setStatus(err.message); }
    state.timer = setTimeout(poll, 600);
  }

  function addEvent(ev) {
    if (ev.event !== "step") return;
    const li = el("li", ev.status || null);
    const head = el("div");
    head.appendChild(el("span", "who", "step " + ev.index + " " + ev.transform));
    head.appendChild(el("span", "stage", ev.status + " · " + ev.path));
    li.appendChild(head);
    li.appendChild(el("p", "small", ev.reason || ""));
    $("timeline").appendChild(li);
  }

  function finish(detail) {
    $("run").disabled = false;
    refreshHistory();
    if (detail.state === "error" || !detail.report) {
      $("headline").textContent = "Run failed: " + (detail.error || "unknown error");
      setStatus("");
      return;
    }
    const report = detail.report;
    state.current.report = report;
    setStatus("Done.");
    const head = $("headline");
    head.replaceChildren();
    head.appendChild(el("div", null, "Tests"));
    const label = report.final.green ? "GREEN" : "RED";
    head.appendChild(el("strong", null, label));
    head.appendChild(el("span", "small",
      "  " + report.baseline.passed + " -> " + report.final.passed + " passing; " +
      detail.kept + " kept, " + detail.rejected + " rejected; smells " +
      detail.smells_before + " -> " + detail.smells_after));
    fillSmellTable($("smells-before"), report.smells_before || []);
    fillSmellTable($("smells-after"), report.smells_after || []);
    renderDiff(report.diff || "");
    loadReportText();
  }

  function fillSmellTable(table, smells) {
    const body = table.querySelector("tbody");
    body.replaceChildren();
    if (!smells.length) {
      const tr = el("tr");
      const td = el("td", "small", "None");
      td.colSpan = 4;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    smells.slice(0, 80).forEach((s) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, s.kind));
      tr.appendChild(el("td", null, s.path));
      tr.appendChild(el("td", null, String(s.line)));
      tr.appendChild(el("td", "sev-" + s.severity, s.severity));
      body.appendChild(tr);
    });
  }

  function renderDiff(text) {
    const root = $("diff");
    root.replaceChildren();
    if (!text) { root.appendChild(el("span", "meta", "(no changes)")); return; }
    text.split("\n").forEach((line) => {
      let cls = "";
      if (line.startsWith("+") && !line.startsWith("+++")) cls = "add";
      else if (line.startsWith("-") && !line.startsWith("---")) cls = "del";
      else if (line.startsWith("@@") || line.startsWith("---") || line.startsWith("+++")) cls = "meta";
      const span = el("span", cls, line + "\n");
      root.appendChild(span);
    });
  }

  async function loadReportText() {
    if (!state.current) return;
    try {
      const md = await api("/api/runs/" + state.current.id + "/report.md");
      $("report").textContent = md;
    } catch (err) { $("report").textContent = "Report unavailable: " + err.message; }
  }

  async function download(kind, filename) {
    if (!state.current) return;
    try {
      const text = await api("/api/runs/" + state.current.id + "/" + kind);
      const blob = new Blob([text], { type: "text/plain" });
      const a = el("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch (err) { setStatus("Download failed: " + err.message); }
  }

  async function refreshHistory() {
    try {
      const list = await api("/api/runs?limit=10");
      const ul = $("history");
      ul.replaceChildren();
      if (!list.runs.length) ul.appendChild(el("li", "small", "No runs yet."));
      list.runs.forEach((d) => {
        const li = el("li");
        const a = el("a", null, d.title);
        a.href = "#run=" + d.id;
        a.addEventListener("click", (e) => { e.preventDefault(); openRun(d.id); });
        li.appendChild(a);
        li.appendChild(el("span", "small", "  " + d.state +
          (d.tests_green === true ? " GREEN" : d.tests_green === false ? " RED" : "") +
          " · " + d.kept + " kept"));
        ul.appendChild(li);
      });
    } catch (err) { /* history is optional */ }
  }

  function switchTab(name, persist) {
    state.tab = name;
    document.querySelectorAll(".tabs button").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    ["smells", "steps", "diff", "report"].forEach((t) => { $("tab-" + t).hidden = t !== name; });
    if (persist !== false) writeHash();
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("example").addEventListener("change", showExample);
    $("run").addEventListener("click", run);
    $("download-report").addEventListener("click", () => download("report.md", state.current.id + "-report.md"));
    $("download-patch").addEventListener("click", () => download("patch.diff", state.current.id + ".diff"));
    $("apikey").addEventListener("change", loadCatalog);
    document.querySelectorAll(".tabs button").forEach((b) => {
      b.addEventListener("click", () => switchTab(b.dataset.tab));
    });
    init();
  });
})();
