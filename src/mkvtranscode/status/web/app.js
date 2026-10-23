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

function renderSystem() {
  const sys = state.snap.system || {};
  const box = $("system-cards");
  box.textContent = "";
  const mem = sys.memory || {};
  const cards = [];
  const hasCpu = sys.cpu_percent !== null && sys.cpu_percent !== undefined;
  cards.push({
    label: "CPU",
    value: hasCpu ? Math.round(sys.cpu_percent) + "%" : "\u2014",
    sub: (sys.load && sys.load.length) ? "load " + sys.load.join(" / ") : "",
    bar: hasCpu ? sys.cpu_percent : null,
  });
  cards.push({
    label: "Memory",
    value: mem.total_mb ? Math.round(mem.used_mb) + "/" + Math.round(mem.total_mb) + " MiB" : "\u2014",
    sub: (mem.available_mb !== undefined ? Math.round(mem.available_mb) + " MiB free" : "")
      + (mem.swap_used_mb ? " \u00b7 swap " + Math.round(mem.swap_used_mb) + " MiB" : ""),
    bar: mem.total_mb ? (mem.used_mb / mem.total_mb) * 100 : null,
  });
  cards.push({ label: "Cores", value: String(sys.cores || "\u2014"), sub: sys.cpu_model || "" });
  if (sys.top_c !== null && sys.top_c !== undefined) {
    cards.push({ label: "Temperature", value: Math.round(sys.top_c) + " C", sub: sys.top_label || "" });
  }
  cards.forEach((item) => {
    const card = document.createElement("div");
    card.className = "card";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = item.label;
    const value = document.createElement("div");
    value.className = "value";
    value.textContent = item.value;
    card.appendChild(label);
    card.appendChild(value);
    if (item.sub) {
      const sub = document.createElement("div");
      sub.className = "model";
      sub.textContent = item.sub;
      card.appendChild(sub);
    }
    if (item.bar !== null && item.bar !== undefined) {
      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("i");
      fill.style.width = Math.max(0, Math.min(100, item.bar)) + "%";
      bar.appendChild(fill);
      card.appendChild(bar);
    }
    box.appendChild(card);
  });
}

function renderGpus() {
  const s = state.snap;
  const gpus = s.gpus || [];
  const box = $("gpu-cards");
  box.textContent = "";
  box.hidden = gpus.length === 0;
  if (!gpus.length) return;
  const busy = {};
  (s.running || []).forEach((job) => {
    const label = job.title || job.name;
    const held = (job.gpus || []).length ? job.gpus : (job.gpu === null || job.gpu === undefined ? [] : [job.gpu]);
    held.forEach((index) => { busy[index] = label; });
  });
  gpus.forEach((gpu) => {
    const card = document.createElement("div");
    card.className = "card";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "GPU " + gpu.index + " \u00b7 " + (gpu.name || "unknown");
    const value = document.createElement("div");
    value.className = "value";
    value.textContent = gpu.util_percent === null ? "\u2014" : Math.round(gpu.util_percent) + "%";
    const sub = document.createElement("div");
    sub.className = "model";
    const bits = [];
    if (gpu.memory_used_mb !== null && gpu.memory_total_mb !== null) {
      bits.push(Math.round(gpu.memory_used_mb) + "/" + Math.round(gpu.memory_total_mb) + " MiB");
    }
    if (gpu.encode_sessions) {
      bits.push(gpu.encode_sessions + " encode " + Math.round(gpu.encode_fps || 0) + " fps");
    }
    if (gpu.temperature_c !== null) bits.push(Math.round(gpu.temperature_c) + " C");
    if (busy[gpu.index]) bits.push(busy[gpu.index]);
    sub.textContent = bits.join(" \u00b7 ");
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("i");
    fill.style.width = (gpu.util_percent || 0) + "%";
    bar.appendChild(fill);
    card.appendChild(label);
    card.appendChild(value);
    card.appendChild(sub);
    card.appendChild(bar);
    box.appendChild(card);
  });
}

function renderCards() {
  const s = state.snap;
  const card = $("service-card");
  const service = s.service || "unknown";
  card.className = "card " + service;
  setText("service-text", service === "active" ? "Watching" : service);
  setText("incoming", s.config ? s.config.paths.incoming : "—");
  const host = s.host || "";
  setText("page-title", "Transcode Monitor" + (host ? " (" + host + ")" : ""));
  document.title = "mkv-transcode Monitor" + (host ? " (" + host + ")" : "");
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
    if ((job.gpus || []).length) left.push("GPU " + job.gpus.join("+"));
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
  const floor = queue.length ? bytes(queue[0].min_size_bytes) : "";
  const ignored = queue.filter((item) => item.reason).length;
  setText("queue-note", queue.length
    ? queue.length + " file(s) in " + (s.config ? s.config.paths.incoming : "")
      + " · floor " + floor + (ignored ? " · " + ignored + " ignored" : "")
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
    status.textContent = item.reason
      ? item.reason + " (" + bytes(item.min_size_bytes) + " floor)"
      : (item.seen ? (item.stable ? "stable" : "size still changing") : "new file");
    row.appendChild(file);
    row.appendChild(size);
    row.appendChild(age);
    row.appendChild(status);
    body.appendChild(row);
  });
}

// ---- imdb search in the upload panel ----------------------------------

let imdbTimer = null;

function searchImdb() {
  const term = $("imdb-q").value.trim();
  if (!term) {
    $("imdb-results").textContent = "";
    setText("imdb-note", "");
    return;
  }
  setText("imdb-note", "searching ...");
  fetch("/api/imdb?q=" + encodeURIComponent(term) + "&limit=12")
    .then((r) => r.json())
    .then((data) => renderImdb(data))
    .catch(() => setText("imdb-note", "the search failed"));
}

function renderImdb(data) {
  const box = $("imdb-results");
  box.textContent = "";
  const results = data.results || [];
  setText("imdb-note", results.length ? results.length + " match(es) in the local index"
    : (data.note || "no match"));
  results.forEach((item) => {
    const row = document.createElement("div");
    row.className = "res";
    const what = document.createElement("div");
    what.className = "t";
    const title = document.createElement("b");
    title.textContent = item.title + (item.year ? " (" + item.year + ")" : " (" + (item.type || "?") + ")");
    const meta = document.createElement("span");
    meta.textContent = [item.imdb_id, item.type, item.genre].filter(Boolean).join(" · ");
    what.appendChild(title);
    what.appendChild(meta);
    const use = document.createElement("button");
    use.type = "button";
    use.textContent = "Use name";
    use.addEventListener("click", () => {
      $("upload-as").value = item.title + (item.year ? " (" + item.year + ")" : "") + ".mkv";
      setText("imdb-note", "Upload as set to " + $("upload-as").value);
    });
    row.appendChild(what);
    row.appendChild(use);
    box.appendChild(row);
  });
}

// ---- upload and download ----------------------------------------------

let xhr = null;

function renderTransfer() {
  const s = state.snap;
  const up = s.upload || {};
  $("upload-off").hidden = !!up.enabled;
  $("upload-form").hidden = !up.enabled;
  if (up.enabled) {
    setText("upload-state", "max " + bytes(up.max_bytes) + " · " + bytes(up.free) + " free");
    $("file").accept = (up.extensions || []).map((e) => "." + e).join(",");
  } else {
    setText("upload-state", "off");
  }
  renderDownloads(s.downloads || []);
  setText("about-version", s.version || "-");
}

function renderDownloads(items) {
  const box = $("downloads");
  box.textContent = "";
  setText("download-note", items.length ? items.length + " finished file(s)" : "nothing finished yet");
  if (!items.length) {
    const none = document.createElement("div");
    none.className = "small";
    none.textContent = "No finished files in the library.";
    box.appendChild(none);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "dl";
    const what = document.createElement("div");
    what.className = "what";
    const label = document.createElement("b");
    label.textContent = item.label;
    const path = document.createElement("span");
    path.textContent = bytes(item.size) + " · " + item.path;
    what.appendChild(label);
    what.appendChild(path);
    const link = document.createElement("a");
    link.textContent = item.suffix;
    link.href = "/api/file?kind=" + encodeURIComponent(item.kind) + "&path=" + encodeURIComponent(item.path);
    link.setAttribute("download", "");
    row.appendChild(what);
    row.appendChild(link);
    box.appendChild(row);
  });
}

function note(text) { setText("upload-note", text); }

function startUpload() {
  const input = $("file");
  const file = input.files && input.files[0];
  if (!file || xhr) return;
  const name = ($("upload-as").value || "").trim() || file.name;
  xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload?name=" + encodeURIComponent(name));
  const total = file.size;
  xhr.upload.onprogress = (ev) => {
    const percent = ev.lengthComputable ? (ev.loaded / total) * 100 : 0;
    $("upload-fill").style.width = percent + "%";
    note(bytes(ev.loaded) + " of " + bytes(total) + " (" + percent.toFixed(1) + "%)");
  };
  xhr.onload = () => {
    let text = "";
    try {
      const data = JSON.parse(xhr.responseText);
      text = "Sent " + bytes(data.size) + ". The watcher starts when the size stops changing.";
    } catch (e) {
      text = xhr.responseText || ("HTTP " + xhr.status);
    }
    $("upload-fill").style.width = xhr.status === 200 ? "100%" : "0%";
    note(text);
    if (xhr.status === 200) {
      input.value = "";
      $("send").disabled = true;
    }
    xhr = null;
    $("cancel").disabled = true;
    load();
  };
  xhr.onerror = () => {
    note("The upload stopped. Check the free space, then try again.");
    xhr = null;
    $("cancel").disabled = true;
    $("send").disabled = false;
  };
  $("send").disabled = true;
  $("cancel").disabled = false;
  $("upload-fill").style.width = "0%";
  note("Sending " + name + " ...");
  xhr.send(file);
}

// ---- polling -----------------------------------------------------------

function current() {
  const list = (state.snap && state.snap.recent) || [];
  return list.find((item) => item.name === state.selected);
}

function render() {
  renderCards();
  renderSystem();
  renderGpus();
  renderJobs();
  renderQueue();
  renderTransfer();
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
  return fetch("/api/snapshot")
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

// The page reloads by itself, so a manual refresh must show that it ran.
function refreshNow() {
  const button = $("refresh");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Refreshing\u2026";
  if (state.timer) clearInterval(state.timer);
  scheduled = 0;                       // re-arm the timer from this click
  load().finally(() => {
    button.disabled = false;
    button.textContent = label;
  });
}

// ---- wiring ------------------------------------------------------------

initTheme();
$("refresh").addEventListener("click", refreshNow);
$("filter").addEventListener("change", renderJobs);
$("search").addEventListener("input", renderJobs);
$("file").addEventListener("change", () => {
  const file = $("file").files && $("file").files[0];
  $("send").disabled = !file;
  $("upload-fill").style.width = "0%";
  note(file ? "Ready: " + file.name + " (" + bytes(file.size) + ")" : "");
});
// ---- about popup -------------------------------------------------------

function openAbout() {
  $("about").hidden = false;
  $("about-close").focus();
}

function closeAbout() {
  $("about").hidden = true;
  $("info").focus();
}

$("info").addEventListener("click", openAbout);
$("about-close").addEventListener("click", closeAbout);
$("about").addEventListener("click", (ev) => {
  if (ev.target.id === "about") closeAbout();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("about").hidden) closeAbout();
});

$("send").addEventListener("click", startUpload);
$("imdb-go").addEventListener("click", searchImdb);
$("imdb-q").addEventListener("keydown", (ev) => { if (ev.key === "Enter") searchImdb(); });
$("imdb-q").addEventListener("input", () => {
  if (imdbTimer) clearTimeout(imdbTimer);
  imdbTimer = setTimeout(searchImdb, 400);
});
$("cancel").addEventListener("click", () => {
  if (!xhr) return;
  xhr.abort();
  xhr = null;
  note("Upload cancelled.");
  $("send").disabled = false;
  $("cancel").disabled = true;
  $("upload-fill").style.width = "0%";
});
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
