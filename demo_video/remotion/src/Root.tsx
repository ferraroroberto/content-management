import React from "react";
import { Composition } from "remotion";
import { Demo } from "./Demo";
import type { DemoProps } from "./types";

// One composition; its size, fps and length come from the props
// demo_video/storyboard.py resolves for a cut (--props).
const EMPTY: DemoProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  durationInFrames: 30,
  product: { name: "", icon_paths: [], colors: ["#e53935", "#8e24aa"] },
  scenes: [],
  tracks: [],
};

export const Root: React.FC = () => (
  <Composition
    id="Demo"
    component={Demo}
    defaultProps={EMPTY}
    durationInFrames={EMPTY.durationInFrames}
    fps={EMPTY.fps}
    width={EMPTY.width}
    height={EMPTY.height}
    calculateMetadata={({ props }) => ({
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);
