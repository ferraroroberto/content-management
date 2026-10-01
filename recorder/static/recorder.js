// Podcast recorder page (issue #341): record this side's camera and mic at full
// resolution, keep every chunk in IndexedDB until the server has it, upload
// while recording, resume after a dropped connection or a reload.
// The call (#342): the same camera stream also feeds a peer-to-peer WebRTC
// call, downscaled; while recording, the other side's voice as received is
// recorded too, at low quality, as the sync reference (#343).
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
const CALL_HEIGHT = 720;           // the call's picture; the recording keeps full resolution
const CALL_BPS = 1_500_000;
const REF_MIME = "audio/webm;codecs=opus";
const REF_BPS = 32_000;
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
    body: JSON.stringify({ total: rec.total, ext: rec.ext, kind: rec.kind || "main", main: rec.main,
                           offset_s: rec.offset_s }),
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
let recorder = null;   // this side's camera + mic
let current = null;
let refRecorder = null; // the other side's voice, while recording
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
  attachToCall();
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

// One recording of ``source`` into IndexedDB chunks; ``onDone`` runs once it is
// stopped and queued. ``main`` (a main recording of this page) makes this a
// reference: it records where in that main recording it starts.
async function record(source, kind, mime, ext, options, onDone, main = null) {
  const rec = { rid: crypto.randomUUID(), token: TOKEN, kind, mime, ext, started: Date.now(), recorded: 0,
                total: 0, stopped: false, result: null, error: null };
  let chain = Promise.resolve();
  const mr = new MediaRecorder(source, { mimeType: mime, ...options });
  mr.ondataavailable = (e) => {
    if (!e.data.size) return;
    const seq = rec.total++;
    rec.recorded += e.data.size;
    // Chained, so chunk writes and the final "stopped" land in order.
    chain = chain.then(async () => { await putChunk(rec.rid, seq, e.data); await putRec(rec); kick(); });
  };
  mr.onstop = () => {
    chain = chain.then(async () => { rec.stopped = true; await putRec(rec); kick(); });
    onDone();
  };
  // Both recorders of this page are started the same way, so their start
  // times on one clock place the reference inside the main recording (#343).
  rec.t0 = performance.now();
  if (main) Object.assign(rec, { main: main.rid, offset_s: (rec.t0 - main.t0) / 1000 });
  mr.start(TIMESLICE_MS);
  await putRec(rec);
  return [mr, rec];
}

async function start() {
  const [mime, ext] = pickMime();
  const p = PROFILES[resolution()];
  const [mr, rec] = await record(stream, "main", mime, ext, { videoBitsPerSecond: p.bps, audioBitsPerSecond: 192000 },
    () => { current = null; stopRef(); releaseWakeLock(); setControls(); });
  recorder = mr;
  current = rec;
  recorder.onerror = (e) => warn(`recording error: ${e.error ? e.error.name : "unknown"}; what was recorded is kept`);
  // A device that disconnects ends its track, and the recorder stops with it: say so.
  for (const track of stream.getTracks()) {
    track.addEventListener("ended", () => {
      if (current === rec) warn(`the ${track.kind === "video" ? "camera" : "microphone"} stopped (disconnected or `
        + "taken by another app); the recording ended there and is being uploaded");
    });
  }
  startRef();
  try { wakeLock = await navigator.wakeLock.request("screen"); } catch { wakeLock = null; }
  setControls();
}

function stop() {
  if (recorder && recorder.state !== "inactive") recorder.stop();
}

// The other side's voice as received, while this side records. A call that
// drops ends the remote track and with it this recording; the next one starts
// when the call comes back, so a session can hold several reference files.
async function startRef() {
  const track = remote && remote.getAudioTracks().find((t) => t.readyState === "live");
  if (!current || refRecorder || !track || !MediaRecorder.isTypeSupported(REF_MIME)) return;
  refRecorder = "starting";
  const [mr] = await record(new MediaStream([track]), "ref", REF_MIME, "webm", { audioBitsPerSecond: REF_BPS },
    () => { refRecorder = null; }, current);
  refRecorder = mr;
}

function stopRef() {
  if (refRecorder && refRecorder !== "starting" && refRecorder.state !== "inactive") refRecorder.stop();
}

// ── call: peer to peer, signalling over the server's WebSocket ─────────────
let pc = null;
let ws = null;
let iceServers = [];
let remote = null;
let polite = false;      // perfect negotiation: the guest yields on an offer collision
let makingOffer = false;
let ignoreOffer = false;
let callState = "connecting";
let wsAlive = true;
let peerId = null;                     // the other page, as the server names it
const PAGE_ID = crypto.randomUUID();   // this page load

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

async function connectSignalling() {
  try {
    ({ iceServers } = await call("/ice"));
  } catch {
    setTimeout(connectSignalling, 3000);
    return;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${TOKEN}?peer=${PAGE_ID}`);
  ws.onmessage = (e) => onSignal(JSON.parse(e.data)).catch((err) => console.warn("[recorder] signal", err));
  ws.onclose = (e) => {
    if (e.code === 4001) { callState = "open in another tab"; wsAlive = false; return; }
    if (e.code === 4404) { callState = "link not valid"; wsAlive = false; return; }
    if (!pc || pc.connectionState !== "connected") callState = "reconnecting";
    setTimeout(connectSignalling, 2000);
  };
}

function startPeer() {
  pc = new RTCPeerConnection({ iceServers });
  pc.onicecandidate = ({ candidate }) => { if (candidate) send({ type: "candidate", candidate }); };
  pc.onnegotiationneeded = async () => {
    try {
      makingOffer = true;
      await pc.setLocalDescription();
      send({ type: pc.localDescription.type, sdp: pc.localDescription.sdp });
    } catch (err) {
      console.warn("[recorder] negotiation", err);
    } finally {
      makingOffer = false;
    }
  };
  pc.ontrack = ({ track, streams }) => {
    remote = streams[0] || new MediaStream([track]);
    $("remote").srcObject = remote;
    $("remote").play().catch(() => { $("hear").hidden = false; });
    if (track.kind === "audio") startRef();
  };
  pc.onconnectionstatechange = () => {
    callState = pc.connectionState;
    if (pc.connectionState === "failed") pc.restartIce();
  };
  attachToCall();
}

function closePeer() {
  if (pc) pc.close();
  pc = null;
  remote = null;
  $("remote").srcObject = null;
  stopRef();
}

// Send this side's camera and mic: the same tracks the recorder uses, the
// picture scaled down to CALL_HEIGHT for the call only.
function attachToCall() {
  if (!pc || !stream) return;
  for (const track of stream.getTracks()) {
    const sender = pc.getSenders().find((s) => s.track && s.track.kind === track.kind);
    if (sender) {
      sender.replaceTrack(track).then(() => track.kind === "video" && capVideo(sender));
    } else if (track.kind === "video") {
      pc.addTransceiver(track, { direction: "sendrecv", streams: [stream], sendEncodings: [videoEncoding(track)] });
    } else {
      pc.addTrack(track, stream);
    }
  }
}

function videoEncoding(track) {
  const height = track.getSettings().height || CALL_HEIGHT;
  return { scaleResolutionDownBy: Math.max(1, height / CALL_HEIGHT), maxBitrate: CALL_BPS };
}

async function capVideo(sender) {
  const params = sender.getParameters();
  if (!params.encodings || !params.encodings.length) return;
  Object.assign(params.encodings[0], videoEncoding(sender.track));
  await sender.setParameters(params).catch((err) => console.warn("[recorder] call cap", err));
}

async function onSignal(message) {
  if (message.type === "peer") {
    if (message.present && message.peer !== peerId) {
      // A new page on the other side (first join, reload, crash): start the call afresh.
      closePeer();
      peerId = message.peer;
      startPeer();
    } else if (!message.present) {
      // Its signalling dropped; a call whose media still flows carries on.
      if (!pc || pc.connectionState !== "connected") {
        closePeer();
        peerId = null;
        callState = "waiting for the other side";
      }
    }
    return;
  }
  if (!pc) startPeer();
  if (message.type === "offer" || message.type === "answer") {
    const collision = message.type === "offer" && (makingOffer || pc.signalingState !== "stable");
    ignoreOffer = !polite && collision;
    if (ignoreOffer) return;
    await pc.setRemoteDescription({ type: message.type, sdp: message.sdp });
    if (message.type === "offer") {
      await pc.setLocalDescription();
      send({ type: pc.localDescription.type, sdp: pc.localDescription.sdp });
    }
  } else if (message.type === "candidate") {
    try {
      await pc.addIceCandidate(message.candidate);
    } catch (err) {
      if (!ignoreOffer) console.warn("[recorder] candidate", err);
    }
  } else if (message.type === "bye") {
    closePeer();
    peerId = null;
    callState = "waiting for the other side";
  }
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
      if (rec.kind !== "ref" && rec.started > Date.now() - 7 * 864e5) {
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
  const live = callState === "connected";
  $("call").textContent = live ? "● connected" : callState;
  $("call").className = `badge ${live ? "ok" : wsAlive ? "warn" : ""}`;
  const finished = recs.filter((r) => r.result);
  window.__recorder = { recording: !!recording, recorded, pending, call: callState,
                        results: finished.filter((r) => r.kind !== "ref").map((r) => r.result),
                        refs: finished.filter((r) => r.kind === "ref").map((r) => r.result),
                        errors: recs.filter((r) => r.error).map((r) => r.error) };
}

async function main() {
  db = await openDb();
  try {
    const { side } = await call("");
    polite = side === "guest";
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
  $("hear").addEventListener("click", () => { $("remote").play(); $("hear").hidden = true; });
  connectSignalling();
  window.addEventListener("online", kick);
  window.addEventListener("beforeunload", (e) => {
    if ((recorder && recorder.state === "recording") || $("state").textContent === "uploading") e.preventDefault();
    send({ type: "bye" });
  });
  await adoptOrphans();
  setControls();
  kick();
  setInterval(() => { render().catch(() => {}); kick(); }, 1000);
}

main();
