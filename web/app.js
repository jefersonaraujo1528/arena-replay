"use strict";
// Arena Replay - painel local. Sem bibliotecas: funciona offline em qualquer navegador moderno.

const POLL_MS = 1500;
const MIN_SEL = 0.5;          // segundos mínimos de um recorte
const AUTO_OPEN_IDLE_MS = 20000; // não troca o vídeo se a pessoa mexeu no editor há pouco
const $ = (id) => document.getElementById(id);

const state = {
  replays: [],
  byId: new Map(),
  selected: null,       // replay aberto
  sel: { start: 0, end: 0 },
  duration: 0,
  fps: 30,
  serverOffset: 0,      // relógio do servidor - relógio do aparelho (s)
  known: new Set(),     // ids já vistos
  pendingSeen: new Set(), // ids vistos ainda processando (para abrir quando ficarem prontos)
  lastEdit: 0,
  firstLoad: true,
  speeds: [1, 0.5, 0.25],
  speedIdx: 0,
};

const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

// ------------------------------------------------------------------ utilidades
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const serverNow = () => Date.now() / 1000 + state.serverOffset;
function fmtClock(ts) { return new Date(ts * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function fmtDay(ts) {
  const d = new Date(ts * 1000), today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "hoje";
  if (d.toDateString() === y.toDateString()) return "ontem";
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}
function fmtAgo(ts) {
  const s = Math.max(0, serverNow() - ts);
  if (s < 50) return "agora mesmo";
  if (s < 3600) return `há ${Math.round(s / 60)} min`;
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  return fmtDay(ts);
}
function fmtT(t) { // 0:03,2
  t = Math.max(0, t);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0").replace(".", ",")}`;
}
const fmtSec = (t) => `${t.toFixed(1).replace(".", ",")} s`;
function fmtSize(b) { return !b ? "" : b > 1048576 ? `${(b / 1048576).toFixed(1).replace(".", ",")} MB` : `${Math.round(b / 1024)} KB`; }

function toast(msg, { error = false, action = null, ms = 3500 } = {}) {
  const el = $("toast"), btn = $("toastBtn");
  $("toastTxt").textContent = msg;
  el.classList.toggle("err", error);
  btn.hidden = !action;
  if (action) { btn.textContent = action.label; btn.onclick = () => { el.classList.remove("show"); action.run(); }; }
  el.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove("show"), ms);
}

async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || 8000);
  try {
    const res = await fetch(path, { cache: "no-store", ...opts, signal: ctrl.signal });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Erro ${res.status}`);
    return body;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("O sistema demorou a responder. Tente de novo.");
    if (e instanceof TypeError) throw new Error("Sem conexão com o Arena Replay. Confira se está no Wi-Fi da arena.");
    throw e;
  } finally { clearTimeout(timer); }
}

function download(url) {
  const a = $("dlAnchor");
  a.href = url;
  a.click();
}

// ------------------------------------------------------------------ status do sistema
const CAM = { ok: "Ativa", starting: "Iniciando", reconnecting: "Reconectando", unavailable: "Indisponível", stopped: "Parada" };
const REC = { recording: "Gravando", starting: "Iniciando", restarting: "Reiniciando", stopped: "Parada" };

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    state.serverOffset = s.server_time - Date.now() / 1000;
    const map = { ok: ["Gravando", "ok"], warming: ["Preparando…", "warn"], degraded: ["Atenção", "err"] };
    const [label, cls] = map[s.overall] || ["—", ""];
    $("statusText").textContent = label;
    $("statusDot").className = "dot " + cls;
    $("sCam").textContent = (CAM[s.camera.state] || s.camera.state) + (s.camera.measured_fps ? ` · ${s.camera.measured_fps} fps` : "");
    $("sRec").textContent = REC[s.recorder.state] || s.recorder.state;
    $("sBuf").textContent = `${Math.round(s.recorder.buffer_seconds)} s`;
    $("sQueue").textContent = s.queue ? `${s.queue} na fila` : "Livre";
    $("sDisk").textContent = s.disk_free_mb > 2048 ? `${Math.round(s.disk_free_mb / 1024)} GB` : `${s.disk_free_mb} MB`;
    $("sLast").textContent = s.last_event_at ? `${fmtClock(s.last_event_at)} · ${s.last_device}` : "—";
    $("version").textContent = `Versão ${s.version}`;
    $("btnTest").hidden = !s.test_trigger_enabled;
    $("btnTestEmpty").hidden = !s.test_trigger_enabled;
    const probs = [...s.problems];
    if (s.camera.last_error && s.camera.state !== "ok") probs.push(s.camera.last_error);
    $("problems").replaceChildren(...probs.map((p) => h("li", { text: p })));
  } catch {
    $("statusText").textContent = "Sem conexão";
    $("statusDot").className = "dot err";
  }
}

function refreshSnapshot() {
  if ($("sheetStatus").hidden) return;
  const img = $("snapshot"), probe = new Image();
  probe.onload = () => { img.src = probe.src; img.hidden = false; $("snapEmpty").hidden = true; };
  probe.onerror = () => { img.hidden = true; $("snapEmpty").hidden = false; };
  probe.src = `/api/camera/snapshot.jpg?t=${Date.now()}`;
}

// ------------------------------------------------------------------ lista de replays
function progressOf(r) {
  if (r.status === "processing") return 0.92;
  const total = r.post_seconds + 1.5;
  return clamp((serverNow() - r.press_time) / total, 0.03, 0.9);
}

function renderCard(r) {
  const done = r.status === "done";
  const thumb = h("div", { class: "thumb" });
  if (r.thumb_url) thumb.append(h("img", { src: r.thumb_url, alt: "", loading: "lazy" }));
  else if (r.status === "error") thumb.append(h("span", { class: "x", text: "erro" }));
  else thumb.append(h("div", { class: "spin" }));
  if (done && r.duration) thumb.append(h("span", { class: "len", text: `${Math.round(r.duration)} s` }));

  const info = h("div", {},
    h("div", { class: "c-time", text: fmtClock(r.press_time) }),
    h("div", { class: "c-meta", text: `${fmtAgo(r.press_time)}${r.presses > 1 ? ` · botão ${r.presses}×` : ""}` }),
  );
  if (!done) info.append(h("span", { class: `c-badge ${r.status}`, text: r.status_label }));
  if (r.status === "waiting_recording" || r.status === "processing") {
    const bar = h("i"); bar.style.width = `${progressOf(r) * 100}%`;
    info.append(h("div", { class: "mini-bar" }, bar));
  }
  if (r.status === "error") info.append(h("div", { class: "c-msg", text: r.error || "Falha ao gerar o replay." }));
  for (const w of r.warnings || []) info.append(h("div", { class: "c-warn", text: `⚠ ${w}` }));

  const btn = h("button", {
    class: `card${state.selected && state.selected.id === r.id ? " active" : ""}${state.known.has(r.id) || state.firstLoad ? "" : " fresh"}`,
    type: "button", "data-id": r.id, "aria-disabled": done ? null : "true",
    "aria-label": `Replay das ${fmtClock(r.press_time)}, ${r.status_label}`,
    onclick: () => { if (done) { openReplay(r, { autoplay: true }); scrollToPlayer(); } },
  }, thumb, info);
  return h("li", {}, btn);
}

let lastListJson = "";
async function refreshReplays(manual = false) {
  try {
    const data = await api("/api/replays?limit=40");
    state.serverOffset = data.server_time - Date.now() / 1000;
    $("listError").hidden = true;
    const list = data.replays;
    state.replays = list;
    state.byId = new Map(list.map((r) => [r.id, r]));
    const json = JSON.stringify(list) + Math.floor(Date.now() / 30000); // atualiza "há X min"
    const pending = list.filter((r) => r.status === "waiting_recording" || r.status === "processing");
    if (json !== lastListJson || pending.length) {
      lastListJson = json;
      $("cards").replaceChildren(...list.map(renderCard));
      $("listEmpty").hidden = list.length > 0;
    }
    handleNewReplays(list, pending);
    list.forEach((r) => state.known.add(r.id));
    if (state.firstLoad) {
      state.firstLoad = false;
      const latest = list.find((r) => r.status === "done");
      if (latest && !state.selected) openReplay(latest, { autoplay: false });
    }
    // replay aberto foi apagado pela retenção
    if (state.selected && !state.byId.has(state.selected.id)) showEmpty();
    if (manual) toast("Lista atualizada");
  } catch (e) {
    $("listError").textContent = e.message;
    $("listError").hidden = false;
  }
}

function handleNewReplays(list, pending) {
  // faixa "lance marcado"
  const cur = pending[0];
  $("now").hidden = !cur;
  if (cur) {
    const left = Math.max(0, Math.ceil(cur.press_time + cur.post_seconds + 1.5 - serverNow()));
    $("nowTitle").textContent = `Lance marcado às ${fmtClock(cur.press_time)}!`;
    $("nowSub").textContent = cur.status === "processing" ? "Montando o vídeo…" : left > 0 ? `Replay pronto em ~${left} s` : "Quase pronto…";
    $("nowBar").style.width = `${progressOf(cur) * 100}%`;
  }
  for (const r of pending) if (!state.firstLoad) state.pendingSeen.add(r.id);
  // replays que acabaram de ficar prontos
  const ready = list.filter((r) => r.status === "done" && (state.pendingSeen.has(r.id) || (!state.firstLoad && !state.known.has(r.id))));
  if (!ready.length) return;
  ready.forEach((r) => state.pendingSeen.delete(r.id));
  const r = ready[0];
  if (navigator.vibrate) navigator.vibrate([60, 40, 60]);
  const editing = Date.now() - state.lastEdit < AUTO_OPEN_IDLE_MS && state.selected;
  if (editing) {
    toast(`Replay das ${fmtClock(r.press_time)} pronto`, { action: { label: "Ver agora", run: () => { openReplay(r, { autoplay: true, fresh: true }); scrollToPlayer(); } }, ms: 8000 });
  } else {
    openReplay(r, { autoplay: true, fresh: true });
    toast("Replay pronto! Toque em Baixar para salvar.");
  }
}

// ------------------------------------------------------------------ player
const video = $("video");

function showEmpty() {
  state.selected = null;
  video.removeAttribute("src"); video.load();
  $("player").hidden = true;
  $("empty").hidden = false;
}

function openReplay(r, { autoplay = false, fresh = false } = {}) {
  const same = state.selected && state.selected.id === r.id;
  state.selected = r;
  state.fps = r.fps || 30;
  $("empty").hidden = true;
  $("player").hidden = false;
  $("vError").hidden = true;
  $("vTitle").textContent = `Lance das ${fmtClock(r.press_time)}`;
  $("vSub").textContent = `${fmtDay(r.press_time)} · ${Math.round(r.pre_seconds)} s antes e ${Math.round(r.post_seconds)} s depois do botão${r.size_bytes ? ` · ${fmtSize(r.size_bytes)}` : ""}`;
  $("vNew").hidden = !fresh;
  $("dlAll").href = r.download_url;
  $("dlAll").textContent = r.duration ? `Baixar vídeo inteiro (${Math.round(r.duration)} s)` : "Baixar vídeo inteiro";
  $("tlStrip").style.backgroundImage = r.filmstrip_url ? `url("${r.filmstrip_url}")` : "none";
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("active", c.dataset.id === r.id));
  if (fresh) { const b = $("dlSel"); b.classList.remove("pulse"); void b.offsetWidth; b.classList.add("pulse"); }
  if (same) { if (autoplay) play(); return; }

  state.duration = r.duration || 0;
  setSelection(0, state.duration, false);
  video.src = r.video_url;
  video.playbackRate = state.speeds[state.speedIdx];
  video.load();
  if (autoplay) video.addEventListener("loadeddata", () => play(), { once: true });
  layoutTimeline();
}

video.addEventListener("loadedmetadata", () => {
  if (isFinite(video.duration) && video.duration > 0) {
    const whole = state.sel.start <= 0.01 && Math.abs(state.sel.end - state.duration) < 0.05;
    state.duration = video.duration;
    if (whole || state.sel.end > state.duration) setSelection(0, state.duration, false);
    layoutTimeline();
  }
});
video.addEventListener("error", () => { if (state.selected) $("vError").hidden = false; });
video.addEventListener("play", () => { $("videoBox").classList.add("playing"); $("player").classList.add("playing-ui"); $("cPlay").setAttribute("aria-label", "Pausar"); tick(); });
video.addEventListener("pause", () => { $("videoBox").classList.remove("playing"); $("player").classList.remove("playing-ui"); $("cPlay").setAttribute("aria-label", "Reproduzir"); layoutTimeline(); });
video.addEventListener("seeked", layoutTimeline);

function play() {
  const { start, end } = state.sel;
  if ($("loopSel").checked && (video.currentTime < start - 0.05 || video.currentTime >= end - 0.05)) video.currentTime = start;
  video.play().catch(() => {});
}
function togglePlay() { video.paused ? play() : video.pause(); }
function seek(t) {
  video.pause();
  video.currentTime = clamp(t, 0, Math.max(0, state.duration - 0.001));
  layoutTimeline();
}
function tick() {
  if (video.paused) return;
  if ($("loopSel").checked && video.currentTime >= state.sel.end) video.currentTime = state.sel.start;
  layoutTimeline();
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------------ linha do tempo / recorte
const tl = $("tl");
const pct = (t) => (state.duration > 0 ? (t / state.duration) * 100 : 0);

function setSelection(start, end, touched = true) {
  const D = state.duration || 0;
  start = clamp(start, 0, Math.max(0, D - MIN_SEL));
  end = clamp(end, Math.min(D, start + MIN_SEL), D);
  state.sel = { start: Math.round(start * 10) / 10, end: Math.round(end * 10) / 10 };
  if (D && state.sel.end > D) state.sel.end = D;
  if (touched) state.lastEdit = Date.now();
  layoutTimeline();
}

function layoutTimeline() {
  const { start, end } = state.sel, D = state.duration;
  const s = pct(start), e = pct(end);
  $("hStart").style.left = `${s}%`;
  $("hEnd").style.left = `${e}%`;
  $("tlSel").style.left = `${s}%`; $("tlSel").style.width = `${e - s}%`;
  $("shadeL").style.width = `${s}%`;
  $("shadeR").style.width = `${100 - e}%`;
  $("tlHead").style.left = `${pct(video.currentTime || 0)}%`;
  const r = state.selected;
  const press = r && r.press_offset != null ? r.press_offset : null;
  $("tlPress").hidden = press == null;
  if (press != null) $("tlPress").style.left = `${pct(press)}%`;
  $("tStart").textContent = fmtT(start);
  $("tEnd").textContent = fmtT(end);
  $("tDur").textContent = fmtSec(Math.max(0, end - start));
  const whole = isWhole();
  $("dlSelTxt").textContent = whole ? `Baixar vídeo (${fmtSec(D || 0)})` : `Baixar trecho (${fmtSec(end - start)})`;
  for (const [id, v, label] of [["hStart", start, "Início"], ["hEnd", end, "Fim"]]) {
    const el = $(id);
    el.setAttribute("aria-valuemin", "0");
    el.setAttribute("aria-valuemax", D.toFixed(1));
    el.setAttribute("aria-valuenow", v.toFixed(1));
    el.setAttribute("aria-valuetext", `${label} em ${fmtSec(v)}`);
  }
  document.querySelectorAll(".chip[data-preset]").forEach((c) => c.classList.toggle("on", presetRange(c.dataset.preset).join() === [start, end].join()));
}
const isWhole = () => state.sel.start <= 0.05 && state.sel.end >= state.duration - 0.05;

function timeAt(clientX) {
  const r = tl.getBoundingClientRect();
  return clamp((clientX - r.left) / r.width, 0, 1) * state.duration;
}

let drag = null;
tl.addEventListener("pointerdown", (e) => {
  if (!state.duration) return;
  const handle = e.target.closest(".handle");
  drag = handle ? (handle.id === "hStart" ? "start" : "end") : "head";
  if (handle) handle.classList.add("drag");
  tl.setPointerCapture(e.pointerId);
  moveDrag(e);
  e.preventDefault();
});
tl.addEventListener("pointermove", (e) => { if (drag) moveDrag(e); });
const endDrag = () => { document.querySelectorAll(".handle.drag").forEach((x) => x.classList.remove("drag")); drag = null; };
tl.addEventListener("pointerup", endDrag);
tl.addEventListener("pointercancel", endDrag);

function moveDrag(e) {
  const t = timeAt(e.clientX);
  if (drag === "start") { setSelection(Math.min(t, state.sel.end - MIN_SEL), state.sel.end); seek(state.sel.start); }
  else if (drag === "end") { setSelection(state.sel.start, Math.max(t, state.sel.start + MIN_SEL)); seek(state.sel.end); }
  else { state.lastEdit = Date.now(); seek(t); }
}

// teclado nas alças: setas = 0,1 s; Shift = 1 s
for (const id of ["hStart", "hEnd"]) {
  $(id).addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 1 : 0.1;
    const dir = e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : 0;
    if (!dir) return;
    e.preventDefault(); e.stopPropagation();
    if (id === "hStart") { setSelection(Math.min(state.sel.start + dir * step, state.sel.end - MIN_SEL), state.sel.end); seek(state.sel.start); }
    else { setSelection(state.sel.start, Math.max(state.sel.end + dir * step, state.sel.start + MIN_SEL)); seek(state.sel.end); }
  });
}

function presetRange(name) {
  const D = state.duration, r = state.selected;
  const p = r && r.press_offset != null ? r.press_offset : D * 2 / 3;
  const round = (v) => Math.round(v * 10) / 10;
  if (name === "play") return [round(clamp(p - 6, 0, D)), round(clamp(p + 2, 0, D))];
  if (name === "before") return [round(clamp(p - 5, 0, D)), round(clamp(p, 0, D))];
  return [0, round(D)];
}
document.querySelectorAll(".chip[data-preset]").forEach((c) => c.addEventListener("click", () => {
  const [a, b] = presetRange(c.dataset.preset);
  setSelection(a, b);
  video.currentTime = state.sel.start;
  play();
}));
$("mIn").addEventListener("click", () => { setSelection(Math.min(video.currentTime, state.sel.end - MIN_SEL), state.sel.end); toast(`Início em ${fmtT(state.sel.start)}`, { ms: 1500 }); });
$("mOut").addEventListener("click", () => { setSelection(state.sel.start, Math.max(video.currentTime, state.sel.start + MIN_SEL)); toast(`Fim em ${fmtT(state.sel.end)}`, { ms: 1500 }); });

// ------------------------------------------------------------------ controles
const frame = () => 1 / (state.fps || 30);
$("cPlay").addEventListener("click", togglePlay);
$("bigPlay").addEventListener("click", play);
video.addEventListener("click", togglePlay);
$("cBack").addEventListener("click", () => { state.lastEdit = Date.now(); seek(video.currentTime - 1); });
$("cFwd").addEventListener("click", () => { state.lastEdit = Date.now(); seek(video.currentTime + 1); });
$("cPrevF").addEventListener("click", () => { state.lastEdit = Date.now(); seek(video.currentTime - frame()); });
$("cNextF").addEventListener("click", () => { state.lastEdit = Date.now(); seek(video.currentTime + frame()); });
$("cSpeed").addEventListener("click", () => {
  state.speedIdx = (state.speedIdx + 1) % state.speeds.length;
  const sp = state.speeds[state.speedIdx];
  video.playbackRate = sp;
  const lbl = `${String(sp).replace(".", ",")}×`;
  $("speedLbl").textContent = lbl;
  $("speedBadge").textContent = lbl;
  $("speedBadge").hidden = sp === 1;
});
$("cFull").addEventListener("click", () => {
  const box = $("videoBox");
  if (document.fullscreenElement) document.exitFullscreen();
  else if (box.requestFullscreen) box.requestFullscreen().catch(() => video.webkitEnterFullscreen && video.webkitEnterFullscreen());
  else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
});

document.addEventListener("keydown", (e) => {
  if (!state.selected || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, .handle")) return;
  const k = e.key;
  if (k === " " || k === "k") { e.preventDefault(); togglePlay(); }
  else if (k === "ArrowLeft") { e.preventDefault(); seek(video.currentTime - 1); }
  else if (k === "ArrowRight") { e.preventDefault(); seek(video.currentTime + 1); }
  else if (k === ",") seek(video.currentTime - frame());
  else if (k === ".") seek(video.currentTime + frame());
  else if (k === "i" || k === "I") $("mIn").click();
  else if (k === "o" || k === "O") $("mOut").click();
  else if (k === "d" || k === "D") $("dlSel").click();
});

// ------------------------------------------------------------------ downloads
async function exportSelection() {
  const r = state.selected;
  if (isWhole()) return { url: r.download_url, duration: state.duration };
  return api(r.export_url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start: state.sel.start, end: state.sel.end }), timeout: 90000,
  });
}

$("dlSel").addEventListener("click", async () => {
  if (!state.selected) return;
  const btn = $("dlSel"), txt = $("dlSelTxt"), old = txt.textContent;
  btn.disabled = true; btn.classList.remove("pulse");
  txt.textContent = isWhole() ? "Baixando…" : "Preparando o trecho…";
  try {
    const res = await exportSelection();
    download(res.url);
    toast(isIOS ? "Vídeo enviado para o app Arquivos" : "Download iniciado");
  } catch (e) {
    toast(e.message, { error: true, ms: 5000 });
  } finally {
    btn.disabled = false; txt.textContent = old; layoutTimeline();
  }
});

// Compartilhar direto (WhatsApp, galeria): só disponível em contexto seguro (ex.: localhost/HTTPS)
if (window.isSecureContext && navigator.canShare) {
  $("shareBtn").hidden = false;
  $("shareBtn").addEventListener("click", async () => {
    try {
      const res = await exportSelection();
      const blob = await (await fetch(res.url)).blob();
      const file = new File([blob], "arena-replay.mp4", { type: "video/mp4" });
      if (!navigator.canShare({ files: [file] })) throw new Error("Este navegador não compartilha vídeos. Use Baixar.");
      await navigator.share({ files: [file], title: "Arena Replay" });
    } catch (e) {
      if (e.name !== "AbortError") toast(e.message, { error: true });
    }
  });
}
$("iosHint").hidden = !isIOS;

// ------------------------------------------------------------------ painéis
let openSheet = null, snapTimer = 0;
function show(id) {
  close();
  openSheet = $(id);
  openSheet.hidden = false;
  $("backdrop").hidden = false;
  const first = openSheet.querySelector("[data-close]");
  if (first) first.focus();
  if (id === "sheetStatus") { refreshStatus(); refreshSnapshot(); snapTimer = setInterval(refreshSnapshot, 2000); }
  if (id === "dlgQr") loadQr();
}
function close() {
  if (!openSheet) return;
  openSheet.hidden = true; $("backdrop").hidden = true; openSheet = null;
  clearInterval(snapTimer);
}
$("btnStatus").addEventListener("click", () => show("sheetStatus"));
$("btnQr").addEventListener("click", () => show("dlgQr"));
$("backdrop").addEventListener("click", close);
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

async function loadQr() {
  $("qrImg").src = `/api/access/qr.svg?t=${Date.now()}`;
  try { const a = await api("/api/access"); $("qrUrl").textContent = a.urls[0]; } catch { $("qrUrl").textContent = location.origin; }
}
$("copyUrl").addEventListener("click", async () => {
  const url = $("qrUrl").textContent;
  try { await navigator.clipboard.writeText(url); toast("Endereço copiado"); }
  catch { const r = document.createRange(); r.selectNodeContents($("qrUrl")); getSelection().removeAllRanges(); getSelection().addRange(r); toast("Endereço selecionado"); }
});

async function testPress(btn) {
  btn.disabled = true;
  try {
    const r = await api("/api/events/test", { method: "POST" });
    toast(r.status === "merged" ? "Toque agrupado ao lance anterior" : "Lance marcado! Gerando o replay…");
    close();
    refreshReplays();
  } catch (e) { toast(e.message, { error: true }); }
  finally { setTimeout(() => (btn.disabled = false), 1000); }
}
$("btnTest").addEventListener("click", (e) => testPress(e.currentTarget));
$("btnTestEmpty").addEventListener("click", (e) => testPress(e.currentTarget));
$("btnRefresh").addEventListener("click", () => { lastListJson = ""; refreshReplays(true); });

function scrollToPlayer() {
  if (innerWidth < 1024) $("viewer").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ ciclo
addEventListener("resize", layoutTimeline);
function loop() { if (!document.hidden) { refreshReplays(); } }
let statusTick = 0;
setInterval(() => { loop(); if (!document.hidden && ++statusTick % 2 === 0) refreshStatus(); }, POLL_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { refreshStatus(); refreshReplays(); } });
refreshStatus();
refreshReplays();
