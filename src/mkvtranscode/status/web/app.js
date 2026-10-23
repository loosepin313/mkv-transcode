// mkv-transcode status page. Reads /api/snapshot, then one /api/job call for
// the selected job. All text goes in with textContent, so log lines cannot
// inject markup.

const $ = (id) => document.getElementById(id);
const state = { snap: { recent: [], queue: [] }, selected: null, pane: "plan", detail: null, timer: null, ms: 5000 };

// ---- format helpers ----------------------------------------------------

function bytes(n) {
  if (!n) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return (v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)) + " " + units[i];
}

function dur(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + "h " + String(m).padStart(2, "0") + "m";
  if (m) return m + "m " + String(sec).padStart(2, "0") + "s";
  return sec + "s";
}

function ago(epoch) {
  if (!epoch) return "—";
  const diff = Date.now() / 1000 - epoch;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.round(diff / 60) + " min ago";
  if (diff < 86400) return (diff / 3600).toFixed(1) + " h ago";
  return new Date(epoch * 1000).toLocaleDateString();
}

function clock(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function pct(value) {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  return value.toFixed(1) + "%";
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

// ---- theme -------------------------------------------------------------

function setTheme(name) {
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("mkvtc-theme", name); } catch (e) { /* private mode */ }
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("mkvtc-theme"); } catch (e) { /* private mode */ }
  if (saved) setTheme(saved);
  $("theme").addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
}

// ---- cards -------------------------------------------------------------

function renderCards() {
  const s = state.snap;
  const card = $("service-card");
  const service = s.service || "unknown";
  card.className = "card " + service;
  setText("service-text", service === "active" ? "Watching" : service);
  setText("incoming", s.config ? s.config.paths.incoming : "—");
  setText("profile", s.config
    ? s.config.profile + " · " + s.config.encoder + " · CRF " + s.config.crf + " · preset " + s.config.preset
    : "—");

  const running = s.running || [];
  setText("running", String(running.length));
  const runCard = $("running-card");
  runCard.className = "card" + (running.length ? " running" : "");
  if (running.length) {
    const job = running[0];
    const percent = job.progress ? job.progress.percent : null;
    const left = [job.title || job.name];
    if (percent !== null) left.push(pct(percent), "pass " + job.progress.pass);
    if (job.eta_seconds) left.push(dur(job.eta_seconds) + " left");
    setText("running-sub", left.join(" · "));
    $("running-bar").hidden = percent === null;
    $("running-fill").style.width = (percent || 0) + "%";
  } else {
    setText("running-sub", (s.queue || []).length ? s.queue.length + " file(s) waiting" : "Idle");
    $("running-bar").hidden = true;
  }

  const counts = s.counts || {};
  setText("done", String(counts.done || 0));
  const parts = [];
  if (counts.failed) parts.push(counts.failed + " failed");
  if (counts.skipped) parts.push(counts.skipped + " skipped");
  if (counts.running) parts.push(counts.running + " running");
  setText("done-sub", parts.length ? parts.join(" · ") : (s.state_exists ? "no failures" : "no state file yet"));

  const totals = s.totals || {};
  setText("saved", totals.saved_percent === null || totals.saved_percent === undefined ? "—" : pct(totals.saved_percent));
  setText("saved-sub", totals.jobs
    ? bytes(totals.source_bytes) + " → " + bytes(totals.output_bytes) + " · " + dur(totals.encode_seconds) + " encoding"
    : "—");

  const disk = s.disk || {};
  setText("free", bytes(disk.free));
  const freeNote = [];
  if (disk.total) freeNote.push("of " + bytes(disk.total));
  if (disk.min_free_bytes) freeNote.push("stop below " + bytes(disk.min_free_bytes));
  setText("free-sub", freeNote.join(" · ") || "—");
}

// ---- job list ----------------------------------------------------------

function visibleJobs() {
  const s = state.snap || {};
  const jobs = s.recent || [];
  const want = $("filter").value;
  const term = $("search").value.trim().toLowerCase();
  return jobs.filter((job) => {
    if (want !== "all" && job.status !== want) return false;
    if (!term) return true;
    return (job.title || "").toLowerCase().includes(term)
      || (job.name || "").toLowerCase().includes(term)
      || (job.imdb_id || "").toLowerCase().includes(term);
  });
}

function renderJobs() {
  const list = $("jobs");
  const jobs = visibleJobs();
  list.textContent = "";
  if (!jobs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    const strong = document.createElement("strong");
    strong.textContent = "No jobs";
    empty.appendChild(strong);
    empty.appendChild(document.createTextNode("The state file has no matching job."));
    list.appendChild(empty);
    return;
  }
  jobs.forEach((job) => {
    const button = document.createElement("button");
    button.className = "job" + (state.selected === job.name ? " selected" : "");
    button.type = "button";

    const top = document.createElement("div");
    top.className = "row";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = job.title || job.name;
    const tag = document.createElement("span");
    tag.className = "tag " + job.status;
    tag.textContent = job.status;
    top.appendChild(name);
    top.appendChild(tag);

    const meta = document.createElement("div");
    meta.className = "row";
    const file = document.createElement("div");
    file.className = "endpoint";
    file.textContent = job.name;
    const right = document.createElement("div");
    right.className = "pct";
    if (job.status === "running" && job.progress) {
      right.textContent = pct(job.progress.percent) + " · " + dur(job.elapsed) + " in";
    } else if (job.saved_percent !== null && job.saved_percent !== undefined) {
      right.textContent = "−" + job.saved_percent.toFixed(1) + "% · " + dur(job.seconds);
    } else {
      right.textContent = dur(job.seconds);
    }
    meta.appendChild(file);
    meta.appendChild(right);

    button.appendChild(top);
    button.appendChild(meta);
    if (job.status === "running" && job.progress) {
      const bar = document.createElement("div");
      bar.className = "bar thin";
      const fill = document.createElement("i");
      fill.style.width = job.progress.percent + "%";
      bar.appendChild(fill);
      button.appendChild(bar);
    }
    button.addEventListener("click", () => select(job));
    list.appendChild(button);
  });
}

// ---- detail pane -------------------------------------------------------

function select(job) {
  state.selected = job.name;
  state.detail = null;
  $("no-selection").hidden = true;
  $("selection").hidden = false;
  renderJobs();
  renderDetail(job);
  fetch("/api/job?name=" + encodeURIComponent(job.name))
    .then((r) => r.json())
    .then((data) => { state.detail = data; renderDetail(job); })
    .catch(() => { state.detail = null; renderDetail(job); });
}

function renderDetail(job) {
  setText("detail-title", job.name);
  const tag = $("detail-status");
  tag.className = "tag " + job.status;
  tag.textContent = job.status;
  setText("detail-time", (job.imdb_id ? job.imdb_id + " · " : "") + clock(job.started)
    + (job.finished ? " → " + clock(job.finished) : ""));

  const running = job.status === "running" && job.progress;
  $("detail-bar").hidden = !running;
  if (running) $("detail-fill").style.width = job.progress.percent + "%";

  setText("detail-source", bytes(job.source_size));
  setText("detail-output", bytes(job.output_size));
  setText("detail-saved", job.saved_percent === null || job.saved_percent === undefined
    ? "—" : pct(job.saved_percent));
  setText("detail-time-taken", dur(job.seconds || (running ? job.elapsed : 0)));
  setText("detail-started", ago(job.started));
  setText("detail-elapsed", running ? dur(job.elapsed) : "—");
  setText("detail-eta", running && job.eta_seconds ? dur(job.eta_seconds) : "—");
  setText("detail-profile", job.profile || "—");

  const error = $("detail-error");
  if (job.status === "failed" && job.message) {
    error.hidden = false;
    error.textContent = job.message;
  } else {
    error.hidden = true;
    error.textContent = "";
  }

  const notes = $("detail-notes");
  const noteLines = (state.detail && state.detail.notes) || job.notes || [];
  if (noteLines.length) {
    notes.hidden = false;
    notes.textContent = noteLines.join("\n");
    notes.style.whiteSpace = "pre-wrap";
  } else {
    notes.hidden = true;
    notes.textContent = "";
  }
  renderContent(job);
}

function renderContent(job) {
  const detail = state.detail || {};
  const pane = state.pane;
  let text = "", note = "";
  if (pane === "plan") {
    const plan = detail.plan && detail.plan.length ? detail.plan : (job.plan || []);
    text = plan.join("\n");
    note = text ? "The plan the pipeline sent to ffmpeg." : "No plan lines in the log.";
  } else if (pane === "log") {
    const tail = detail.log_tail || [];
    text = tail.join("\n");
    note = detail.found === false
      ? "No log file for this job."
      : "Last " + tail.length + " lines of " + (detail.log_path || "the job log") + ".";
  } else {
    text = detail.report ? JSON.stringify(detail.report, null, 2) : "";
    note = text ? "The JSON report for this job." : "No report file for this job.";
  }
  $("content").textContent = text || "—";
  setText("content-note", note);
}

function renderQueue() {
  const s = state.snap || {};
  const body = $("queue");
  const queue = s.queue || [];
  body.textContent = "";
  setText("queue-note", queue.length
    ? queue.length + " file(s) in " + (s.config ? s.config.paths.incoming : "")
    : "empty");
  if (!queue.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "small";
    cell.textContent = "Nothing waiting.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  queue.forEach((item) => {
    const row = document.createElement("tr");
    const file = document.createElement("td");
    file.className = "file";
    file.textContent = item.name;
    const size = document.createElement("td");
    size.className = "num";
    size.textContent = bytes(item.size);
    const age = document.createElement("td");
    age.className = "num";
    age.textContent = ago(item.mtime);
    const status = document.createElement("td");
    status.className = "small";
    status.textContent = item.hidden
      ? "ignored (hidden)"
      : (!item.seen ? "new file" : (item.stable ? "stable" : "size still changing"));
    row.appendChild(file);
    row.appendChild(size);
    row.appendChild(age);
    row.appendChild(status);
    body.appendChild(row);
  });
}

// ---- polling -----------------------------------------------------------

function current() {
  const list = (state.snap && state.snap.recent) || [];
  return list.find((item) => item.name === state.selected);
}

function render() {
  renderCards();
  renderJobs();
  renderQueue();
  const job = current();
  if (job) renderDetail(job);
  setText("updated", "Updated " + new Date().toLocaleTimeString()
    + " · " + (state.snap.host || "") + " · v" + (state.snap.version || "?"));
  const error = $("error");
  if (state.snap.error) {
    error.hidden = false;
    error.textContent = "Snapshot failed: " + state.snap.error;
  } else if (!state.snap.state_exists) {
    error.hidden = false;
    error.textContent = "No state file yet at " + state.snap.state_file
      + ". The watcher creates it on the first job.";
  } else {
    error.hidden = true;
  }
}

function load() {
  fetch("/api/snapshot")
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((data) => {
      state.snap = data;
      if (data.interval) state.ms = data.interval * 1000;
      render();
      schedule();
    })
    .catch((exc) => {
      $("error").hidden = false;
      $("error").textContent = "Cannot reach the status server: " + exc.message;
    });
}

let scheduled = 0;
function schedule() {
  if (scheduled === state.ms) return;
  if (state.timer) clearInterval(state.timer);
  scheduled = state.ms;
  state.timer = setInterval(load, state.ms);
}

// ---- wiring ------------------------------------------------------------

initTheme();
$("refresh").addEventListener("click", load);
$("filter").addEventListener("change", renderJobs);
$("search").addEventListener("input", renderJobs);
document.querySelectorAll(".tabs [data-pane]").forEach((button) => {
  button.addEventListener("click", () => {
    state.pane = button.dataset.pane;
    document.querySelectorAll(".tabs [data-pane]").forEach((other) => {
      other.setAttribute("aria-selected", String(other === button));
    });
    const job = current();
    if (job) renderContent(job);
  });
});
$("copy").addEventListener("click", () => {
  const text = $("content").textContent;
  if (navigator.clipboard) navigator.clipboard.writeText(text);
  $("copy").textContent = "Copied";
  setTimeout(() => { $("copy").textContent = "Copy"; }, 1200);
});

load();
