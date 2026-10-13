// Wargame Box palette: printed counter stock and map-sheet inks.
// Ownership is never shown by colour alone: every counter and control marker carries the power code.
// (src/brink/geo/preview.py keeps its own older palette for static previews.)
export const POWER_ORDER = ["USA", "CHN", "EU", "RUS", "IND", "JPN", "GLF"] as const;

/** Counter stock (the full-strength printed colour of each power's counters). */
export const POWER_COLOURS: Record<string, string> = {
  USA: "#8FAFC6",
  CHN: "#D9776A",
  EU: "#E6CB5A",
  RUS: "#B0A794",
  IND: "#E7A35A",
  JPN: "#F4F1E8",
  GLF: "#84A56C",
};

export const UNIT_NAMES: Record<string, string> = {
  A: "Army",
  F: "Fleet",
  C: "Capital",
  I: "Influence",
};

// map-sheet inks (mirrored as CSS tokens in style.css)
export const INK = "#1D1D1B";
export const PAPER = "#F2EEE2";
export const BUFF = "#E4DCC2";
export const RED = "#B3261E";

export function powerColour(p: string | null | undefined): string {
  return (p && POWER_COLOURS[p]) || "#C9C4B6";
}

/** Mix a hex colour towards another (default white). */
export function mix(hex: string, amount: number, base = "#FFFFFF"): string {
  const a = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const b = [1, 3, 5].map((i) => parseInt(base.slice(i, i + 2), 16));
  return "#" + a.map((x, i) => Math.round(x + (b[i] - x) * amount).toString(16).padStart(2, "0")).join("");
}

/** The counter's die-cut edge: the same stock, darker (Japan's white stock has a black edge). */
export function edgeColour(p: string | null | undefined): string {
  if (p === "JPN") return "#3A3833";
  return mix(powerColour(p), 0.5, INK);
}

/** Order-arrow ink for a power: its counter colour, darkened a step so it reads on the sheet. */
export function arrowInk(p: string | null | undefined): string {
  if (p === "JPN") return "#F4F1E8";
  return mix(powerColour(p), 0.2, INK);
}

/** Flat printed tint for a power's home provinces. */
export function homeFill(owner: string): string {
  if (owner === "JPN") return "#FBFAF5";
  return mix(POWER_COLOURS[owner] ?? "#C9C4B6", 0.3, PAPER);
}

/** Lighter tint for a swing state aligned to a power. */
export function swingFill(owner: string): string {
  if (owner === "JPN") return "#F6F3EA";
  return mix(POWER_COLOURS[owner] ?? "#C9C4B6", 0.58, BUFF);
}
