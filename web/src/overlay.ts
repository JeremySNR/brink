import { BoardView, TOKEN_R } from "./board";
import { POWER_COLOURS, UNIT_NAMES, powerColour } from "./theme";
import type { Order, Phase, Pt, Unit, UnitType } from "./types";
import { svg } from "./util";

const MIL = new Set(["A", "F"]);
const layerOf = (t: string): "military" | "civil" => (MIL.has(t) ? "military" : "civil");

export interface OverlayHandlers {
  onUnitClick(u: Unit, ev: MouseEvent): void;
  onUnitHover(u: Unit | null, ev?: MouseEvent): void;
}

/** Units and order arrows for one phase. Redraws on zoom (positions depend on block scale). */
export class Overlay {
  private phase: Phase | null = null;
  selected: Unit | null = null;
  hoverOrder: number | null = null;

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
        id, viewBox: "0 0 10 10", refX: 7, refY: 5, markerWidth: 4.2, markerHeight: 4.2,
        orient: "auto-start-reverse", markerUnits: "strokeWidth",
      }, defs);
      svg("path", {
        d: open ? "M1 1.5L8 5L1 8.5" : "M0 0.8L9.5 5L0 9.2L2.4 5Z",
        fill: open ? "none" : colour, stroke: colour, "stroke-width": open ? 1.6 : 0.6,
        "stroke-linejoin": "round", "stroke-linecap": "round",
      }, m);
    };
    for (const [p, c] of Object.entries(POWER_COLOURS)) {
      mk(`arrow-${p}`, c);
      mk(`arrow-open-${p}`, c, true);
    }
    mk("arrow-neutral", "#6B7280");
  }

  // ------------------------------------------------------------------ positions
  private unitPos(u: { type: string; at: string }, dislodged = false): Pt {
    const p = this.view.slot(u.at, layerOf(u.type));
    if (!dislodged) return p;
    const f = this.view.f;
    return [p[0] + 7 * f, p[1] - 7 * f];
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
    ph.orders.forEach((o, i) => this.drawOrder(o, i, ph));
    for (const u of ph.units) this.drawUnit(u, ph);
    // dislodged units (the record lists them separately)
    for (const d of ph.dislodged ?? []) {
      if (ph.units.some((u) => u.dislodged && u.at === d.at && u.type === d.type && u.power === d.power)) continue;
      if (ph.kind === "retreat") this.drawUnit({ power: d.power, type: d.type, at: d.at, dislodged: true }, ph);
    }
    // builds (winter): ghost tokens
    for (const o of ph.orders) {
      if (o.kind === "build" && o.unit) {
        const pos = this.unitPos(o.unit);
        const g = this.token(o.unit.type, pos, powerColour(o.power), L.units, true);
        g.classList.add("ghost");
        const t = svg("title", {}, g);
        t.textContent = `${o.power} builds ${UNIT_NAMES[o.unit.type]} in ${o.unit.at}`;
      }
    }
  }

  private drawUnit(u: Unit, ph: Phase) {
    const pos = this.unitPos(u, !!u.dislodged);
    const col = powerColour(u.power);
    const g = this.token(u.type, pos, col, this.view.layer.units, !!u.dislodged);
    g.classList.add("unit");
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
    // badges: COMMONS leaf
    const o = ph.orders.find((x) => x.unit && x.unit.at === u.at && x.unit.type === u.type && x.power === u.power);
    if (o?.kind === "commons") this.leafBadge(pos);
    if (o?.kind === "disband") this.crossMark(pos, this.r() * 0.9, "#1F2933");
  }

  /** Token shapes: Army circle, Fleet triangle, Capital diamond, Influence hexagon. */
  token(type: UnitType, [x, y]: Pt, colour: string, parent: Element, ghost = false): SVGGElement {
    const r = this.r();
    const g = svg("g", { class: "token", transform: `translate(${x} ${y})` }, parent);
    const shapeAttrs = { class: "token-shape", fill: ghost ? "none" : colour, stroke: ghost ? colour : undefined };
    let shape: SVGElement;
    if (type === "A") shape = svg("circle", { r, ...shapeAttrs }, g);
    else {
      let pts: Pt[];
      if (type === "F") pts = [[0, -r * 1.18], [r * 1.12, r * 0.78], [-r * 1.12, r * 0.78]];
      else if (type === "C") pts = [[0, -r * 1.22], [r * 1.22, 0], [0, r * 1.22], [-r * 1.22, 0]];
      else pts = [0, 60, 120, 180, 240, 300].map((a) => {
        const t = ((a + 30) * Math.PI) / 180;
        return [r * 1.1 * Math.cos(t), r * 1.1 * Math.sin(t)] as Pt;
      });
      shape = svg("polygon", { points: pts.map((p) => p.map((v) => v.toFixed(2)).join(",")).join(" "), ...shapeAttrs }, g);
    }
    shape.setAttribute("stroke-linejoin", "round");
    const glyph = svg("text", {
      class: "token-glyph", y: type === "F" ? r * 0.36 : r * 0.36, "font-size": (r * 1.02).toFixed(2),
    }, g);
    glyph.textContent = type;
    if (ghost) glyph.setAttribute("fill", colour);
    return g;
  }

  private leafBadge([x, y]: Pt) {
    const r = this.r();
    const g = svg("g", { class: "badge leaf", transform: `translate(${x + r * 0.95} ${y - r * 0.95}) scale(${r / 6.5})` },
      this.view.layer.badges);
    svg("circle", { r: 3.6 }, g);
    svg("path", { d: "M-1.8 1.9C-2.1 -0.9 -0.2 -2.4 2.2 -2.3C2.3 0.2 0.8 2.1 -1.8 1.9ZM-1.8 1.9L0.9 -0.9" }, g);
    const t = svg("title", {}, g);
    t.textContent = "COMMONS";
  }

  private crossMark([x, y]: Pt, s: number, colour: string, parent: Element = this.view.layer.badges) {
    const g = svg("g", { class: "fail-x", transform: `translate(${x} ${y})` }, parent);
    svg("circle", { r: s * 0.9 }, g);
    svg("path", { d: `M${-s * 0.42} ${-s * 0.42}L${s * 0.42} ${s * 0.42}M${s * 0.42} ${-s * 0.42}L${-s * 0.42} ${s * 0.42}`, stroke: colour }, g);
  }

  // ------------------------------------------------------------------ orders
  private drawOrder(o: Order, i: number, ph: Phase) {
    const L = this.view.layer.orders;
    const px = 1 / this.view.k;
    const col = powerColour(o.power);
    const failed = !["ok", "success", "succeeded"].includes(o.result);
    const g = svg("g", {
      class: `order kind-${o.kind}${failed ? " failed" : ""}${this.hoverOrder === i ? " hot" : ""}`,
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
          colour: col, width: w, startGap: r * 1.15, endGap: r * 1.25, marker: `arrow-${o.power}`,
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
            colour: col, width: w * 0.62, startGap: r * 1.1, endGap: r * 1.7, marker: `arrow-open-${o.power}`,
            dash: [4.5 * px, 3 * px], bend: -0.18,
          });
          if (failed) this.failMark(g, from, to, r);
        } else {
          const to = this.unitPos(o.target);
          this.arrow(g, from, to, {
            colour: col, width: w * 0.62, startGap: r * 1.1, endGap: r * 1.75, dash: [4.5 * px, 3 * px], bend: -0.1,
          });
          svg("circle", {
            cx: to[0], cy: to[1], r: r * 1.6, class: "support-ring", stroke: col,
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
        this.arrow(g, from, best, { colour: "#5F6B7A", width: w * 0.8, startGap: r * 1.2, endGap: 5.5 * this.view.f, bend: 0.08, cls: "sanction-line" });
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
        if (failed) this.crossMark(from!, r * 1.1, "#B42318", g);
        break;
      }
      case "commons":
      case "hold":
      default:
        if (o.kind === "hold" && failed && from) this.crossMark([from[0] + r, from[1] - r], r * 0.7, "#B42318", g);
        break;
    }
    const t = svg("title", {}, g);
    t.textContent = `${o.text}${failed ? ` — ${o.result}` : ""}${o.note ? ` (${o.note})` : ""}`;
    void ph;
  }

  private failMark(g: SVGGElement, a: Pt, b: Pt, r: number) {
    const mid: Pt = [a[0] + (b[0] - a[0]) * 0.62, a[1] + (b[1] - a[1]) * 0.62];
    this.crossMark(mid, r * 0.85, "#B42318", g);
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
    svg("path", { d, class: "arrow-casing", "stroke-width": o.width * 2.1 }, g);
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
