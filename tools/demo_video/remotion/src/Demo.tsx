import React from "react";
import { AbsoluteFill, Audio, Sequence, interpolate, staticFile } from "remotion";
import { Backdrop, C, INTER, SceneFade } from "./ui";
import {
  Brand,
  CropZoom,
  DeviceCaption,
  FullBleed,
  Outro,
  SideBySide,
  SplitWindowMonitor,
  StepsDevice,
  TilesStatement,
  TitleCard,
  WindowStates,
} from "./scenes";
import type { DemoProps, Product, Scene, Track } from "./types";

const SceneBody: React.FC<{ s: Scene; product: Product }> = ({ s, product }) => {
  switch (s.type) {
    case "full_bleed":
      return <FullBleed s={s} />;
    case "tiles_statement":
      return <TilesStatement s={s} />;
    case "title_card":
      return <TitleCard s={s} />;
    case "brand":
      return <Brand s={s} product={product} />;
    case "steps_device":
      return <StepsDevice s={s} />;
    case "split_window_monitor":
      return <SplitWindowMonitor s={s} />;
    case "crop_zoom":
      return <CropZoom s={s} />;
    case "side_by_side":
      return <SideBySide s={s} />;
    case "device_caption":
      return <DeviceCaption s={s} />;
    case "window_states":
      return <WindowStates s={s} />;
    case "outro":
      return <Outro s={s} product={product} />;
  }
};

const TrackAudio: React.FC<{ t: Track }> = ({ t }) => (
  <Sequence from={t.from} durationInFrames={t.dur}>
    <Audio
      src={staticFile(t.src)}
      startFrom={t.start_from}
      volume={(f) =>
        interpolate(f, [0, Math.max(1, t.fade_in), Math.max(2, t.dur - t.fade_out), Math.max(3, t.dur)], [0, t.volume, t.volume, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      }
    />
  </Sequence>
);

export const Demo: React.FC<DemoProps> = ({ product, scenes, tracks }) => (
  <AbsoluteFill style={{ background: C.bg, fontFamily: INTER }}>
    <Backdrop />
    {scenes.map((s) => (
      <Sequence key={s.id} from={s.from} durationInFrames={s.dur}>
        {s.type === "outro" ? (
          <SceneBody s={s} product={product} />
        ) : (
          <SceneFade dur={s.dur} edge={s.fade}>
            <SceneBody s={s} product={product} />
          </SceneFade>
        )}
      </Sequence>
    ))}
    {tracks.map((t, i) => (
      <TrackAudio key={i} t={t} />
    ))}
  </AbsoluteFill>
);
