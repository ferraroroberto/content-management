import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadHand } from "@remotion/google-fonts/PatrickHand";
import type { CaptionSpec, ChatMessage, ClipRef, Product } from "./types";

export const { fontFamily: INTER } = loadInter("normal", { weights: ["400", "600", "800"], subsets: ["latin"] });
export const { fontFamily: HAND } = loadHand("normal", { weights: ["400"], subsets: ["latin"] });

export const C = {
  bg: "#0e1020",
  ink: "#f5f6fb",
  dim: "#a9aec8",
  red: "#e53935",
  green: "#00a44e",
  yellow: "#f2b705",
  blue: "#1e88e5",
  purple: "#8e24aa",
  slate: "#555a7a",
  paper: "#f2f2f2",
};

/** A named palette colour or any CSS colour. */
export const color = (c: string | undefined, fallback: string = C.red) =>
  c ? ((C as Record<string, string>)[c] ?? c) : fallback;

/** Layout facts for the current canvas: `k` scales the 1920×1080 design. */
export const useLayout = () => {
  const { width: W, height: H, fps } = useVideoConfig();
  const landscape = W / H > 1.3;
  const square = !landscape && H / W < 1.15;
  const k = landscape ? W / 1920 : W / 1350;
  const pad = landscape ? 70 * k : 56;
  return { W, H, fps, landscape, square, k, pad };
};

/** A recorded clip, started at `from` seconds of the source and played at `rate`. */
export const Clip: React.FC<{ clip: ClipRef; style?: React.CSSProperties; cover?: boolean; focus?: string }> = ({
  clip,
  style,
  cover,
  focus,
}) => {
  const { fps } = useVideoConfig();
  const { crop } = clip;
  const video = (
    <OffthreadVideo
      src={staticFile(clip.src)}
      startFrom={Math.round(clip.from * fps)}
      playbackRate={clip.rate}
      muted
      style={{
        width: "100%",
        height: "100%",
        display: "block",
        objectFit: cover ? "cover" : undefined,
        objectPosition: focus,
        ...(crop ? {} : style),
      }}
    />
  );
  if (!crop) return video;
  return (
    <div style={{ width: "100%", height: "100%", overflow: "hidden", position: "relative", ...style }}>
      <div
        style={{
          position: "absolute",
          width: `${(1920 / crop.w) * 100}%`,
          height: `${(1080 / crop.h) * 100}%`,
          left: `${(-crop.x / crop.w) * 100}%`,
          top: `${(-crop.y / crop.h) * 100}%`,
        }}
      >
        {video}
      </div>
    </div>
  );
};

/** Spring 0→1 starting at `delay` frames. */
export const useIn = (delay = 0, damping = 14) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, config: { damping, mass: 0.7 } });
};

/** Fade a whole scene in and out over `edge` frames. */
export const SceneFade: React.FC<{ dur: number; edge?: number; children: React.ReactNode }> = ({ dur, edge = 8, children }) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, edge, dur - edge, dur], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

export const Backdrop: React.FC = () => {
  const frame = useCurrentFrame();
  const a = (frame / 6) % 360;
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(1200px 800px at ${30 + 10 * Math.sin((a * Math.PI) / 180)}% 20%, #2a2f63 0%, transparent 60%),
                     radial-gradient(900px 700px at 85% ${80 + 8 * Math.cos((a * Math.PI) / 180)}%, #3b1730 0%, transparent 60%),
                     ${C.bg}`,
      }}
    />
  );
};

/** Kicker chip + headline + sub, springing up. Sizes are in canvas px. */
export const Caption: React.FC<
  CaptionSpec & { delay?: number; align?: "left" | "center"; k?: number }
> = ({ kicker, title, sub, delay = 0, width = 640, align = "left", color: c, size = 64, k = 1 }) => {
  const p = useIn(delay);
  const p2 = useIn(delay + 6);
  const p3 = useIn(delay + 12);
  return (
    <div style={{ width: width * k, textAlign: align, fontFamily: INTER, color: C.ink }}>
      {kicker && (
        <div
          style={{
            display: "inline-block",
            padding: `${8 * k}px ${18 * k}px`,
            borderRadius: 999,
            background: color(c),
            fontWeight: 800,
            fontSize: 24 * k,
            letterSpacing: 1.5 * k,
            textTransform: "uppercase",
            opacity: p,
            transform: `translateY(${(1 - p) * 20}px)`,
            marginBottom: 22 * k,
          }}
        >
          {kicker}
        </div>
      )}
      <div
        style={{
          fontSize: size * k,
          fontWeight: 800,
          lineHeight: 1.08,
          letterSpacing: -1 * k,
          opacity: p2,
          transform: `translateY(${(1 - p2) * 30}px)`,
          whiteSpace: "pre-line",
        }}
      >
        {title}
      </div>
      {sub && (
        <div
          style={{
            marginTop: 22 * k,
            fontSize: 30 * k,
            lineHeight: 1.35,
            color: C.dim,
            fontWeight: 400,
            opacity: p3,
            transform: `translateY(${(1 - p3) * 20}px)`,
            whiteSpace: "pre-line",
          }}
        >
          {sub}
        </div>
      )}
    </div>
  );
};

/** Height a ZoomWindow of `width` takes, chrome included. */
export const zoomWindowHeight = (width: number, k = 1) => (width * 9) / 16 + 38 * k + 46 * k;

/** A Zoom-style meeting window around a 16:9 view — what participants see. */
export const ZoomWindow: React.FC<{ width: number; label?: string; children: React.ReactNode; people?: number; k?: number }> = ({
  width,
  label,
  children,
  people = 18,
  k = 1,
}) => {
  const h = (width * 9) / 16;
  const bar = 38 * k;
  const dot = 12 * k;
  return (
    <div
      style={{
        width,
        borderRadius: 14 * k,
        overflow: "hidden",
        background: "#1a1a1a",
        boxShadow: "0 40px 80px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.08)",
        fontFamily: INTER,
      }}
    >
      <div style={{ height: bar, display: "flex", alignItems: "center", gap: 8 * k, padding: `0 ${14 * k}px`, color: "#ddd", fontSize: 15 * k }}>
        <span style={{ width: dot, height: dot, borderRadius: dot / 2, background: "#ff5f57" }} />
        <span style={{ width: dot, height: dot, borderRadius: dot / 2, background: "#febc2e" }} />
        <span style={{ width: dot, height: dot, borderRadius: dot / 2, background: "#28c840" }} />
        <span style={{ marginLeft: 14 * k, fontWeight: 600 }}>{label ?? "Zoom Meeting"}</span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 * k, color: "#ff6b6b", fontWeight: 600 }}>
          <span style={{ width: 9 * k, height: 9 * k, borderRadius: 5 * k, background: "#ff3b30" }} /> REC
        </span>
      </div>
      <div style={{ width, height: h, background: C.paper }}>{children}</div>
      <div
        style={{
          height: 46 * k,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 34 * k,
          color: "#cfcfcf",
          fontSize: 14 * k,
          fontWeight: 600,
        }}
      >
        <span>🎤 Mute</span>
        <span>📷 Video</span>
        <span>👥 {people}</span>
        <span style={{ color: "#4fa3ff" }}>💬 Chat</span>
        <span>⬚ Share</span>
      </div>
    </div>
  );
};

/** Height a Monitor of `width` takes, stand included. */
export const monitorHeight = (width: number, k = 1) => (width * 9) / 16 + 28 * k + 46 * k + 12 * k;

/** A dark monitor bezel — a second screen. */
export const Monitor: React.FC<{ width: number; children: React.ReactNode; k?: number }> = ({ width, children, k = 1 }) => {
  const h = (width * 9) / 16;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <div
        style={{
          width: width + 28 * k,
          padding: 14 * k,
          borderRadius: 18 * k,
          background: "#0b0b0f",
          boxShadow: "0 40px 80px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.1)",
        }}
      >
        <div style={{ width, height: h, overflow: "hidden", borderRadius: 4, background: "#fff" }}>{children}</div>
      </div>
      <div style={{ width: 90 * k, height: 46 * k, background: "linear-gradient(#1b1b22,#0d0d12)" }} />
      <div style={{ width: 260 * k, height: 12 * k, borderRadius: 6 * k, background: "#1b1b22" }} />
    </div>
  );
};

/** Height a Laptop of screen `width` takes, base included; its base is `width + 160k` wide. */
export const laptopHeight = (width: number, k = 1) => (width * 9) / 16 + 40 * k + 26 * k;

/** A laptop — an app on the facilitator's PC. */
export const Laptop: React.FC<{ width: number; children: React.ReactNode; k?: number }> = ({ width, children, k = 1 }) => {
  const h = (width * 9) / 16;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <div
        style={{
          width: width + 36 * k,
          padding: `${18 * k}px ${18 * k}px ${22 * k}px`,
          borderRadius: `${22 * k}px ${22 * k}px ${6 * k}px ${6 * k}px`,
          background: "#16161c",
          boxShadow: "0 0 0 2px #2c2c36",
        }}
      >
        <div style={{ width, height: h, overflow: "hidden", borderRadius: 3, background: "#fff" }}>{children}</div>
      </div>
      <div
        style={{
          width: width + 160 * k,
          height: 26 * k,
          borderRadius: `0 0 ${26 * k}px ${26 * k}px`,
          background: "linear-gradient(#c9ccd6,#8d909c)",
          boxShadow: "0 40px 60px rgba(0,0,0,.6)",
        }}
      />
    </div>
  );
};

/** A device frame by name, around a clip. */
export const Device: React.FC<{ kind: "laptop" | "monitor" | "window"; width: number; k?: number; children: React.ReactNode }> = ({
  kind,
  width,
  k = 1,
  children,
}) =>
  kind === "laptop" ? (
    <Laptop width={width} k={k}>{children}</Laptop>
  ) : kind === "monitor" ? (
    <Monitor width={width} k={k}>{children}</Monitor>
  ) : (
    <ZoomWindow width={width} k={k}>{children}</ZoomWindow>
  );

export const deviceHeight = (kind: "laptop" | "monitor" | "window", width: number, k = 1) =>
  kind === "laptop" ? laptopHeight(width, k) : kind === "monitor" ? monitorHeight(width, k) : zoomWindowHeight(width, k);

/** Outer width of a device whose screen is `width` (the laptop's base is wider). */
export const deviceOuterWidth = (kind: "laptop" | "monitor" | "window", width: number, k = 1) =>
  kind === "laptop" ? width + 160 * k : kind === "monitor" ? width + 28 * k : width;

/** Chat messages popping in, bottom-up, like Zoom's chat panel. */
export const ChatStream: React.FC<{
  messages: ChatMessage[];
  every?: number;
  start?: number;
  width?: number;
  max?: number;
  k?: number;
}> = ({ messages, every = 9, start = 0, width = 420, max = 6, k = 1 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const shown = messages
    .map((m, i) => ({ ...m, at: start + i * every }))
    .filter((m) => frame >= m.at)
    .slice(-max);
  return (
    <div style={{ width: width * k, display: "flex", flexDirection: "column", gap: 12 * k, fontFamily: INTER }}>
      {shown.map((m) => {
        const p = spring({ frame: frame - m.at, fps, config: { damping: 13, mass: 0.6 } });
        return (
          <div
            key={m.at}
            style={{
              alignSelf: "flex-start",
              background: "rgba(255,255,255,.96)",
              color: "#111",
              borderRadius: `${18 * k}px ${18 * k}px ${18 * k}px ${4 * k}px`,
              padding: `${12 * k}px ${18 * k}px`,
              boxShadow: "0 10px 30px rgba(0,0,0,.35)",
              transform: `translateX(${(1 - p) * 120}px) scale(${0.85 + 0.15 * p})`,
              opacity: p,
            }}
          >
            <div style={{ fontSize: 16 * k, fontWeight: 800, color: C.blue }}>{m.name}</div>
            <div style={{ fontSize: 24 * k, fontWeight: 600 }}>{m.text}</div>
          </div>
        );
      })}
    </div>
  );
};

export const Label: React.FC<{ children: React.ReactNode; color?: string; delay?: number; k?: number }> = ({
  children,
  color: c = "#ffffff22",
  delay = 0,
  k = 1,
}) => {
  const p = useIn(delay);
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10 * k,
        padding: `${8 * k}px ${16 * k}px`,
        borderRadius: 999,
        background: color(c, c),
        color: C.ink,
        fontFamily: INTER,
        fontWeight: 700,
        fontSize: 22 * k,
        opacity: p,
        transform: `translateY(${(1 - p) * 12}px)`,
        border: "1px solid rgba(255,255,255,.18)",
      }}
    >
      {children}
    </div>
  );
};

/** The product's mark: its icon paths (24×24, Lucide-style strokes) in a rounded gradient tile. */
export const Logo: React.FC<{ product: Product; size?: number }> = ({ product, size = 120 }) => (
  <div
    style={{
      width: size,
      height: size,
      borderRadius: size * 0.26,
      background: `linear-gradient(135deg, ${product.colors[0]}, ${product.colors[1]})`,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      boxShadow: `0 20px 50px ${product.colors[0]}73`,
    }}
  >
    <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      {product.icon_paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  </div>
);

export const Pop: React.FC<{ delay?: number; style?: React.CSSProperties; children: React.ReactNode }> = ({ delay = 0, style, children }) => {
  const p = useIn(delay, 16);
  return <div style={{ ...style, opacity: p, transform: `translateY(${(1 - p) * 60}px) scale(${0.94 + 0.06 * p})` }}>{children}</div>;
};
