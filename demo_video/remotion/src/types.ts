// The resolved props demo_video/storyboard.py writes for one cut: copy keys are
// already strings, clip refs are already {src, from, rate}. Nothing here reads
// demo.json directly.

export type Crop = { x: number; y: number; w: number; h: number };

/** A recorded clip: `src` relative to the demo folder, `from` in source seconds. */
export type ClipRef = { src: string; from: number; rate: number; crop?: Crop };

export type CaptionSpec = {
  kicker?: string;
  title: string;
  sub?: string;
  color?: string;
  size?: number;
  width?: number;
};

export type ChatMessage = { name: string; text: string };

type Base = { id: string; from: number; dur: number; fade: number };

export type FullBleedScene = Base & {
  type: "full_bleed";
  clip: ClipRef;
  caption: CaptionSpec;
  zoom?: [number, number];
  focus?: string;
  chat?: { messages: ChatMessage[]; every_s: number; start_s: number; max: number };
};

export type TilesStatementScene = Base & {
  type: "tiles_statement";
  tiles: string[];
  chats: string[];
  talkers: number[];
  before: CaptionSpec;
  after: CaptionSpec;
  swap_s: number;
};

export type TitleCardScene = Base & { type: "title_card"; caption: CaptionSpec };

export type BrandScene = Base & { type: "brand"; sub: string };

export type StepsDeviceScene = Base & {
  type: "steps_device";
  steps: { title: string; sub: string }[];
  /** Source seconds (in `clip`) at which steps 2..n become active. */
  step_at: number[];
  device: "laptop" | "monitor" | "window";
  clip: ClipRef;
};

export type SplitScene = Base & {
  type: "split_window_monitor";
  caption: CaptionSpec;
  window: { label: string; clip: ClipRef };
  monitor: { label: string; clip: ClipRef };
};

export type CropZoomScene = Base & {
  type: "crop_zoom";
  caption: CaptionSpec;
  panel: ClipRef;
  window: ClipRef;
};

export type SideBySideScene = Base & {
  type: "side_by_side";
  caption: CaptionSpec;
  windows: ClipRef[];
  stagger_s: number;
};

export type DeviceCaptionScene = Base & {
  type: "device_caption";
  caption: CaptionSpec;
  device: "laptop" | "monitor" | "window";
  clip: ClipRef;
};

export type WindowStatesScene = Base & {
  type: "window_states";
  caption: CaptionSpec;
  segments: { clip: ClipRef; seconds: number | null }[];
  legend: {
    labels: string[];
    colors: string[];
    /** Which segment the legend follows; before it, the legend is dim. */
    segment: number;
    /** Beat start of that segment's clip, in source seconds. */
    anchor: number;
    /** [seconds after the anchor, state index], ascending. */
    changes: [number, number][];
  };
  speed_chip: boolean;
};

export type OutroScene = Base & {
  type: "outro";
  clip: ClipRef;
  tagline: string;
  footer: string;
};

export type Scene =
  | FullBleedScene
  | TilesStatementScene
  | TitleCardScene
  | BrandScene
  | StepsDeviceScene
  | SplitScene
  | CropZoomScene
  | SideBySideScene
  | DeviceCaptionScene
  | WindowStatesScene
  | OutroScene;

export type Track = {
  src: string;
  /** Composition frames. */
  from: number;
  dur: number;
  /** Source frames to skip. */
  start_from: number;
  volume: number;
  fade_in: number;
  fade_out: number;
};

export type Product = { name: string; icon_paths: string[]; colors: [string, string] };

export type DemoProps = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  product: Product;
  scenes: Scene[];
  tracks: Track[];
};
