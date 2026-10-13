// Die-cut counters: one drawing used on the map, in the player aid, the key and the start screen.
// Canonical size: a 14-unit square centred on 0,0; callers scale it.
import { INK, edgeColour, powerColour } from "./theme";
import type { UnitType } from "./types";
import { svg } from "./util";

export const SIDE = 14;
const H = SIDE / 2;
const EDGE = 1.3; // visible counter thickness (bottom and right), canonical units

/** Black unit symbol, authored at one stroke weight (0.95 units). */
export function glyph(type: UnitType, parent: Element, colour = INK): SVGGElement {
  const g = svg("g", { class: "ctr-glyph", fill: "none", stroke: colour, "stroke-width": 0.95, "stroke-linejoin": "round", "stroke-linecap": "round" }, parent);
  switch (type) {
    case "A": // NATO infantry: a box with an X
      svg("rect", { x: -4.4, y: -5.2, width: 8.8, height: 5.6 }, g);
      svg("path", { d: "M-4.4 -5.2L4.4 0.4M4.4 -5.2L-4.4 0.4" }, g);
      break;
    case "F": // a ship: hull and superstructure
      svg("path", { d: "M-5 -1.6H5L3.3 0.9H-3.3Z", fill: colour, stroke: "none" }, g);
      svg("path", { d: "M-2.4 -1.6V-3.4H0.9V-1.6M-0.6 -3.4V-5.1", fill: "none" }, g);
      break;
    case "C": { // stacked ingot bars
      const ingot = (cx: number, cy: number) =>
        `M${cx - 2.3} ${cy + 1}L${cx - 1.6} ${cy - 1}H${cx + 1.6}L${cx + 2.3} ${cy + 1}Z`;
      svg("path", { d: ingot(-2.45, -0.2) + ingot(2.45, -0.2) + ingot(0, -2.6), fill: colour, stroke: "none" }, g);
      break;
    }
    case "I": // broadcast arcs over a mast
      svg("path", { d: "M-1.9 -2.3A2.6 2.6 0 0 1 1.9 -2.3M-3.5 -3.9A4.9 4.9 0 0 1 3.5 -3.9" }, g);
      svg("path", { d: "M0 -1.2V0.6", }, g);
      svg("circle", { cx: 0, cy: -1.4, r: 0.85, fill: colour, stroke: "none" }, g);
      break;
  }
  return g;
}

/** A counter group at the origin (canonical units). `code` prints the power code under the symbol. */
export function counterGroup(type: UnitType, power: string, parent: Element, opts: { ghost?: boolean; code?: boolean } = {}): SVGGElement {
  const g = svg("g", { class: `ctr p-${power}${opts.ghost ? " ghost" : ""}` }, parent);
  const col = powerColour(power);
  if (!opts.ghost) {
    svg("rect", { class: "ctr-edge", x: -H + EDGE, y: -H + EDGE, width: SIDE, height: SIDE, fill: edgeColour(power) }, g);
  }
  svg("rect", { class: "ctr-face", x: -H, y: -H, width: SIDE, height: SIDE, fill: col }, g);
  glyph(type, g);
  if (opts.code !== false) {
    const t = svg("text", { class: "ctr-code", x: 0, y: 5.25 }, g);
    t.textContent = power;
  }
  return g;
}

/** A standalone counter <svg> for HTML (player aid, key, start screen). */
export function counterSvg(type: UnitType, power: string, px = 26, cls = "counter"): SVGSVGElement {
  const pad = EDGE + 0.6;
  const s = svg("svg", {
    class: cls, viewBox: `${-H - 0.6} ${-H - 0.6} ${SIDE + pad + 0.6} ${SIDE + pad + 0.6}`,
    width: px, height: px, "aria-hidden": "true",
  });
  counterGroup(type, power, s);
  return s;
}

/** A plain swatch counter with only the power code (supply-centre rows, control markers in the key). */
export function codeCounter(power: string, px = 26, cls = "counter code-only"): SVGSVGElement {
  const pad = EDGE + 0.6;
  const s = svg("svg", {
    class: cls, viewBox: `${-H - 0.6} ${-H - 0.6} ${SIDE + pad + 0.6} ${SIDE + pad + 0.6}`,
    width: px, height: px, "aria-hidden": "true",
  });
  const g = svg("g", { class: `ctr p-${power}` }, s);
  svg("rect", { class: "ctr-edge", x: -H + EDGE, y: -H + EDGE, width: SIDE, height: SIDE, fill: edgeColour(power) }, g);
  svg("rect", { class: "ctr-face", x: -H, y: -H, width: SIDE, height: SIDE, fill: powerColour(power) }, g);
  const t = svg("text", { class: "ctr-code big", x: 0, y: 2.1 }, g);
  t.textContent = power;
  return s;
}
