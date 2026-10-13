import { BoardView, TOKEN_R } from "./board";
import { counterGroup } from "./counter";
import { POWER_COLOURS, RED, UNIT_NAMES, arrowInk } from "./theme";
import type { Order, Phase, Pt, Unit, UnitType } from "./types";
import { svg } from "./util";

const MIL = new Set(["A", "F"]);
const layerOf = (t: string): "military" | "civil" => (MIL.has(t) ? "military" : "civil");
const OK = ["ok", "success", "succeeded"];

export interface OverlayHandlers {
  onUnitClick(u: Unit, ev: MouseEvent): void;
  onUnitHover(u: Unit | null, ev?: MouseEvent): void;
}

/** Counters and order arrows for one phase. Redraws on zoom (positions depend on block scale). */
export class Overlay {
  private phase: Phase | null = null;
  selected: Unit | null = null;
  hoverOrder: number | null = null;
  /** draw a frame for explicit HOLD orders (play mode drafts) */
  showHolds = false;
  /** the power the player controls: its orders print in red */
  you: string | null = null;

  constructor(
    private view: BoardView,
    private h: OverlayHandlers,
  ) {
    view.onZoom(() => this.draw());
    this.markers();
  }

  setPhase(p: Phase) {
    this.phase = p;
    this.draw();
  }

  private markers() {
    const defs = this.view.svg.querySelector("defs")!;
    const mk = (id: string, colour: string, open = false) => {
      const m = svg("marker", {
        id, viewBox: "0 0 10 10", refX: 6.5, refY: 5, markerWidth: 3.6, markerHeight: 3.6,
        orient: "auto-start-reverse", markerUnits: "strokeWidth",
      }, defs);
      svg("path", {
        d: open ? "M1 1.2L8 5L1 8.8" : "M0 0.6L9.6 5L0 9.4Z",
        fill: open ? "none" : colour, stroke: open ? colour : "#1D1D1B", "stroke-width": open ? 1.7 : 0.7,
        "stroke-linejoin": "miter", "stroke-linecap": "square",
      }, m);
    };
    for (const p of Object.keys(POWER_COLOURS)) {
      mk(`arrow-${p}`, arrowInk(p));
      mk(`arrow-open-${p}`, p === "JPN" ? "#1D1D1B" : arrowInk(p), true);
    }
    mk("arrow-you", RED);
    mk("arrow-open-you", RED, true);
  }

  private ink(power: string) {
    return power === this.you ? RED : arrowInk(power);
  }

  private mkId(power: string, open = false) {
    return `arrow-${open ? "open-" : ""}${power === this.you ? "you" : power}`;
  }

  // ------------------------------------------------------------------ positions
  private unitPos(u: { type: string; at: string }, dislodged = false): Pt {
    const p = this.view.slot(u.at, layerOf(u.type));
    if (!dislodged) return p;
    const f = this.view.f;
    return [p[0] + 9 * f, p[1] - 9 * f];
  }

  private r() {
    return TOKEN_R * this.view.f;
  }

  // ------------------------------------------------------------------ drawing
  draw() {
    const ph = this.phase;
    const L = this.view.layer;
    L.units.replaceChildren();
    L.orders.replaceChildren();
    L.badges.replaceChildren();
    if (!ph) return;
    this.view.svg.classList.toggle("has-you", !!this.you);
    ph.orders.forEach((o, i) => this.drawOrder(o, i, ph));
    for (const u of ph.units) this.drawUnit(u, ph);
    // dislodged units (the record lists them separately)
    for (const d of ph.dislodged ?? []) {
      if (ph.units.some((u) => u.dislodged && u.at === d.at && u.type === d.type && u.power === d.power)) continue;
      if (ph.kind === "retreat") this.drawUnit({ power: d.power, type: d.type, at: d.at, dislodged: true }, ph);
    }
    // builds (winter): ghost counters, die-cut outline only
    for (const o of ph.orders) {
      if (o.kind === "build" && o.unit) {
        const pos = this.unitPos(o.unit);
        const g = this.token(o.unit.type, o.power, pos, L.units, true);
        g.classList.add("ghost-unit");
        const t = svg("title", {}, g);
        t.textContent = `${o.power} builds ${UNIT_NAMES[o.unit.type]} in ${o.unit.at}`;
      }
    }
  }

  private drawUnit(u: Unit, ph: Phase) {
    const pos = this.unitPos(u, !!u.dislodged);
    const g = this.token(u.type, u.power, pos, this.view.layer.units, false, !!u.dislodged);
    g.classList.add("unit");
    if (u.power === this.you) g.classList.add("yours");
    if (u.dislodged) g.classList.add("dislodged");
    if (this.selected && this.selected.at === u.at && this.selected.type === u.type) g.classList.add("selected");
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    const name = `${this.view.map.powers[u.power]?.adjective ?? u.power} ${UNIT_NAMES[u.type]} in ${
      this.view.map.provinces[u.at]?.name ?? u.at}${u.dislodged ? " (dislodged)" : ""}`;
    g.setAttribute("aria-label", name);
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.h.onUnitClick(u, ev);
    });
    g.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        this.h.onUnitClick(u, ev as unknown as MouseEvent);
      }
    });
    g.addEventListener("mouseenter", (ev) => this.h.onUnitHover(u, ev));
    g.addEventListener("mouseleave", () => this.h.onUnitHover(null));
    // badges: COMMONS leaf, disband cross
    const o = ph.orders.find((x) => x.unit && x.unit.at === u.at && x.unit.type === u.type && x.power === u.power);
    if (o?.kind === "commons") this.leafBadge(pos);
    if (o?.kind === "disband") this.crossMark(pos, this.r() * 0.9);
  }

  /** A die-cut counter: power stock, black unit symbol, power code; scaled with the block. */
  token(type: UnitType, power: string, [x, y]: Pt, parent: Element, ghost = false, dislodged = false): SVGGElement {
    const s = (this.r() * 2.06) / 14;
    const g = svg("g", {
      class: "token", transform: `translate(${x} ${y}) scale(${s.toFixed(4)})${dislodged ? " rotate(-14)" : ""}`,
    }, parent);
    counterGroup(type, power, g, { ghost });
    return g;
  }

  private leafBadge([x, y]: Pt) {
    const r = this.r();
    const g = svg("g", { class: "badge leaf", transform: `translate(${x + r * 1.05} ${y - r * 1.05}) scale(${r / 6.5})` },
      this.view.layer.badges);
    svg("circle", { r: 3.4 }, g);
    svg("path", { d: "M-1.7 1.8C-2 -0.9 -0.2 -2.3 2.1 -2.2C2.2 0.2 0.8 2 -1.7 1.8ZM-1.7 1.8L0.9 -0.9" }, g);
    const t = svg("title", {}, g);
    t.textContent = "COMMONS";
  }

  private crossMark([x, y]: Pt, s: number, parent: Element = this.view.layer.badges) {
    const g = svg("g", { class: "fail-x", transform: `translate(${x} ${y})` }, parent);
    svg("rect", { x: -s * 0.62, y: -s * 0.62, width: s * 1.24, height: s * 1.24 }, g);
    svg("path", { d: `M${-s * 0.36} ${-s * 0.36}L${s * 0.36} ${s * 0.36}M${s * 0.36} ${-s * 0.36}L${-s * 0.36} ${s * 0.36}` }, g);
  }

  // ------------------------------------------------------------------ orders
  private drawOrder(o: Order, i: number, ph: Phase) {
    const L = this.view.layer.orders;
    const px = 1 / this.view.k;
    const col = this.ink(o.power);
    const failed = !OK.includes(o.result);
    const g = svg("g", {
      class: `order kind-${o.kind}${failed ? " failed" : ""}${this.hoverOrder === i ? " hot" : ""}${o.power === this.you ? " yours" : ""}${o.power === "JPN" && o.power !== this.you ? " pale" : ""}`,
      "data-index": i,
    }, L);
    const r = this.r();
    const u = o.unit;
    const from = u ? this.unitPos(u) : null;
    const w = 2.6 * px; // screen px
    const end = (to: Pt) => to;

    switch (o.kind) {
      case "move":
      case "retreat": {
        if (!u || !o.to) return;
        const fromP = o.kind === "retreat" ? this.unitPos(u, true) : from!;
        const to = this.view.slot(o.to, layerOf(u.type));
        this.arrow(g, fromP, end(to), {
          colour: col, width: w, startGap: r * 1.15, endGap: r * 1.25, marker: this.mkId(o.power),
          dash: o.kind === "retreat" ? [5 * px, 3 * px] : undefined, bend: 0.12,
        });
        if (o.via === "convoy") g.classList.add("via-convoy");
        if (failed) this.failMark(g, fromP, to, r);
        break;
      }
      case "support": {
        if (!from || !o.target) return;
        if (o.to) {
          const tgtLayer = layerOf(o.target.type);
          const to = this.view.slot(o.to, tgtLayer);
          this.arrow(g, from, to, {
            colour: col, width: w * 0.62, startGap: r * 1.1, endGap: r * 1.7, marker: this.mkId(o.power, true),
            dash: [4.5 * px, 3 * px], bend: -0.18,
          });
          if (failed) this.failMark(g, from, to, r);
        } else {
          const to = this.unitPos(o.target);
          this.arrow(g, from, to, {
            colour: col, width: w * 0.62, startGap: r * 1.1, endGap: r * 1.75, dash: [4.5 * px, 3 * px], bend: -0.1,
          });
          const q = r * 1.5;
          svg("rect", {
            x: to[0] - q, y: to[1] - q, width: q * 2, height: q * 2, class: "support-ring", stroke: col,
            "stroke-width": w * 0.62, "stroke-dasharray": `${4.5 * px} ${3 * px}`,
          }, g);
          if (failed) this.failMark(g, from, to, r);
        }
        break;
      }
      case "convoy": {
        if (!from || !o.target || !o.to) return;
        const a = this.unitPos(o.target);
        const to = this.view.slot(o.to, "military");
        const d = curveThrough([a, from, to], r * 1.2, r * 1.4);
        svg("path", {
          d, class: "convoy-casing", "stroke-width": w * 0.75 + 2 * px,
          "stroke-dasharray": `${0.1 * px} ${4.2 * px}`, "stroke-linecap": "round",
        }, g);
        svg("path", {
          d, class: "convoy", stroke: col, "stroke-width": w * 0.75,
          "stroke-dasharray": `${0.1 * px} ${4.2 * px}`, "stroke-linecap": "round",
        }, g);
        svg("circle", { cx: from[0], cy: from[1], r: r * 1.55, class: "convoy-ring", stroke: col, "stroke-width": w * 0.5 }, g);
        if (failed) this.failMark(g, a, to, r);
        break;
      }
      case "disrupt": {
        if (!from || !o.to) return;
        const to = this.view.centreOf(o.to);
        const d = zigzag(from, to, r * 1.2, r * 0.6, 3.2 * px * 1.4, 2.6 * px * 1.2);
        svg("path", { d, class: "zigzag-casing", "stroke-width": w * 0.8 + 2 * px }, g);
        svg("path", { d, class: "zigzag", stroke: col, "stroke-width": w * 0.8 }, g);
        svg("circle", { cx: to[0], cy: to[1], r: 3.2 * this.view.f, class: "disrupt-target", stroke: col, "stroke-width": w * 0.7 }, g);
        if (failed) this.failMark(g, from, to, r);
        break;
      }
      case "sanction": {
        if (!from || !o.target_power) return;
        const homes = this.view.map.powers[o.target_power]?.homes ?? [];
        let best: Pt | null = null;
        let bd = Infinity;
        for (const h of homes) {
          const c = this.view.centreOf(h);
          const dd = Math.hypot(c[0] - from[0], c[1] - from[1]);
          if (dd < bd) { bd = dd; best = c; }
        }
        if (!best) return;
        this.arrow(g, from, best, { colour: o.power === this.you ? RED : "#4A4740", width: w * 0.5, dash: [3 * px, 2.5 * px], startGap: r * 1.2, endGap: 5.5 * this.view.f, bend: 0.08, cls: "sanction-line" });
        const s = 4.6 * this.view.f;
        const bg = svg("g", { class: "blocked", transform: `translate(${best[0]} ${best[1]})` }, g);
        svg("circle", { r: s }, bg);
        svg("path", { d: `M${-s * 0.7} ${s * 0.7}L${s * 0.7} ${-s * 0.7}` }, bg);
        if (failed) this.failMark(g, from, best, r);
        break;
      }
      case "close": {
        if (!u) return;
        const p = this.view.board.provinces[u.at];
        if (!p) return;
        const el = svg("path", { d: p.d, class: "close-hl", stroke: col, "stroke-width": 2.4 * px }, g);
        el.style.fill = col;
        if (failed) this.crossMark(from!, r * 1.1, g);
        break;
      }
      case "commons":
      case "hold":
      default:
        if (o.kind === "hold" && failed && from) this.crossMark([from[0] + r, from[1] - r], r * 0.7, g);
        else if (o.kind === "hold" && from && this.showHolds) {
          const q = r * 1.45;
          svg("rect", { x: from[0] - q, y: from[1] - q, width: q * 2, height: q * 2, class: "hold-ring", stroke: col, "stroke-width": w * 0.7 }, g);
        }
        break;
    }
    const t = svg("title", {}, g);
    t.textContent = `${o.text}${failed ? ` — ${o.result}` : ""}${o.note ? ` (${o.note})` : ""}`;
    void ph;
  }

  private failMark(g: SVGGElement, a: Pt, b: Pt, r: number) {
    const mid: Pt = [a[0] + (b[0] - a[0]) * 0.62, a[1] + (b[1] - a[1]) * 0.62];
    this.crossMark(mid, r * 0.85, g);
  }

  private arrow(
    g: SVGGElement,
    a: Pt,
    b: Pt,
    o: {
      colour: string; width: number; startGap: number; endGap: number; marker?: string; dash?: number[];
      bend?: number; cls?: string;
    },
  ) {
    const W = this.view.board.width;
    // across the seam: leave one edge and enter the other
    if (Math.abs(b[0] - a[0]) > W / 2) {
      const goingLeft = b[0] > a[0];
      const ym = (a[1] + b[1]) / 2;
      const e1: Pt = [this.view.edgeX(goingLeft ? "left" : "right", ym), ym];
      const e2: Pt = [this.view.edgeX(goingLeft ? "right" : "left", ym), ym];
      this.arrow(g, a, e1, { ...o, endGap: 0, marker: undefined });
      this.arrow(g, e2, b, { ...o, startGap: 0 });
      return;
    }
    const d = arcPath(a, b, o.startGap, o.endGap, o.bend ?? 0.12);
    if (!d) return;
    const px = 1 / this.view.k;
    svg("path", {
      d, class: "arrow-casing", "stroke-width": o.width + 2 * px,
      "stroke-dasharray": o.dash ? o.dash.join(" ") : undefined,
    }, g);
    const p = svg("path", {
      d, class: `arrow ${o.cls ?? ""}`, stroke: o.colour, "stroke-width": o.width,
      "marker-end": o.marker ? `url(#${o.marker})` : undefined,
      "stroke-dasharray": o.dash ? o.dash.join(" ") : undefined,
    }, g);
    void p;
  }
}

// ------------------------------------------------------------------ geometry
function arcPath(a: Pt, b: Pt, g0: number, g1: number, bend: number): string | null {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const L = Math.hypot(dx, dy);
  if (L < g0 + g1 + 1) return null;
  const nx = -dy / L;
  const ny = dx / L;
  const c: Pt = [a[0] + dx / 2 + nx * L * bend, a[1] + dy / 2 + ny * L * bend];
  const p0 = towards(a, c, g0);
  const p1 = towards(b, c, g1);
  return `M${f(p0[0])} ${f(p0[1])}Q${f(c[0])} ${f(c[1])} ${f(p1[0])} ${f(p1[1])}`;
}

function towards(p: Pt, q: Pt, d: number): Pt {
  const L = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1;
  return [p[0] + ((q[0] - p[0]) / L) * d, p[1] + ((q[1] - p[1]) / L) * d];
}

function curveThrough(pts: Pt[], g0: number, g1: number): string {
  const [a, m, b] = pts;
  const p0 = towards(a, m, g0);
  const p1 = towards(b, m, g1);
  return `M${f(p0[0])} ${f(p0[1])}Q${f(m[0])} ${f(m[1])} ${f(p1[0])} ${f(p1[1])}`;
}

function zigzag(a: Pt, b: Pt, g0: number, g1: number, amp: number, step: number): string {
  const p0 = towards(a, b, g0);
  const p1 = towards(b, a, g1);
  const L = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  if (L < 1) return "";
  const ux = (p1[0] - p0[0]) / L;
  const uy = (p1[1] - p0[1]) / L;
  const n = Math.max(2, Math.floor(L / step));
  let d = `M${f(p0[0])} ${f(p0[1])}`;
  for (let i = 1; i < n; i++) {
    const t = (i / n) * L;
    const s = i % 2 ? amp : -amp;
    d += `L${f(p0[0] + ux * t - uy * s)} ${f(p0[1] + uy * t + ux * s)}`;
  }
  return d + `L${f(p1[0])} ${f(p1[1])}`;
}

const f = (v: number) => v.toFixed(2);
