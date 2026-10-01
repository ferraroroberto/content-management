// Podcast recorder page (issue #341): record this side's camera and mic at full
// resolution, keep every chunk in IndexedDB until the server has it, upload
// while recording, resume after a dropped connection or a reload.
"use strict";

const TOKEN = location.pathname.split("/").filter(Boolean).pop();
const API = `/api/${TOKEN}`;
const PROFILES = {
  "1080": { width: 1920, height: 1080, bps: 12_000_000 },
  "2160": { width: 3840, height: 2160, bps: 40_000_000 },
};
// H.264 in WebM first. Chrome's MP4 recorder crashes the tab once a recording
// passes 4 GiB (#341: reproduced at 2^32 bytes, ~46 min at 1080p, ~14 min at
// 4K); its WebM muxer has no such limit. MP4 stays as the fallback for browsers
// that cannot record WebM (Safari). The server remuxes either one to .mp4.
const MIMES = [
  ["video/webm;codecs=h264,opus", "webm"],
  ["video/webm;codecs=vp9,opus", "webm"],
  ["video/webm", "webm"],
  ["video/mp4;codecs=avc1.640033,mp4a.40.2", "mp4"],
  ["video/mp4;codecs=avc1,mp4a.40.2", "mp4"],
  ["video/mp4", "mp4"],
];
const TIMESLICE_MS = 1000;
const MAX_BACKOFF_MS = 5000;
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mb = (bytes) => `${(bytes / 1e6).toFixed(1)} MB`;

// ── IndexedDB ─────────────────────────────────────────────────────────────
let db;
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("podcast-recorder", 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore("chunks", { keyPath: ["rid", "seq"] });
      req.result.createObjectStore("recs", { keyPath: "rid" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
function tx(store, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    let out;
    if (req) req.onsuccess = () => { out = req.result; };
    t.oncomplete = () => resolve(out);
    t.onerror = t.onabort = () => reject(t.error);
  });
}
const range = (rid) => IDBKeyRange.bound([rid, 0], [rid, Infinity]);
const putChunk = (rid, seq, blob) => tx("chunks", "readwrite", (s) => s.put({ rid, seq, blob }));
const deleteChunk = (rid, seq) => tx("chunks", "readwrite", (s) => s.delete([rid, seq]));
const firstChunk = (rid) => tx("chunks", "readonly", (s) => s.get(range(rid)));
const chunkKeys = (rid) => tx("chunks", "readonly", (s) => s.getAllKeys(range(rid)));
const putRec = (rec) => tx("recs", "readwrite", (s) => s.put({ ...rec }));
const myRecs = async () => (await tx("recs", "readonly", (s) => s.getAll())).filter((r) => r.token === TOKEN);
function pendingBytes(rid) {
  return new Promise((resolve, reject) => {
    let total = 0;
    const req = db.transaction("chunks").objectStore("chunks").openCursor(range(rid));
    req.onsuccess = () => {
      const cur = req.result;
      if (!cur) return resolve(total);
      total += cur.value.blob.size;
      cur.continue();
    };
    req.onerror = () => reject(req.error);
  });
}

// ── server calls ─────────────────────────────────────────────────────────
class Fatal extends Error {}
async function call(path, options) {
  const res = await fetch(`${API}${path}`, options);
  const body = await res.json().catch(() => ({}));
  if (res.ok) return body;
  const message = body.error || `server answered ${res.status}`;
  if (res.status === 409) return { conflict: body };
  if (res.status >= 400 && res.status < 500) throw new Fatal(message);
  throw new Error(message);
}

async function reconcile(rec) {
  const { have, finished } = await call(`/rec/${rec.rid}/have`);
  const held = new Set(have);
  for (const [rid, seq] of await chunkKeys(rec.rid)) if (held.has(seq)) await deleteChunk(rid, seq);
  if (finished && !rec.result) { rec.result = finished; await putRec(rec); }
  return have;
}

// ── upload loop: one chunk at a time, oldest first, retried until it lands ──
let uploading = false;
let online = true;
let lastError = "";

async function retrying(rec, fn) {
  let wait = 1000;
  for (;;) {
    try {
      const out = await fn();
      online = true; lastError = "";
      return out;
    } catch (err) {
      if (err instanceof Fatal) throw err;
      online = false; lastError = err.message;
      await sleep(wait);
      wait = Math.min(wait * 2, MAX_BACKOFF_MS);
      await reconcile(rec).catch(() => {});
    }
  }
}

async function finish(rec) {
  const out = await call(`/rec/${rec.rid}/finish`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ total: rec.total, ext: rec.ext }),
  });
  if (out.conflict) {
    const missing = out.conflict.missing || [];
    const local = new Set((await chunkKeys(rec.rid)).map(([, seq]) => seq));
    if (!missing.every((seq) => local.has(seq))) throw new Fatal(`the server is missing ${missing.length} chunk(s) this page no longer has`);
    return; // they are still queued here: the loop uploads them first
  }
  rec.result = out;
  await putRec(rec);
}

async function kick() {
  if (uploading) return;
  uploading = true;
  try {
    for (;;) {
      let worked = false;
      for (const rec of (await myRecs()).filter((r) => !r.result && !r.error)) {
        try {
          const chunk = await firstChunk(rec.rid);
          if (chunk) {
            await retrying(rec, async () => {
              await call(`/rec/${rec.rid}/chunk/${chunk.seq}`, { method: "PUT", body: chunk.blob });
              await deleteChunk(rec.rid, chunk.seq);
            });
          } else if (rec.stopped) {
            await retrying(rec, () => finish(rec));
          } else {
            continue; // still recording, nothing queued yet
          }
        } catch (err) {
          rec.error = err.message;
          await putRec(rec);
        }
        worked = true;
        break;
      }
      if (!worked) break;
    }
  } finally {
    uploading = false;
  }
}

// ── capture ──────────────────────────────────────────────────────────────
let stream = null;
let recorder = null;
let current = null;
let wakeLock = null;

const resolution = () => document.querySelector("input[name=res]:checked").value;

async function openStream() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  const p = PROFILES[resolution()];
  const cam = $("camera").value, mic = $("mic").value;
  stream = await navigator.mediaDevices.getUserMedia({
    video: { deviceId: cam ? { exact: cam } : undefined, width: { ideal: p.width }, height: { ideal: p.height },
             frameRate: { ideal: 30 } },
    audio: { deviceId: mic ? { exact: mic } : undefined, echoCancellation: true, noiseSuppression: false,
             autoGainControl: false, sampleRate: { ideal: 48000 } },
  });
  $("preview").srcObject = stream;
  const v = stream.getVideoTracks()[0].getSettings();
  const [mime] = pickMime();
  $("settings").textContent = `Camera gives ${v.width}×${v.height} at ${Math.round(v.frameRate || 0)} fps · `
    + `${mime || "no supported recording format"}`;
  $("start").disabled = !mime;
}

async function listDevices() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  for (const [kind, id] of [["videoinput", "camera"], ["audioinput", "mic"]]) {
    const select = $(id), keep = select.value;
    select.replaceChildren(...devices.filter((d) => d.kind === kind).map((d, i) => {
      const o = document.createElement("option");
      o.value = d.deviceId; o.textContent = d.label || `${id} ${i + 1}`;
      return o;
    }));
    if (keep) select.value = keep;
  }
}

function pickMime() {
  return MIMES.find(([m]) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || [];
}

async function start() {
  const [mime, ext] = pickMime();
  const p = PROFILES[resolution()];
  const rec = { rid: crypto.randomUUID(), token: TOKEN, mime, ext, started: Date.now(), recorded: 0,
                total: 0, stopped: false, result: null, error: null };
  await putRec(rec);
  current = rec;
  let chain = Promise.resolve();
  recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: p.bps, audioBitsPerSecond: 192000 });
  recorder.ondataavailable = (e) => {
    if (!e.data.size) return;
    const seq = rec.total++;
    rec.recorded += e.data.size;
    // Chained, so chunk writes and the final "stopped" land in order.
    chain = chain.then(async () => { await putChunk(rec.rid, seq, e.data); await putRec(rec); kick(); });
  };
  recorder.onstop = () => {
    chain = chain.then(async () => { rec.stopped = true; await putRec(rec); current = null; kick(); });
    releaseWakeLock();
    setControls();
  };
  recorder.onerror = (e) => warn(`recording error: ${e.error ? e.error.name : "unknown"}; what was recorded is kept`);
  // A device that disconnects ends its track, and the recorder stops with it: say so.
  for (const track of stream.getTracks()) {
    track.addEventListener("ended", () => {
      if (current === rec) warn(`the ${track.kind === "video" ? "camera" : "microphone"} stopped (disconnected or `
        + "taken by another app); the recording ended there and is being uploaded");
    });
  }
  recorder.start(TIMESLICE_MS);
  try { wakeLock = await navigator.wakeLock.request("screen"); } catch { wakeLock = null; }
  setControls();
}

function stop() {
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

function warn(message) {
  console.warn(`[recorder] ${message}`);
  $("settings").textContent = message;
  $("settings").className = "meta err";
}

function releaseWakeLock() {
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
}

// A recording the tab never stopped (crash, closed tab): close it at the last chunk either side has.
async function adoptOrphans() {
  for (const rec of (await myRecs()).filter((r) => !r.stopped && !r.result)) {
    const local = (await chunkKeys(rec.rid)).map(([, seq]) => seq);
    const remote = await reconcile(rec).catch(() => []);
    rec.total = Math.max(-1, ...local, ...remote) + 1;
    rec.stopped = true;
    await putRec(rec);
  }
}

// ── UI ───────────────────────────────────────────────────────────────────
function setControls() {
  const recording = recorder && recorder.state === "recording";
  $("start").disabled = recording || !stream;
  $("stop").disabled = !recording;
  for (const id of ["camera", "mic", "res1080", "res2160"]) $(id).disabled = recording;
}

async function render() {
  const recs = await myRecs();
  let recorded = 0, pending = 0;
  const lines = [], done = [];
  for (const rec of recs) {
    if (rec.result) {
      if (rec.started > Date.now() - 7 * 864e5) {
        done.push(`✅ ${new Date(rec.started).toLocaleString()}: uploaded, ${Math.round(rec.result.duration_s / 60)} min`);
      }
      continue;
    }
    const waiting = await pendingBytes(rec.rid);
    recorded += rec.recorded; pending += waiting;
    if (rec.error) lines.push(`⚠️ ${rec.error}`);
  }
  const recording = recorder && recorder.state === "recording";
  $("state").textContent = recording ? "● recording" : pending ? "uploading" : "idle";
  $("state").className = `badge ${recording ? "rec" : pending ? "warn" : ""}`;
  $("bar").value = recorded ? (recorded - pending) / recorded : 0;
  $("upload").textContent = recorded
    ? `${mb(recorded - pending)} of ${mb(recorded)} uploaded${pending ? `, ${mb(pending)} still to send` : ", all sent"}`
      + (lines.length ? ` · ${lines.join(" · ")}` : "")
    : "Nothing recorded yet.";
  $("done").textContent = done.join(" · ");
  $("net").textContent = online ? "" : `connection lost, retrying (${lastError})`;
  $("net").className = online ? "meta" : "meta err";
  window.__recorder = { recording: !!recording, recorded, pending, results: recs.filter((r) => r.result).map((r) => r.result),
                        errors: recs.filter((r) => r.error).map((r) => r.error) };
}

async function main() {
  db = await openDb();
  try {
    const { side } = await call("");
    $("intro").textContent = `You are recording the ${side} side. Pick your camera and microphone, `
      + "then start when the conversation starts. Your recording stays on this computer until it is uploaded.";
  } catch (err) {
    $("intro").textContent = `This link does not work (${err.message}).`;
    $("intro").className = "err";
    return;
  }
  try {
    await openStream();
    await listDevices();
  } catch (err) {
    $("settings").textContent = `Camera or microphone not available: ${err.message}`;
    $("settings").className = "meta err";
  }
  for (const id of ["camera", "mic", "res1080", "res2160"]) $(id).addEventListener("change", () => openStream().catch(
    (err) => { $("settings").textContent = `Camera or microphone not available: ${err.message}`; }));
  $("start").addEventListener("click", start);
  $("stop").addEventListener("click", stop);
  window.addEventListener("online", kick);
  window.addEventListener("beforeunload", (e) => {
    if ((recorder && recorder.state === "recording") || $("state").textContent === "uploading") e.preventDefault();
  });
  await adoptOrphans();
  setControls();
  kick();
  setInterval(() => { render().catch(() => {}); kick(); }, 1000);
}

main();
