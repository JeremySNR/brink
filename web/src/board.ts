import { select } from "d3-selection";
import "d3-transition";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { currentTheme, homeFill, mix, powerColour, swingFill } from "./theme";
import type { Board, MapData, Phase, Pt } from "./types";
import { clamp, svg } from "./util";

export const TOKEN_R = 6.5;
const MIN_TOKEN_PX = 8.5; // token radius on screen never smaller than this
const MAX_TOKEN_PX = 15; // ... nor larger than this
const LABEL_FONT = 7.4; // map units at label_size 1
const SEA_FONT = 6.0;

interface Block {
  pid: string;
  c: Pt; // block centre (map units)
  g: SVGGElement; // label + centre marker group, scaled by f around c
  label: SVGTextElement | null;
  extra: SVGTextElement[];
  marker: SVGElement | null;
  width: number; // province bbox width (map units)
  textW: number; // label width at scale 1 (map units)
  font: number;
  sea: boolean;
}

export class BoardView {
  readonly svg: SVGSVGElement;
  readonly viewport: SVGGElement;
  readonly layer: Record<string, SVGGElement> = {};
  readonly paths = new Map<string, SVGPathElement>();
  readonly blocks = new Map<string, Block>();
  k = 1;
  f = 1;
  private zoomer: ZoomBehavior<SVGSVGElement, unknown>;
  private fitT: ZoomTransform = zoomIdentity;
  private listeners: (() => void)[] = [];
  private raf = 0;
  private leftEdge: Pt[] = [];
  private rightEdge: Pt[] = [];

  constructor(
    host: HTMLElement,
    readonly board: Board,
    readonly map: MapData,
  ) {
    this.svg = svg("svg", {
      class: "board",
      role: "img",
      "aria-label": "Brink world board",
      tabindex: 0,
    });
    host.appendChild(this.svg);
    this.defs();
    this.viewport = svg("g", { class: "viewport" }, this.svg);
    for (const name of [
      "ocean", "seas", "shallow", "lakes", "seaBorders", "land", "landBorders", "coast", "closures",
      "routes", "wraps", "reach", "labels", "orders", "units", "badges",
    ]) {
      this.layer[name] = svg("g", { class: `layer-${name}` }, this.viewport);
    }
    this.draw();
    this.leftEdge = parsePolyline(board.seam.left);
    this.rightEdge = parsePolyline(board.seam.right);

    this.zoomer = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 40])
      .on("zoom", (ev) => this.applyZoom(ev.transform));
    select(this.svg).call(this.zoomer).on("dblclick.zoom", null);
    select(this.svg).on("dblclick", (ev: MouseEvent) => {
      const pt = this.svg.getBoundingClientRect();
      this.zoomBy(ev.shiftKey ? 0.5 : 2, [ev.clientX - pt.left, ev.clientY - pt.top]);
    });
    new ResizeObserver(() => this.fit(false)).observe(host);
  }

  onZoom(fn: () => void) {
    this.listeners.push(fn);
  }

  // ------------------------------------------------------------------ static drawing
  private defs() {
    const d = svg("defs", {}, this.svg);
    const grad = svg("radialGradient", { id: "ocean-grad", cx: "50%", cy: "42%", r: "75%" }, d);
    svg("stop", { offset: "0", class: "ocean-stop-a" }, grad);
    svg("stop", { offset: "1", class: "ocean-stop-b" }, grad);
    // impassable: diagonal hatch
    const h = svg("pattern", {
      id: "hatch-impassable", width: 4, height: 4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)",
    }, d);
    svg("rect", { width: 4, height: 4, class: "hatch-bg-impassable" }, h);
    svg("line", { x1: 0, y1: 0, x2: 0, y2: 4, class: "hatch-line-impassable" }, h);
    // unaligned swing: fine warm hatch
    const s = svg("pattern", {
      id: "hatch-swing", width: 3, height: 3, patternUnits: "userSpaceOnUse", patternTransform: "rotate(-45)",
    }, d);
    svg("rect", { width: 3, height: 3, class: "hatch-bg-swing" }, s);
    svg("line", { x1: 0, y1: 0, x2: 0, y2: 3, class: "hatch-line-swing" }, s);
    const f = svg("filter", { id: "token-shadow", x: "-60%", y: "-60%", width: "220%", height: "220%" }, d);
    svg("feDropShadow", { dx: 0, dy: 0.6, stdDeviation: 0.7, "flood-color": "#000", "flood-opacity": 0.35 }, f);
  }

  private draw() {
    const { board, map } = this;
    const L = this.layer;
    svg("path", { d: board.outline, class: "ocean" }, L.ocean);
    svg("path", { d: board.graticule, class: "graticule" }, L.ocean);

    const landD: string[] = [];
    for (const [pid, p] of Object.entries(board.provinces)) {
      const mp = map.provinces[pid];
      if (!mp) continue;
      if (mp.kind === "sea") {
        const el = svg("path", {
          d: p.d, class: `sea${mp.chokepoint ? " chokepoint" : mp.ocean ? " ocean-zone" : " marginal"}${mp.opens ? " not-open" : ""}`,
          "data-id": pid,
        }, L.seas);
        this.paths.set(pid, el);
        svg("path", { d: p.d, class: "sea-border" }, L.seaBorders);
      } else {
        landD.push(p.d);
        const el = svg("path", { d: p.d, class: `land ${mp.group}`, "data-id": pid }, L.land);
        this.paths.set(pid, el);
        svg("path", { d: p.d, class: "land-border" }, L.landBorders);
      }
    }
    const all = landD.join("");
    svg("path", { d: all, class: "shallow-wide" }, L.shallow);
    svg("path", { d: all, class: "shallow" }, L.shallow);
    if (board.lakes) svg("path", { d: board.lakes, class: "lake" }, L.lakes);
    if (board.decor) svg("path", { d: board.decor, class: "decor" }, L.lakes);
    svg("path", { d: all, class: "coastline" }, L.coast);

    // wrap indicators at both seam edges
    const seen = new Set<string>();
    for (const wm of board.wrap_marks) {
      const y = Math.round((wm.at[0][1]) / 25) * 25;
      if (seen.has(`${y}`)) continue;
      seen.add(`${y}`);
      const [l, r] = wm.at;
      if (wm.y[1] - wm.y[0] < 8) continue;
      this.wrapMark(l, -1, wm.left === wm.right ? wm.left : `${wm.left}`);
      this.wrapMark(r, 1, wm.right);
    }

    // label / marker blocks
    for (const [pid, p] of Object.entries(board.provinces)) {
      const mp = map.provinces[pid];
      if (!mp) continue;
      const sea = mp.kind === "sea";
      const c: Pt = [p.label[0], p.label[1] + 4.5];
      const g = svg("g", { class: `block${sea ? " sea-block" : ""}`, "data-id": pid }, this.layer.labels);
      let marker: SVGElement | null = null;
      if (p.centre && mp.centre !== "none") {
        marker = svg("circle", { cx: p.centre[0], cy: p.centre[1], r: 2.6, class: "centre-marker" }, g);
      }
      if (mp.chokepoint) {
        // small "gate" icon left of the chokepoint name
        const gi = svg("g", { class: "choke-icon", transform: `translate(${p.label[0]} ${p.label[1] - 7.5})` }, g);
        svg("circle", { r: 3.3 }, gi);
        svg("path", { d: "M-1.6 -1.7V1.7M1.6 -1.7V1.7M-2.4 0H2.4" }, gi);
      }
      const font = sea ? SEA_FONT * clamp(p.label_size, 0.8, 1.3) : LABEL_FONT * p.label_size;
      const text = sea ? mp.name.toUpperCase() : mp.name;
      const label = svg("text", {
        x: p.label[0], y: p.label[1] + font * 0.34, class: sea ? "sea-label" : `land-label ${mp.group}`,
        "font-size": font.toFixed(2),
      }, g);
      label.textContent = text;
      const extra: SVGTextElement[] = [];
      for (const e of p.labels_extra ?? []) {
        const t = svg("text", {
          x: e[0], y: e[1] + font * 0.34, class: "sea-label", "font-size": font.toFixed(2),
        }, this.layer.labels);
        t.textContent = text;
        extra.push(t);
      }
      const bb = this.paths.get(pid)!.getBBox?.() ?? { width: 100 };
      const textW = text.length * font * (sea ? 0.74 : 0.56);
      this.blocks.set(pid, {
        pid, c, g, label, extra, marker, width: bb.width || 100, textW, font, sea,
      });
    }
  }

  private wrapMark(at: Pt, dir: -1 | 1, id: string) {
    const g = svg("g", { class: "wrap-mark", transform: `translate(${at[0] - dir * 7} ${at[1]})` }, this.layer.wraps);
    const s = 2.6;
    let d = "";
    for (const k of [0, 1]) {
      const ox = dir * k * 3;
      d += `M${ox - dir * s} ${-s}L${ox} 0L${ox - dir * s} ${s}`;
    }
    svg("path", { d }, g);
    // text sits inboard of the chevrons: "← continues" on the left edge, "continues →" on the right
    const t = svg("text", { x: -dir * 6, y: 1.3, "text-anchor": dir < 0 ? "start" : "end" }, g);
    t.textContent = dir < 0 ? "← continues" : "continues →";
    const title = svg("title", {}, g);
    title.textContent = `${this.map.provinces[id]?.name ?? id} continues across the board edge`;
  }

  // ------------------------------------------------------------------ phase styling
  paintPhase(phase: Phase) {
    const theme = currentTheme();
    for (const [pid, el] of this.paths) {
      const mp = this.map.provinces[pid];
      if (mp.kind === "sea") continue;
      const owner = phase.ownership[pid] ?? null;
      let fill = "";
      if (mp.impassable) fill = "url(#hatch-impassable)";
      else if (mp.centre === "home") fill = homeFill(owner ?? mp.home_of!, theme);
      else if (mp.centre === "swing") fill = owner ? swingFill(owner, theme) : "url(#hatch-swing)";
      el.style.fill = fill;
      el.classList.toggle("aligned", mp.centre === "swing" && !!owner);
      el.classList.toggle("not-open", !!mp.opens && phase.year < mp.opens);
      const b = this.blocks.get(pid);
      if (b?.marker) {
        const m = b.marker as SVGCircleElement;
        m.classList.remove("home", "aligned", "unaligned");
        if (mp.centre === "home") {
          m.classList.add("home");
          m.style.fill = powerColour(owner ?? mp.home_of);
          m.style.stroke = "";
          m.setAttribute("r", "2.6");
        } else if (owner) {
          m.classList.add("aligned");
          m.style.fill = "var(--marker-ring-fill)";
          m.style.stroke = powerColour(owner);
          m.setAttribute("r", "2.9");
        } else {
          m.classList.add("unaligned");
          m.style.fill = "";
          m.style.stroke = "";
          m.setAttribute("r", "2.4");
        }
      }
      if (b?.label) b.label.classList.toggle("on-home", mp.centre === "home");
    }
    for (const [pid, el] of this.paths) {
      const mp = this.map.provinces[pid];
      if (mp.kind !== "sea") continue;
      el.classList.toggle("not-open", !!mp.opens && phase.year < mp.opens);
    }
    // closures
    const cl = this.layer.closures;
    cl.replaceChildren();
    for (const [z, against] of Object.entries(phase.closed || {})) {
      const p = this.board.provinces[z];
      if (!p) continue;
      const closer = phase.units.find((u) => u.at === z && u.type === "F")?.power;
      const col = powerColour(closer);
      const el = svg("path", { d: p.d, class: "closure" }, cl);
      el.style.fill = col;
      el.style.stroke = col;
      const t = svg("title", {}, el);
      t.textContent = `${this.map.provinces[z].name} closed${closer ? ` by ${closer}` : ""}${
        against.length ? ` to ${against.join(", ")}` : ""}`;
    }
    // routes
    const rl = this.layer.routes;
    rl.replaceChildren();
    for (const [rid, r] of Object.entries(this.board.routes)) {
      const notOpen = !!r.opens && phase.year < r.opens;
      const blocked = r.chokepoints.some((c) => (phase.closed || {})[c]);
      const g = svg("g", { class: `route${notOpen ? " not-open" : ""}${blocked ? " blocked" : ""}`, "data-id": rid }, rl);
      svg("path", { d: r.d, class: "route-casing" }, g);
      svg("path", { d: r.d, class: "route-line" }, g);
      const t = svg("title", {}, g);
      t.textContent = `${r.name} trade route: ${r.ends[0]} ↔ ${r.ends[1]} via ${r.path.join(" · ")}${
        notOpen ? ` (opens ${r.opens})` : ""}${blocked ? " (a chokepoint on it is closed)" : ""}`;
      for (const [a, b] of r.seam_points) {
        for (const p of [a, b]) {
          const dir = p[0] < this.board.width / 2 ? -1 : 1;
          const s = 2.4;
          let d = "";
          for (const k of [0, 1]) {
            const ox = p[0] - dir * 4 + dir * k * 2.8;
            d += `M${ox - dir * s} ${p[1] - s}L${ox} ${p[1]}L${ox - dir * s} ${p[1] + s}`;
          }
          svg("path", { d, class: "route-chevron" }, g);
        }
      }
    }
  }

  // ------------------------------------------------------------------ geometry helpers
  /** Block-scaled slot position for a unit layer in a province. */
  slot(pid: string, layer: "military" | "civil"): Pt {
    const p = this.board.provinces[pid];
    const b = this.blocks.get(pid);
    if (!p || !b) return [0, 0];
    const s = (layer === "civil" ? p.slots.civil : null) ?? p.slots.military;
    return [b.c[0] + (s[0] - b.c[0]) * this.f, b.c[1] + (s[1] - b.c[1]) * this.f];
  }

  centreOf(pid: string): Pt {
    const b = this.blocks.get(pid);
    return b ? b.c : [0, 0];
  }

  /** x of the left/right board edge at a given y. */
  edgeX(side: "left" | "right", y: number): number {
    const pts = side === "left" ? this.leftEdge : this.rightEdge;
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1];
      const [x1, y1] = pts[i];
      if ((y >= y0 && y <= y1) || (y >= y1 && y <= y0)) {
        const t = y1 === y0 ? 0 : (y - y0) / (y1 - y0);
        return x0 + t * (x1 - x0);
      }
    }
    return side === "left" ? 0 : this.board.width;
  }

  // ------------------------------------------------------------------ zoom
  private applyZoom(t: ZoomTransform) {
    this.viewport.setAttribute("transform", t.toString());
    this.k = t.k;
    this.svg.style.setProperty("--px", String(1 / t.k));
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(() => this.afterZoom());
  }

  private afterZoom() {
    const k = this.k;
    // tokens keep a comfortable screen size: grow when zoomed out, shrink back when zoomed far in
    const tokPx = TOKEN_R * k;
    const f = clamp(clamp(tokPx, MIN_TOKEN_PX, MAX_TOKEN_PX) / tokPx, 0.35, 3.2);
    this.f = f;
    // labels: natural size, clamped to a readable screen range that grows a little with importance;
    // then greedy collision culling (homes and big provinces first)
    const cands: { b: Block; fl: number; rect: [number, number, number, number]; pri: number }[] = [];
    for (const b of this.blocks.values()) {
      const natural = b.font * k;
      const ls = b.font / (b.sea ? SEA_FONT : LABEL_FONT);
      const want = clamp(natural * 1.2, 8, 10.5 + 4.5 * ls);
      const fl = clamp(want / natural, 0.3, 2.2);
      const tr = Math.abs(fl - 1) < 1e-3 ? "" : `translate(${b.c[0]} ${b.c[1]}) scale(${fl}) translate(${-b.c[0]} ${-b.c[1]})`;
      b.g.setAttribute("transform", tr);
      if (!b.label) continue;
      const screenFont = natural * fl;
      const fits = b.width * k >= b.textW * k * fl * (b.sea ? 0.85 : 0.95);
      const big = b.sea ? screenFont >= 7 : screenFont >= 7.5;
      b.label.classList.add("hidden");
      for (const e of b.extra) e.classList.add("hidden");
      if (!(big && fits)) continue;
      // label rect in map units (label anchor is 4.5 above the block centre, scaled about it)
      const lx = Number(b.label.getAttribute("x"));
      const ly = b.c[1] + (Number(b.label.getAttribute("y")) - b.font * 0.34 - b.c[1]) * fl;
      const w = b.textW * fl;
      const h = b.font * fl * 1.05;
      const mp = this.map.provinces[b.pid];
      const pri = (mp.centre === "home" ? 3e7 : mp.centre === "swing" ? 2e7 : b.sea ? 0 : 1e7) + b.width;
      cands.push({ b, fl, rect: [lx - w / 2, ly - h / 2, lx + w / 2, ly + h / 2], pri });
    }
    cands.sort((a, z) => z.pri - a.pri);
    const placed: [number, number, number, number][] = [];
    const pad = 2 / k;
    for (const c of cands) {
      const [x0, y0, x1, y1] = c.rect;
      if (placed.some(([a0, b0, a1, b1]) => x0 < a1 + pad && x1 > a0 - pad && y0 < b1 + pad && y1 > b0 - pad)) continue;
      placed.push(c.rect);
      c.b.label!.classList.remove("hidden");
      for (const e of c.b.extra) {
        const x = Number(e.getAttribute("x"));
        const y = Number(e.getAttribute("y"));
        e.classList.toggle("hidden", f > 1.6);
        e.setAttribute("transform", Math.abs(c.fl - 1) < 1e-3 ? "" : `translate(${x} ${y}) scale(${c.fl}) translate(${-x} ${-y})`);
      }
    }
    this.svg.classList.toggle("zoomed-out", k * f < 1.1);
    this.svg.classList.toggle("zoomed-in", k > 2.2);
    for (const fn of this.listeners) fn();
  }

  fit(animate = true) {
    const r = this.svg.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const pad = 12;
    let k = Math.min((r.width - pad * 2) / this.board.width, (r.height - pad * 2) / this.board.height);
    const minK = k;
    // portrait / narrow screens: start zoomed so the board fills more of the height (pan to see the rest)
    if (r.height > r.width * 0.9) k = Math.min((r.height * 0.62) / this.board.height, k * 2.6);
    const x = (r.width - this.board.width * k) / 2;
    const y = (r.height - this.board.height * k) / 2;
    const wasFit = Math.abs(this.k - this.fitT.k) < 1e-6 || this.fitT.k === 1;
    this.fitT = zoomIdentity.translate(x, y).scale(k);
    this.zoomer
      .scaleExtent([minK * 0.9, minK * 14])
      .translateExtent([[-this.board.width * 0.15, -this.board.height * 0.2], [this.board.width * 1.15, this.board.height * 1.2]]);
    const sel = select(this.svg);
    if (animate) sel.transition().duration(450).call(this.zoomer.transform, this.fitT);
    else if (wasFit) sel.call(this.zoomer.transform, this.fitT);
  }

  zoomBy(factor: number, at?: [number, number]) {
    const sel = select(this.svg);
    if (at) sel.transition().duration(300).call(this.zoomer.scaleBy, factor, at);
    else sel.transition().duration(300).call(this.zoomer.scaleBy, factor);
  }

  zoomTo(pid: string) {
    const el = this.paths.get(pid);
    if (!el) return;
    const bb = el.getBBox();
    const r = this.svg.getBoundingClientRect();
    const k = clamp(Math.min(r.width / (bb.width * 3), r.height / (bb.height * 3)), this.fitT.k, this.fitT.k * 6);
    const t = zoomIdentity.translate(r.width / 2, r.height / 2).scale(k).translate(-(bb.x + bb.width / 2), -(bb.y + bb.height / 2));
    select(this.svg).transition().duration(500).call(this.zoomer.transform, t);
  }

  tint(hex: string, a: number) {
    return mix(hex, a);
  }
}

function parsePolyline(d: string): Pt[] {
  const nums = (d.match(/-?\d+(\.\d+)?/g) || []).map(Number);
  const out: Pt[] = [];
  for (let i = 0; i + 1 < nums.length; i += 2) out.push([nums[i], nums[i + 1]]);
  return out;
}
