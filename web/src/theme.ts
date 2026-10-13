// Power palette: Okabe-Ito derived (colour-blind aware), darkened so white glyphs stay legible.
// Keep in sync with src/brink/geo/preview.py.
export const POWER_ORDER = ["USA", "CHN", "EU", "RUS", "IND", "JPN", "GLF"] as const;

export const POWER_COLOURS: Record<string, string> = {
  USA: "#1F6FB2",
  CHN: "#C8501E",
  EU: "#3E95D1",
  RUS: "#566079",
  IND: "#C98A08",
  JPN: "#B45E91",
  GLF: "#0F8A68",
};

export const UNIT_NAMES: Record<string, string> = {
  A: "Army",
  F: "Fleet",
  C: "Capital",
  I: "Influence",
};

export function powerColour(p: string | null | undefined): string {
  return (p && POWER_COLOURS[p]) || "#8A8F98";
}

/** Mix a hex colour towards another (default white). */
export function mix(hex: string, amount: number, base = "#FFFFFF"): string {
  const a = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const b = [1, 3, 5].map((i) => parseInt(base.slice(i, i + 2), 16));
  return "#" + a.map((x, i) => Math.round(x + (b[i] - x) * amount).toString(16).padStart(2, "0")).join("");
}

export type ThemeName = "light" | "dark";

export function currentTheme(): ThemeName {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/** Province fills per theme (CSS variables can't be used inside some SVG paint servers reliably). */
export function homeFill(owner: string, theme: ThemeName): string {
  // dark theme: lift the colour a little so dark powers (Russia) still read against dark land
  return theme === "dark" ? mix(POWER_COLOURS[owner], 0.2, "#FFFFFF") : mix(POWER_COLOURS[owner], 0.12);
}

export function swingFill(owner: string, theme: ThemeName): string {
  return theme === "dark" ? mix(POWER_COLOURS[owner], 0.5, "#222A36") : mix(POWER_COLOURS[owner], 0.58);
}
