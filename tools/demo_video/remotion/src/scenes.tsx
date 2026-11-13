// One component per scene type. Landscape (16:9) keeps the reference layout
// in 1920×1080 design px scaled by `k`; square and portrait stack the parts.
import React from "react";
import { AbsoluteFill, Sequence, interpolate, useCurrentFrame } from "remotion";
import {
  C,
  Caption,
  ChatStream,
  Clip,
  Device,
  HAND,
  INTER,
  Label,
  Logo,
  Monitor,
  Pop,
  ZoomWindow,
  color,
  deviceHeight,
  deviceOuterWidth,
  monitorHeight,
  useIn,
  useLayout,
  zoomWindowHeight,
} from "./ui";
import type {
  BrandScene,
  CaptionSpec,
  CropZoomScene,
  DeviceCaptionScene,
  FullBleedScene,
  OutroScene,
  Product,
  SideBySideScene,
  SplitScene,
  StepsDeviceScene,
  TilesStatementScene,
  TitleCardScene,
  WindowStatesScene,
} from "./types";

/** Rough rendered height of a caption, for stacking (design px × k). */
const captionHeight = (c: CaptionSpec, k: number, defaultSize: number, width: number) => {
  const size = (c.size ?? defaultSize) * k;
  const charsPerLine = Math.max(8, (width * k) / (size * 0.55));
  const lines = (s: string) => s.split("\n").reduce((n, l) => n + Math.max(1, Math.ceil(l.length / charsPerLine)), 0);
  let h = lines(c.title) * size * 1.08;
  if (c.kicker) h += 70 * k;
  if (c.sub) h += 22 * k + lines(c.sub) * 30 * k * 1.35 * (size / (30 * k) > 1.6 ? 1.2 : 1);
  return h;
};

/** The widest screen width whose device fits inside `maxW` × `maxH`. */
const fitDevice = (kind: "laptop" | "monitor" | "window", maxW: number, maxH: number, k: number) => {
  let w = maxW - (deviceOuterWidth(kind, maxW, k) - maxW);
  while (w > 100 && deviceHeight(kind, w, k) > maxH) w -= 8;
  return w;
};

export const FullBleed: React.FC<{ s: FullBleedScene }> = ({ s }) => {
  const frame = useCurrentFrame();
  const { W, H, landscape, square, k, pad, fps } = useLayout();
  const [z0, z1] = s.zoom ?? [1, 1.07];
  const bandH = (W * 9) / 16;
  const bandTop = landscape ? 0 : square ? 0 : Math.max(pad, (H - bandH) / 2 - 140 * k);
  const zoom = interpolate(frame, [0, s.dur], [z0, z1]);
  const capW = landscape ? (s.caption.width ?? 1300) : (W - 2 * pad) / k;
  const capH = captionHeight(s.caption, k, 92, capW);
  return (
    <AbsoluteFill>
      {landscape ? (
        <AbsoluteFill style={{ transform: `scale(${zoom})`, transformOrigin: s.focus ?? "40% 55%" }}>
          <Clip clip={s.clip} />
        </AbsoluteFill>
      ) : (
        <div style={{ position: "absolute", left: 0, top: bandTop, width: W, height: bandH, overflow: "hidden" }}>
          <div style={{ width: "100%", height: "100%", transform: `scale(${zoom})`, transformOrigin: s.focus ?? "40% 55%" }}>
            <Clip clip={s.clip} />
          </div>
        </div>
      )}
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(90deg, rgba(14,16,32,.0) 55%, rgba(14,16,32,.75) 100%), linear-gradient(0deg, rgba(14,16,32,.92) 0%, rgba(14,16,32,.0) 42%)",
        }}
      />
      <div style={{ position: "absolute", left: landscape ? 90 * k : pad, bottom: landscape ? 80 * k : pad }}>
        <Caption {...s.caption} width={capW} size={s.caption.size ?? 92} delay={8} k={k} />
      </div>
      {s.chat && (
        <div style={{ position: "absolute", right: landscape ? 60 * k : pad, bottom: landscape ? 80 * k : pad + capH + 30 * k }}>
          <ChatStream
            messages={s.chat.messages}
            every={Math.max(1, Math.round(s.chat.every_s * fps))}
            start={Math.round(s.chat.start_s * fps)}
            width={380}
            max={s.chat.max}
            k={k}
          />
        </div>
      )}
    </AbsoluteFill>
  );
};

const TILE_HUES = [18, 200, 280, 140, 35, 330, 170, 250, 5, 60, 220, 300];

/** A meeting tile with the camera on: a lit room, a person, and now and then a chat message. */
const Tile: React.FC<{ name: string; index: number; delay: number; chat?: { text: string; at: number } }> = ({ name, index, delay, chat }) => {
  const frame = useCurrentFrame();
  const p = useIn(delay, 18);
  const c = useIn(chat ? chat.at : 0, 12);
  const hue = TILE_HUES[index % TILE_HUES.length];
  const sway = Math.sin((frame + index * 17) / 22) * 3;
  const talking = chat !== undefined && frame >= chat.at && frame < chat.at + 40;
  return (
    <div
      style={{
        width: 220,
        height: 140,
        borderRadius: 10,
        background: `radial-gradient(140px 90px at 30% 20%, hsl(${hue} 45% 62%), hsl(${hue} 35% 28%))`,
        position: "relative",
        opacity: p,
        transform: `scale(${0.8 + 0.2 * p})`,
        fontFamily: INTER,
        boxShadow: talking ? `0 0 0 3px ${C.green}` : "0 0 0 1px rgba(255,255,255,.08)",
      }}
    >
      <div style={{ position: "absolute", inset: 0, borderRadius: 10, overflow: "hidden" }}>
        <div style={{ position: "absolute", left: 84 + sway, top: 30, width: 52, height: 52, borderRadius: 26, background: `hsl(${(hue + 30) % 360} 30% 82%)` }} />
        <div
          style={{
            position: "absolute",
            left: 55 + sway * 0.6,
            top: 88,
            width: 110,
            height: 80,
            borderRadius: "55px 55px 0 0",
            background: `hsl(${(hue + 180) % 360} 35% 40%)`,
          }}
        />
      </div>
      <div style={{ position: "absolute", left: 8, bottom: 7, fontSize: 13, color: "#fff", background: "rgba(0,0,0,.45)", padding: "2px 8px", borderRadius: 6 }}>
        {name}
      </div>
      {chat && frame >= chat.at && (
        <div
          style={{
            position: "absolute",
            right: -10,
            top: -24,
            background: "#fff",
            color: "#111",
            fontSize: 17,
            fontWeight: 700,
            padding: "6px 12px",
            borderRadius: "14px 14px 4px 14px",
            boxShadow: "0 8px 20px rgba(0,0,0,.4)",
            opacity: c,
            transform: `translateY(${(1 - c) * 14}px) scale(${0.7 + 0.3 * c})`,
            whiteSpace: "nowrap",
            zIndex: 2,
          }}
        >
          💬 {chat.text}
        </div>
      )}
    </div>
  );
};

export const TilesStatement: React.FC<{ s: TilesStatementScene }> = ({ s }) => {
  const frame = useCurrentFrame();
  const { W, H, landscape, k, pad, fps } = useLayout();
  const swap = Math.round(s.swap_s * fps);
  const gridW = 4 * 220 + 3 * 14;
  const gridH = 3 * 140 + 2 * 34;
  const ts = landscape ? k : (W - 2 * pad) / gridW;
  const grid = (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 220px)", gap: 14, rowGap: 34 }}>
      {s.tiles.map((name, i) => {
        const n = s.talkers.indexOf(i);
        return <Tile key={name} name={name} index={i} delay={i * 2} chat={n >= 0 && s.chats[n] ? { text: s.chats[n], at: 22 + n * 14 } : undefined} />;
      })}
    </div>
  );
  const textW = landscape ? 820 : (W - 2 * pad) / k;
  const caption = frame < swap ? (
    <Caption {...s.before} width={textW} size={s.before.size ?? 70} k={k} />
  ) : (
    <Sequence from={swap} layout="none">
      <Caption {...s.after} width={textW} size={s.after.size ?? 76} k={k} />
    </Sequence>
  );
  return landscape ? (
    <AbsoluteFill>
      <div style={{ position: "absolute", right: 80 * k, top: 190 * k, transform: `scale(${ts})`, transformOrigin: "right top" }}>{grid}</div>
      <div style={{ position: "absolute", left: 90 * k, top: 300 * k }}>{caption}</div>
    </AbsoluteFill>
  ) : (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: pad, top: pad + 20 }}>{caption}</div>
      <div style={{ position: "absolute", left: pad, top: H - pad - gridH * ts, transform: `scale(${ts})`, transformOrigin: "left top" }}>{grid}</div>
    </AbsoluteFill>
  );
};

export const TitleCard: React.FC<{ s: TitleCardScene }> = ({ s }) => {
  const { W, landscape, k, pad } = useLayout();
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <Caption {...s.caption} align="center" width={landscape ? (s.caption.width ?? 1500) : (W - 2 * pad) / k} size={s.caption.size ?? 96} k={k} />
    </AbsoluteFill>
  );
};

export const Brand: React.FC<{ s: BrandScene; product: Product }> = ({ s, product }) => {
  const { W, landscape, k, pad } = useLayout();
  const p = useIn(0, 12);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", gap: 34 * k }}>
      <div style={{ transform: `scale(${0.6 + 0.4 * p}) rotate(${(1 - p) * -12}deg)`, opacity: p }}>
        <Logo product={product} size={150 * k} />
      </div>
      <Caption title={product.name} sub={s.sub} align="center" width={landscape ? 1500 : (W - 2 * pad) / k} size={104} delay={6} k={k} />
    </AbsoluteFill>
  );
};

export const StepsDevice: React.FC<{ s: StepsDeviceScene }> = ({ s }) => {
  const frame = useCurrentFrame();
  const { W, landscape, k, pad, fps } = useLayout();
  const toFrame = (sec: number) => ((sec - s.clip.from) / s.clip.rate) * fps;
  const switches = [0, ...s.step_at.map(toFrame)];
  const active = switches.filter((x) => frame >= x).length - 1;
  const steps = (
    <div style={{ width: (landscape ? 560 : (W - 2 * pad) / k) * k }}>
      {s.steps.map((step, i) => {
        const on = i === active;
        const done = i < active;
        return (
          <Pop key={step.title} delay={i * 8}>
            <div
              style={{
                display: "flex",
                gap: 22 * k,
                padding: `${26 * k}px`,
                marginBottom: 22 * k,
                borderRadius: 22 * k,
                background: on ? "rgba(255,255,255,.1)" : "transparent",
                border: `2px solid ${on ? "rgba(255,255,255,.35)" : "transparent"}`,
                opacity: on ? 1 : done ? 0.55 : 0.4,
                fontFamily: INTER,
              }}
            >
              <div
                style={{
                  flex: "0 0 auto",
                  width: 58 * k,
                  height: 58 * k,
                  borderRadius: 29 * k,
                  background: on ? C.red : done ? C.green : "#3a3f5c",
                  color: "#fff",
                  fontWeight: 800,
                  fontSize: 28 * k,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {done ? "✓" : i + 1}
              </div>
              <div style={{ color: C.ink }}>
                <div style={{ fontSize: 38 * k, fontWeight: 800, lineHeight: 1.1 }}>{step.title}</div>
                <div style={{ fontSize: 24 * k, color: C.dim, marginTop: 8 * k, lineHeight: 1.3 }}>{step.sub}</div>
              </div>
            </div>
          </Pop>
        );
      })}
    </div>
  );
  if (landscape) {
    return (
      <AbsoluteFill>
        <div style={{ position: "absolute", left: 70 * k, top: 200 * k }}>{steps}</div>
        <Pop delay={2} style={{ position: "absolute", left: 690 * k, top: 170 * k }}>
          <Device kind={s.device} width={1100 * k} k={k}>
            <Clip clip={s.clip} />
          </Device>
        </Pop>
      </AbsoluteFill>
    );
  }
  const devW = fitDevice(s.device, W - 2 * pad, W * 0.62, k);
  return (
    <AbsoluteFill>
      <Pop delay={2} style={{ position: "absolute", left: (W - deviceOuterWidth(s.device, devW, k)) / 2, top: pad }}>
        <Device kind={s.device} width={devW} k={k}>
          <Clip clip={s.clip} />
        </Device>
      </Pop>
      <div style={{ position: "absolute", left: pad, top: pad + deviceHeight(s.device, devW, k) + 24 * k }}>{steps}</div>
    </AbsoluteFill>
  );
};

export const SplitWindowMonitor: React.FC<{ s: SplitScene }> = ({ s }) => {
  const { W, H, landscape, square, k, pad } = useLayout();
  if (landscape) {
    return (
      <AbsoluteFill>
        <div style={{ position: "absolute", left: 70 * k, top: 56 * k }}>
          <Caption {...s.caption} width={s.caption.width ?? 1780} size={s.caption.size ?? 54} k={k} />
        </div>
        <div style={{ position: "absolute", left: 70 * k, top: 250 * k }}>
          <Label color={C.red} delay={10} k={k}>● {s.window.label}</Label>
          <div style={{ height: 16 * k }} />
          <ZoomWindow width={1000 * k} k={k}>
            <Clip clip={s.window.clip} />
          </ZoomWindow>
        </div>
        <div style={{ position: "absolute", left: 1120 * k, top: 330 * k }}>
          <Label delay={16} k={k}>🖥 {s.monitor.label}</Label>
          <div style={{ height: 16 * k }} />
          <Monitor width={700 * k} k={k}>
            <Clip clip={s.monitor.clip} />
          </Monitor>
        </div>
      </AbsoluteFill>
    );
  }
  const capW = (W - 2 * pad) / k;
  const capH = captionHeight(s.caption, k, s.caption.size ?? 54, capW);
  const winTop = pad + capH + 24 * k;
  if (square) {
    const gap = 24 * k;
    const sw = (W - 2 * pad - gap) * 0.6;
    const mw = W - 2 * pad - gap - sw - 28 * k;
    return (
      <AbsoluteFill>
        <div style={{ position: "absolute", left: pad, top: pad }}>
          <Caption {...s.caption} width={capW} size={s.caption.size ?? 54} k={k} />
        </div>
        <div style={{ position: "absolute", left: pad, top: winTop + 60 * k }}>
          <Label color={C.red} delay={10} k={k}>● {s.window.label}</Label>
          <div style={{ height: 12 * k }} />
          <ZoomWindow width={sw} k={k}>
            <Clip clip={s.window.clip} />
          </ZoomWindow>
        </div>
        <div style={{ position: "absolute", left: pad + sw + gap, top: winTop + 60 * k }}>
          <Label delay={16} k={k}>🖥 {s.monitor.label}</Label>
          <div style={{ height: 12 * k }} />
          <Monitor width={mw} k={k}>
            <Clip clip={s.monitor.clip} />
          </Monitor>
        </div>
      </AbsoluteFill>
    );
  }
  const winW = Math.min(W - 2 * pad, ((H - winTop - pad - 50 * k) * 0.8 - 84 * k) * (16 / 9));
  const monW = W * 0.42;
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: pad, top: pad }}>
        <Caption {...s.caption} width={capW} size={s.caption.size ?? 54} k={k} />
      </div>
      <div style={{ position: "absolute", left: pad, top: winTop }}>
        <Label color={C.red} delay={10} k={k}>● {s.window.label}</Label>
        <div style={{ height: 12 * k }} />
        <ZoomWindow width={winW} k={k}>
          <Clip clip={s.window.clip} />
        </ZoomWindow>
      </div>
      <div style={{ position: "absolute", right: pad, top: H - pad - monitorHeight(monW, k) - 50 * k }}>
        <Label delay={16} k={k}>🖥 {s.monitor.label}</Label>
        <div style={{ height: 12 * k }} />
        <Monitor width={monW} k={k}>
          <Clip clip={s.monitor.clip} />
        </Monitor>
      </div>
    </AbsoluteFill>
  );
};

export const CropZoom: React.FC<{ s: CropZoomScene }> = ({ s }) => {
  const { W, H, landscape, square, k, pad } = useLayout();
  const crop = s.panel.crop ?? { x: 0, y: 0, w: 1920, h: 1080 };
  const panel = (w: number) => (
    <div
      style={{
        width: w,
        height: (w * crop.h) / crop.w,
        borderRadius: 18 * k,
        overflow: "hidden",
        boxShadow: "0 30px 70px rgba(0,0,0,.55), 0 0 0 3px rgba(79,163,255,.8)",
        background: "#fff",
      }}
    >
      <Clip clip={s.panel} />
    </div>
  );
  if (landscape) {
    return (
      <AbsoluteFill>
        <div style={{ position: "absolute", left: 70 * k, top: 60 * k }}>
          <Caption {...s.caption} width={s.caption.width ?? 600} size={s.caption.size ?? 56} delay={4} k={k} />
        </div>
        <Pop delay={6} style={{ position: "absolute", left: 120 * k, top: 470 * k }}>
          {panel(378 * k)}
        </Pop>
        <Pop delay={0} style={{ position: "absolute", left: 640 * k, top: 150 * k }}>
          <ZoomWindow width={1210 * k} k={k}>
            <Clip clip={s.window} />
          </ZoomWindow>
        </Pop>
      </AbsoluteFill>
    );
  }
  const capW = (W - 2 * pad) / k;
  const capH = captionHeight(s.caption, k, s.caption.size ?? 56, capW);
  const winTop = pad + capH + 24 * k;
  if (square) {
    const gap = 24 * k;
    const panelH = H - winTop - pad;
    const pw = Math.min(W * 0.26, (panelH * crop.w) / crop.h);
    const ww = W - 2 * pad - gap - pw;
    return (
      <AbsoluteFill>
        <div style={{ position: "absolute", left: pad, top: pad }}>
          <Caption {...s.caption} width={capW} size={s.caption.size ?? 56} delay={4} k={k} />
        </div>
        <Pop delay={6} style={{ position: "absolute", left: pad, top: winTop }}>
          {panel(pw)}
        </Pop>
        <Pop delay={0} style={{ position: "absolute", left: pad + pw + gap, top: winTop }}>
          <ZoomWindow width={ww} k={k}>
            <Clip clip={s.window} />
          </ZoomWindow>
        </Pop>
      </AbsoluteFill>
    );
  }
  const winW = Math.min(W - 2 * pad, (H - winTop - pad - 84 * k) * (16 / 9));
  const panelW = Math.min(W * 0.3, ((H - pad - winTop) * 0.8 * crop.w) / crop.h);
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: pad, top: pad }}>
        <Caption {...s.caption} width={capW} size={s.caption.size ?? 56} delay={4} k={k} />
      </div>
      <Pop delay={0} style={{ position: "absolute", left: (W - winW) / 2, top: winTop }}>
        <ZoomWindow width={winW} k={k}>
          <Clip clip={s.window} />
        </ZoomWindow>
      </Pop>
      <Pop delay={6} style={{ position: "absolute", left: pad, top: H - pad - (panelW * crop.h) / crop.w }}>
        {panel(panelW)}
      </Pop>
    </AbsoluteFill>
  );
};

export const SideBySide: React.FC<{ s: SideBySideScene }> = ({ s }) => {
  const { W, H, landscape, k, pad, fps } = useLayout();
  const n = s.windows.length;
  const gap = 35 * k;
  const capW = landscape ? (s.caption.width ?? 1780) : (W - 2 * pad) / k;
  const top = landscape ? 300 * k : pad + captionHeight(s.caption, k, s.caption.size ?? 54, capW) + 30 * k;
  let winW: number;
  if (landscape) {
    winW = (W - 2 * pad - (n - 1) * gap) / n;
  } else {
    const eachH = (H - top - pad - (n - 1) * gap) / n;
    winW = Math.min(W - 2 * pad, (eachH - 84 * k) * (16 / 9));
  }
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: pad, top: landscape ? 56 * k : pad }}>
        <Caption {...s.caption} width={capW} size={s.caption.size ?? 54} k={k} />
      </div>
      {s.windows.map((clip, i) => (
        <Pop
          key={i}
          delay={Math.round(i * s.stagger_s * fps)}
          style={
            landscape
              ? { position: "absolute", left: pad + i * (winW + gap), top }
              : { position: "absolute", left: (W - winW) / 2, top: top + i * (zoomWindowHeight(winW, k) + gap) }
          }
        >
          <ZoomWindow width={winW} k={k}>
            <Clip clip={clip} />
          </ZoomWindow>
        </Pop>
      ))}
    </AbsoluteFill>
  );
};

export const DeviceCaption: React.FC<{ s: DeviceCaptionScene }> = ({ s }) => {
  const { W, H, landscape, k, pad } = useLayout();
  if (landscape) {
    return (
      <AbsoluteFill>
        <AbsoluteFill style={{ padding: `0 ${70 * k}px`, justifyContent: "center" }}>
          <Caption {...s.caption} width={s.caption.width ?? 560} size={s.caption.size ?? 58} k={k} />
        </AbsoluteFill>
        <Pop delay={4} style={{ position: "absolute", left: 690 * k, top: 170 * k }}>
          <Device kind={s.device} width={1100 * k} k={k}>
            <Clip clip={s.clip} />
          </Device>
        </Pop>
      </AbsoluteFill>
    );
  }
  const capW = (W - 2 * pad) / k;
  const capH = captionHeight(s.caption, k, s.caption.size ?? 58, capW);
  const top = pad + capH + 30 * k;
  const devW = fitDevice(s.device, W - 2 * pad, H - top - pad, k);
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: pad, top: pad }}>
        <Caption {...s.caption} width={capW} size={s.caption.size ?? 58} k={k} />
      </div>
      <Pop delay={4} style={{ position: "absolute", left: (W - deviceOuterWidth(s.device, devW, k)) / 2, top }}>
        <Device kind={s.device} width={devW} k={k}>
          <Clip clip={s.clip} />
        </Device>
      </Pop>
    </AbsoluteFill>
  );
};

export const WindowStates: React.FC<{ s: WindowStatesScene }> = ({ s }) => {
  const frame = useCurrentFrame();
  const { W, H, landscape, k, pad, fps } = useLayout();
  // segment start frames
  const starts: number[] = [];
  let t = 0;
  for (const seg of s.segments) {
    starts.push(t);
    t += seg.seconds == null ? s.dur : Math.round(seg.seconds * fps);
  }
  const li = s.legend.segment;
  const seg = s.segments[li];
  let state = -1;
  if (frame >= starts[li]) {
    const src = seg.clip.from + ((frame - starts[li]) / fps) * seg.clip.rate;
    const rel = src - s.legend.anchor;
    for (const [at, st] of s.legend.changes) if (rel >= at) state = st;
  }
  const legend = (
    <div style={{ display: "flex", flexDirection: landscape ? "column" : "row", flexWrap: "wrap", gap: 16 * k, fontFamily: INTER }}>
      {s.legend.labels.map((label, i) => {
        const on = state === i;
        const c = color(s.legend.colors[i], s.legend.colors[i]);
        return (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 16 * k, opacity: state < 0 ? 0.35 : on ? 1 : 0.4 }}>
            <div
              style={{
                width: 30 * k,
                height: 30 * k,
                borderRadius: 15 * k,
                background: c,
                boxShadow: on ? `0 0 0 6px ${c}55, 0 0 30px ${c}` : "none",
                border: "2px solid rgba(255,255,255,.4)",
              }}
            />
            <div style={{ color: C.ink, fontSize: 32 * k, fontWeight: on ? 800 : 500 }}>{label}</div>
          </div>
        );
      })}
    </div>
  );
  const winW = landscape ? 1200 * k : 0;
  const capW = landscape ? (s.caption.width ?? 560) : (W - 2 * pad) / k;
  const capH = landscape ? 0 : captionHeight(s.caption, k, s.caption.size ?? 54, capW);
  const winTop = landscape ? 150 * k : pad + capH + 24 * k + 2 * 50 * k + 24 * k;
  const stackW = landscape ? winW : Math.min(W - 2 * pad, (H - winTop - pad - 84 * k) * (16 / 9));
  const winLeft = landscape ? 650 * k : (W - stackW) / 2;
  const showChip = s.speed_chip && state >= 0 && seg.clip.rate !== 1;
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: landscape ? 70 * k : pad, top: landscape ? 200 * k : pad }}>
        <Caption {...s.caption} width={capW} size={s.caption.size ?? 54} k={k} />
        <div style={{ marginTop: (landscape ? 50 : 24) * k }}>{legend}</div>
      </div>
      <Pop delay={0} style={{ position: "absolute", left: winLeft, top: winTop }}>
        <ZoomWindow width={stackW} k={k}>
          {s.segments.map((sg, i) => (
            <Sequence key={i} from={starts[i]} durationInFrames={sg.seconds == null ? undefined : Math.round(sg.seconds * fps)} layout="none">
              <Clip clip={sg.clip} />
            </Sequence>
          ))}
        </ZoomWindow>
      </Pop>
      {showChip && (
        <div
          style={{
            position: "absolute",
            left: winLeft + stackW - 110 * k,
            top: winTop + 25 * k,
            fontFamily: INTER,
            fontWeight: 800,
            fontSize: 22 * k,
            color: "#fff",
            background: "rgba(0,0,0,.55)",
            padding: `${6 * k}px ${14 * k}px`,
            borderRadius: 999,
          }}
        >
          ⏩ ×{seg.clip.rate}
        </div>
      )}
    </AbsoluteFill>
  );
};

export const Outro: React.FC<{ s: OutroScene; product: Product }> = ({ s, product }) => {
  const frame = useCurrentFrame();
  const { W, landscape, k, pad } = useLayout();
  const black = interpolate(frame, [s.dur - 30, s.dur], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const inn = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: inn }}>
      <AbsoluteFill style={{ filter: "blur(7px)", transform: "scale(1.08)" }}>
        <Clip clip={s.clip} cover={!landscape} />
      </AbsoluteFill>
      <AbsoluteFill style={{ background: "rgba(14,16,32,.78)" }} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", gap: 30 * k }}>
        <Pop delay={4}>
          <Logo product={product} size={130 * k} />
        </Pop>
        <Caption title={s.tagline} align="center" width={landscape ? 1600 : (W - 2 * pad) / k} size={landscape ? 88 : 72} delay={10} k={k} />
        <Pop delay={30}>
          <div style={{ fontFamily: INTER, color: C.ink, fontSize: 44 * k, fontWeight: 800, letterSpacing: -0.5 }}>{product.name}</div>
        </Pop>
        <Pop delay={40}>
          <div style={{ fontFamily: HAND, color: C.dim, fontSize: 36 * k, textAlign: "center", padding: `0 ${pad}px` }}>{s.footer}</div>
        </Pop>
      </AbsoluteFill>
      <AbsoluteFill style={{ background: "#000", opacity: black }} />
    </AbsoluteFill>
  );
};
