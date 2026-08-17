import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { voice } from "./Blob.jsx";
import { targetBrief, targetIds } from "./guide.js";

/**
 * Depot radio: text briefing plus Gemini Live voice.
 * The key never leaves the API. This panel only speaks JSON over /api/assistant.
 *
 * The face of it is the blob; this is the transcript and the keyboard.
 */

const STARTERS = [
  "What am I looking at?",
  "Which truck is late?",
  "Walk me through the board",
];

/**
 * Mounted for the whole session, rendered only when the panel is open — so a
 * voice call started from the orb keeps running with the transcript closed.
 */
const Assistant = forwardRef(function Assistant(
  { open, aside, onClose, onLive, context, onAction },
  ref
) {
  const [status, setStatus] = useState(null);
  const [messages, setMessages] = useState(() => [
    {
      role: "officer",
      text: "Ask about a truck, the clock, or a red band — I point while I answer. Tap the orb to speak.",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState("off"); // off | connecting | on | talking | fallback
  const [err, setErr] = useState("");
  const scroller = useRef(null);
  const liveRef = useRef(null);
  const abortRef = useRef(null);
  const ctxRef = useRef(context);
  ctxRef.current = context;

  useEffect(() => {
    fetch("/api/assistant/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setStatus({ configured: false }));
  }, []);

  useEffect(() => {
    scroller.current?.scrollTo(0, scroller.current.scrollHeight);
  }, [messages, open]);

  useEffect(() => {
    if (!open) return;
    document.querySelector(".ask-form input")?.focus();
  }, [open]);

  // The blob is the officer's face: it should look like whatever the officer
  // is actually doing.
  useEffect(() => {
    voice.state =
      live === "talking" ? "speaking"
      : live === "on" ? "listening"
      : live === "connecting" ? "thinking"
      : busy ? "thinking"
      : err ? "error"
      : "idle";
  }, [live, busy, err]);

  useEffect(() => () => {
    liveRef.current?.stop();
    voice.state = "idle";
  }, []);

  const payload = (msg) => {
    const c = ctxRef.current || {};
    // Lean board: ids + status only — lat/lon and full windows bloat every turn.
    return {
      message: msg,
      view: c.view,
      selected: c.selected,
      decision: c.decision,
      kpis: c.kpis,
      vehicles: (c.vehicles || []).slice(0, 14).map((v) => ({
        id: v.id, code: v.code, status: v.status,
        vehicle_type: v.vehicle_type || v.kind,
      })),
      shipments: (c.shipments || []).slice(0, 24).map((s) => ({
        id: s.id, code: s.code, status: s.status, priority: s.priority,
        demand_kg: s.demand_kg, late_min: s.late_min,
      })),
      overlays: (c.overlays || []).slice(0, 12).map((o) => ({
        id: o.id, name: o.name, active: o.active, kind: o.kind,
      })),
      targets: targetIds(),
      target_brief: targetBrief(),
    };
  };

  const markTried = () => {
    try { localStorage.setItem("sr.askCoach", "1"); } catch { /* */ }
  };

  const send = async (text) => {
    const msg = (text || draft).trim();
    if (!msg || busy) return;
    markTried();
    setDraft("");
    setMessages((m) => [...m, { role: "you", text: msg }]);
    setBusy(true);
    setErr("");
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    // One empty officer line, filled in as the answer streams into it.
    let slot = -1;
    setMessages((m) => {
      slot = m.length;
      return [...m, { role: "officer", text: "", pending: true }];
    });
    const write = (fn) =>
      setMessages((m) => m.map((x, i) => (i === slot ? fn(x) : x)));

    // Coalesce token paints so the log does not re-render on every SSE byte.
    let pending = "";
    let flushTimer = 0;
    const flushDelta = () => {
      flushTimer = 0;
      if (!pending) return;
      const chunk = pending;
      pending = "";
      write((x) => ({ ...x, text: x.text + chunk, pending: false }));
    };

    try {
      const res = await fetch("/api/assistant/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload(msg)),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) throw new Error(`briefing failed (${res.status})`);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let got = false;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (ev.type === "delta") {
            got = true;
            pending += ev.text;
            if (!flushTimer) flushTimer = window.setTimeout(flushDelta, 40);
          } else if (ev.type === "action") {
            onAction?.(ev);
          } else if (ev.type === "error") {
            setErr(ev.message);
            write((x) => ({ ...x, text: x.text || ev.message, pending: false, bad: !x.text }));
            got = true;
          }
        }
      }
      flushDelta();
      if (!got) write((x) => ({ ...x, text: "No reply came back.", pending: false, bad: true }));
    } catch (e) {
      if (e?.name === "AbortError") return;
      const m = String(e.message || e);
      setErr(m);
      write((x) => ({ ...x, text: x.text || m, pending: false, bad: !x.text }));
    } finally {
      clearTimeout(flushTimer);
      setBusy(false);
    }
  };

  const toggleLive = async () => {
    if (live === "on" || live === "connecting" || live === "talking") {
      liveRef.current?.stop();
      liveRef.current = null;
      setLive("off");
      return;
    }
    markTried();
    setErr("");
    setLive("connecting");
    try {
      await startLive(liveRef, () => payload(""), {
        onReady: () => setLive("on"),
        onTalking: (v) => setLive((s) => (s === "off" ? s : v ? "talking" : "on")),
        onHeard: (text) =>
          setMessages((m) => joinTurn(m, "you", text)),
        onSaid: (text) =>
          setMessages((m) => joinTurn(m, "officer", text)),
        onAction,
        onReconnecting: () => setLive("connecting"),
        onError: (message) => {
          setErr(message);
          setLive("fallback");
        },
        onClose: () => setLive("off"),
      });
    } catch (e) {
      setErr(String(e.message || e));
      setLive("fallback");
    }
  };

  // The orb drives the microphone directly, so it needs a way in that does not
  // involve the panel being on screen.
  useImperativeHandle(ref, () => ({ toggleLive, live }), [live]);
  useEffect(() => { onLive?.(live); }, [live, onLive]);

  if (!open) return null;

  const configured = status?.configured !== false;
  const liveLabel =
    live === "connecting" ? "Connecting…"
    : live === "talking" ? "Speaking"
    : live === "on" ? "Listening — tap to stop"
    : live === "fallback" ? "Voice dropped — type instead"
    : "Voice";

  return (
    <aside className={`ask${aside ? " aside" : ""}`} aria-label="Shift briefing">
      <header className="ask-head">
        <div>
          <b>Ask</b>
          <span>Points at the board · does not drive trucks</span>
        </div>
        <button className="insp-close" onClick={onClose} aria-label="Close briefing">✕</button>
      </header>

      {!configured && (
        <p className="ask-warn">
          No Gemini key on the API. Add GEMINI_API_KEY to apps/api/.env and restart.
        </p>
      )}
      {err && <p className="ask-warn">{err}</p>}

      <div className="ask-log" ref={scroller}>
        {messages.map((m, i) => (
          <div key={i} className={`ask-msg ${m.role}${m.bad ? " bad" : ""}`}>
            <b>{m.role === "you" ? "You" : "Officer"}</b>
            {m.pending ? <em className="ask-wait">Reading the board…</em> : <Rich text={m.text} />}
          </div>
        ))}
      </div>

      <div className={`ask-starters${messages.length > 2 ? " tucked" : ""}`}>
        {STARTERS.map((s) => (
          <button key={s} type="button" onClick={() => send(s)} disabled={busy}>
            {s}
          </button>
        ))}
      </div>

      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about a truck, the clock, or a red band"
          aria-label="Question about the shift"
        />
        <button type="submit" className="ask-send" disabled={busy || !draft.trim()}>
          Send
        </button>
        <button
          type="button"
          className={`ask-mic${live === "on" || live === "talking" ? " on" : ""}`}
          onClick={toggleLive}
          disabled={!configured || live === "connecting"}
          title="Gemini Live voice"
          aria-pressed={live === "on" || live === "talking"}
        >
          {liveLabel}
        </button>
      </form>
    </aside>
  );
});

export default Assistant;

/** Voice transcripts arrive in fragments; glue them onto the turn in progress. */
function joinTurn(messages, role, text) {
  const last = messages[messages.length - 1];
  if (last && last.role === role && last.streamed) {
    return messages.map((m, i) =>
      i === messages.length - 1 ? { ...m, text: m.text + text } : m
    );
  }
  return [...messages, { role, text, streamed: true }];
}

/**
 * The officer writes in the small amount of markdown it was asked for.
 * Rendered into elements, never innerHTML — a model's output is not markup.
 */
function Rich({ text }) {
  const blocks = String(text || "").split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, bi) => {
        const lines = block.split("\n").filter((l) => l.trim());
        const bullets = lines.every((l) => /^\s*[-*]\s+/.test(l)) && lines.length > 0;
        const numbers = lines.every((l) => /^\s*\d+[.)]\s+/.test(l)) && lines.length > 0;
        if (bullets || numbers) {
          const List = numbers ? "ol" : "ul";
          return (
            <List key={bi} className="ask-list">
              {lines.map((l, li) => (
                <li key={li}>{inline(l.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ""))}</li>
              ))}
            </List>
          );
        }
        return <p key={bi}>{inline(block)}</p>;
      })}
    </>
  );
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;

function inline(s) {
  return String(s)
    .split(INLINE)
    .filter((p) => p !== "")
    .map((part, i) => {
      if (/^\*\*[\s\S]+\*\*$/.test(part)) return <b key={i}>{part.slice(2, -2)}</b>;
      if (/^`[\s\S]+`$/.test(part)) return <code key={i}>{part.slice(1, -1)}</code>;
      if (/^\*[^*]+\*$/.test(part)) return <em key={i}>{part.slice(1, -1)}</em>;
      return <React.Fragment key={i}>{part}</React.Fragment>;
    });
}

// --------------------------------------------------------------- the radio --

/**
 * Capture runs in an AudioWorklet. ScriptProcessorNode is deprecated, warns in
 * the console on every session, and does its work on the main thread — which
 * is the same thread painting the map at sixty frames a second.
 */
const PUMP = `
class Pump extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(4096);
    this.n = 0;
    this.gate = false;
    this.port.onmessage = (e) => { this.gate = !!e.data.gate; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) {
        // While the officer is talking, the microphone is mostly hearing the
        // officer. Feeding that back makes the model interrupt itself and the
        // conversation dies after one answer. Frames are dropped unless they
        // are clearly louder than the room — a deliberate interruption still
        // gets through.
        let pass = true;
        if (this.gate) {
          let sum = 0;
          for (let k = 0; k < this.buf.length; k++) {
            const v = this.buf[k] / 32768;
            sum += v * v;
          }
          pass = Math.sqrt(sum / this.buf.length) > 0.12;
        }
        if (pass) {
          const out = this.buf.slice();
          this.port.postMessage(out, [out.buffer]);
        }
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor('pcm-pump', Pump);
`;

const b64 = (bytes) => {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
};

async function startLive(slot, context, hooks) {
  const audio = {
    in: null, out: null, mic: null, node: null, meter: null,
    sock: null, next: 0, queued: [], gated: false, gateTimer: 0,
    onDrain: () => hooks.onTalking?.(false),
  };
  let wanted = true;
  let handle = null;   // session-resumption handle from the API
  let tries = 0;
  let ping = 0;

  const shut = (ctx) => {
    if (!ctx || ctx.state === "closed") return;
    ctx.close().catch(() => { /* it was on its way out anyway */ });
  };

  const stop = () => {
    wanted = false;
    clearInterval(ping);
    clearTimeout(audio.gateTimer);
    try { audio.sock?.close(); } catch { /* already shut */ }
    try { audio.node?.disconnect(); } catch { /* */ }
    try { audio.mic?.getTracks().forEach((t) => t.stop()); } catch { /* */ }
    shut(audio.in);
    shut(audio.out);
    voice.inNode = null;
    voice.outNode = null;
    slot.current = null;
    hooks.onClose?.();
  };
  slot.current = { stop };

  const connect = async () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/api/assistant/live`);
    audio.sock = sock;

    await new Promise((resolve, reject) => {
      sock.onopen = resolve;
      sock.onerror = () => reject(new Error("Live socket failed to open"));
    });

    // A resume handle turns a dropped session into a hiccup instead of the end
    // of the conversation — which matters, because an audio session is capped
    // at fifteen minutes and Gemini warns before it cuts one.
    sock.send(JSON.stringify({ type: "setup", context: context(), resume: handle }));

    sock.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "ready") {
        tries = 0;
        hooks.onReady?.();
        if (!audio.node) {
          beginMic(audio).catch((e) => hooks.onError?.(String(e.message || e)));
        }
      }
      if (msg.type === "resume") handle = msg.handle;
      if (msg.type === "goaway") {
        // Not an error the dispatcher needs to see: reconnecting on the handle
        // is exactly what the handle is for.
        try { sock.close(); } catch { /* it is going anyway */ }
      }
      if (msg.type === "heard" && msg.text) hooks.onHeard?.(msg.text);
      if (msg.type === "said" && msg.text) {
        hooks.onTalking?.(true);
        hooks.onSaid?.(msg.text);
      }
      if (msg.type === "transcript" && msg.text) hooks.onSaid?.(msg.text);
      if (msg.type === "action") hooks.onAction?.(msg);
      if (msg.type === "audio" && msg.data) {
        hooks.onTalking?.(true);
        playPcm(audio, msg.data, msg.mime);
        armGate(audio);
      }
      // Barge-in. Everything already scheduled is an answer to a question the
      // dispatcher has stopped asking, so it has to go.
      if (msg.type === "interrupted") {
        flush(audio);
        hooks.onTalking?.(false);
      }
      if (msg.type === "turn_complete") {
        // Model finished generating — keep "talking" until playback drains.
        armGate(audio);
      }
      if (msg.type === "error") hooks.onError?.(msg.message || "Live error");
    };

    sock.onclose = () => {
      if (!wanted) return;
      if (tries >= 5) {
        hooks.onError?.("Voice kept dropping. Tap the orb to try again.");
        stop();
        return;
      }
      tries += 1;
      hooks.onReconnecting?.(tries);
      setTimeout(() => {
        if (wanted) connect().catch((e) => hooks.onError?.(String(e.message || e)));
      }, 350 * tries);
    };

    clearInterval(ping);
    // Fresh board on meaningful change only — a 15s full dump competed with audio.
    let lastSig = "";
    ping = setInterval(() => {
      if (sock.readyState !== WebSocket.OPEN) return;
      const ctx = context();
      const sig = `${ctx.view}|${ctx.selected}|${(ctx.vehicles || []).length}|${(ctx.kpis?.plan?.committed_routes) ?? ""}`;
      if (sig === lastSig) return;
      lastSig = sig;
      sock.send(JSON.stringify({ type: "context", context: ctx }));
    }, 8000);
  };

  await connect();
}

async function beginMic(audio) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const ctx = new AudioContext({ sampleRate: 16000 });
  const url = URL.createObjectURL(new Blob([PUMP], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);

  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-pump");
  const meter = ctx.createAnalyser();
  meter.fftSize = 256;
  // The socket is read at send time, not captured: a reconnect swaps it and
  // the microphone must follow the new one without being torn down.
  node.port.onmessage = (e) => {
    const sock = audio.sock;
    if (!sock || sock.readyState !== WebSocket.OPEN) return;
    sock.send(JSON.stringify({ type: "audio", data: b64(new Uint8Array(e.data.buffer)) }));
  };
  src.connect(node);
  src.connect(meter);
  audio.in = ctx;
  audio.mic = stream;
  audio.node = node;
  voice.inNode = meter;
}

function outCtx(audio, rate) {
  if (!audio.out) {
    audio.out = new AudioContext({ sampleRate: rate });
    const meter = audio.out.createAnalyser();
    meter.fftSize = 256;
    meter.connect(audio.out.destination);
    audio.meter = meter;
    voice.outNode = meter;
  }
  // Chrome starts a context suspended when it was not created inside a
  // gesture; without this the officer talks and nothing comes out.
  if (audio.out.state === "suspended") audio.out.resume().catch(() => {});
  return audio.out;
}

/** Close the mic gate while the officer speaks; open it when playback drains. */
function gate(audio, shut) {
  if (audio.gated === shut) return;
  audio.gated = shut;
  audio.node?.port.postMessage({ gate: shut });
}

function armGate(audio) {
  gate(audio, true);
  clearTimeout(audio.gateTimer);
  const ctx = audio.out;
  const left = ctx ? Math.max(0, (audio.next - ctx.currentTime) * 1000) : 0;
  // A turn that is cut short never sends turn_complete, so the gate must also
  // be able to open itself once there is nothing left to play.
  audio.gateTimer = setTimeout(() => gate(audio, false), left + 50);
}

function flush(audio) {
  for (const src of audio.queued) {
    try { src.stop(); } catch { /* already finished */ }
  }
  audio.queued = [];
  audio.next = 0;
  clearTimeout(audio.gateTimer);
  gate(audio, false);
}

function playPcm(audio, data, mime) {
  const rate = Number(/rate=(\d+)/.exec(mime || "")?.[1]) || 24000;
  const raw = atob(data);
  const n = raw.length;
  const bytes = new Uint8Array(n);
  // Chunked fill — faster than a single giant loop on long replies.
  for (let i = 0; i < n; i += 0x4000) {
    const end = Math.min(i + 0x4000, n);
    for (let j = i; j < end; j++) bytes[j] = raw.charCodeAt(j);
  }
  const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 1);
  const ctx = outCtx(audio, rate);
  const frame = ctx.createBuffer(1, samples.length, rate);
  const ch = frame.getChannelData(0);
  for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 32768;
  const src = ctx.createBufferSource();
  src.buffer = frame;
  src.connect(audio.meter || ctx.destination);
  const start = Math.max(ctx.currentTime + 0.02, audio.next || 0);
  src.start(start);
  audio.next = start + frame.duration;
  audio.queued.push(src);
  src.onended = () => {
    audio.queued = audio.queued.filter((s) => s !== src);
    if (!audio.queued.length) {
      gate(audio, false);
      audio.onDrain?.();
    }
  };
}
