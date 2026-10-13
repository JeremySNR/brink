import { select } from "d3-selection";
import "d3-transition";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { edgeColour, homeFill, mix, powerColour, swingFill } from "./theme";
import type { Board, MapData, Phase, Pt } from "./types";
import { clamp, svg } from "./util";

export const TOKEN_R = 6.5;
const MIN_TOKEN_PX = 10; // token radius on screen never smaller than this
const MAX_TOKEN_PX = 16; // ... nor larger than this
const LABEL_FONT = 7.6; // map units at label_size 1
const SEA_FONT = 6.0;

interface Block {
  pid: string;
  c: Pt; // block centre (map units)
  g: SVGGElement; // label + centre marker group, scaled by f around c
  label: SVGTextElement | null;
  extra: SVGTextElement[];
  marker: SVGElement | null;
  ring: SVGCircleElement | null;
  control: SVGGElement | null;
  controlText: SVGTextElement | null;
  controlFace: SVGRectElement | null;
  controlEdge: SVGRectElement | null;
  controlSplit: SVGRectElement | null;
  choke: SVGGElement | null;
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
    const hatch = (id: string, size: number, angle: number, bg: string | null, line: string, w: number) => {
      const p = svg("pattern", {
        id, width: size, height: size, patternUnits: "userSpaceOnUse", patternTransform: `rotate(${angle})`,
      }, d);
      if (bg) svg("rect", { width: size, height: size, class: bg }, p);
      svg("line", { x1: 0, y1: 0, x2: 0, y2: size, class: line, "stroke-width": w }, p);
    };
    // impassable: grey stock with a fine diagonal hatch
    hatch("hatch-impassable", 2.6, 45, "hatch-bg-impassable", "hatch-line-impassable", 0.55);
    // not yet open (seas and land): a sparse grey overprint
    hatch("hatch-closed-sea", 3.2, -45, null, "hatch-line-notopen", 0.5);
    // legal destinations: printed overprint hatch (red for your own unit, ink in replay)
    hatch("hatch-reach-red", 3, 45, null, "hatch-line-red", 0.8);
    hatch("hatch-reach-ink", 3, 45, null, "hatch-line-ink", 0.7);
  }

  private draw() {
    const { board, map } = this;
    const L = this.layer;
    svg("path", { d: board.outline, class: "ocean" }, L.ocean);
    svg("path", { d: board.graticule, class: "graticule" }, L.ocean);
    svg("path", { d: board.outline, class: "sheet-edge" }, L.wraps);

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
      let ring: SVGCircleElement | null = null;
      let control: SVGGElement | null = null;
      let controlText: SVGTextElement | null = null;
      let controlFace: SVGRectElement | null = null;
      let controlEdge: SVGRectElement | null = null;
      let controlSplit: SVGRectElement | null = null;
      let choke: SVGGElement | null = null;
      if (p.centre && mp.centre !== "none") {
        // supply centre: a printed ring (unaligned swing) or dot (home), and a control marker
        // (a small square counter with the owner's code) when a power holds it
        const [mx, my] = p.centre;
        marker = svg("g", { class: "centre", transform: `translate(${mx} ${my})` }, g);
        ring = svg("circle", { r: 2.5, class: "centre-ring" }, marker);
        control = svg("g", { class: "control" }, marker);
        controlEdge = svg("rect", { x: -5.6, y: -3.4, width: 12, height: 7.2, class: "control-edge" }, control);
        controlFace = svg("rect", { x: -6.2, y: -4, width: 12, height: 7.2, class: "control-face" }, control);
        // contested: the occupier's colour printed on the marker's right half
        controlSplit = svg("rect", { x: -0.2, y: -4, width: 6, height: 7.2, class: "control-split" }, control);
        controlText = svg("text", { x: -0.2, y: 1.75, class: "control-code" }, control);
      }
      if (mp.chokepoint) {
        // printed chokepoint symbol: a gate across a channel
        choke = svg("g", { class: "choke-icon", transform: `translate(${p.label[0]} ${p.label[1] - 7.5})` }, g);
        svg("rect", { x: -3.4, y: -3.4, width: 6.8, height: 6.8, transform: "rotate(45)" }, choke);
        svg("path", { d: "M-1.5 -1.9V1.9M1.5 -1.9V1.9", class: "gate" }, choke);
        svg("path", { d: "M-3.2 0H3.2", class: "bar" }, choke);
      }
      const font = sea ? SEA_FONT * clamp(p.label_size, 0.8, 1.3) : LABEL_FONT * p.label_size;
      const text = mp.name.toUpperCase();
      const label = svg("text", {
        x: p.label[0], y: p.label[1] + font * 0.34, class: sea ? "sea-label" : `land-label ${mp.group}`,
        "font-size": font.toFixed(2),
      }, g);
      label.textContent = text;
      // the province code the order notation uses (A ROM - BER), printed small beside the name
      const code = mp.impassable ? "" : pid;
      const addCode = (t: SVGTextElement) => {
        if (!code) return;
        const ts = svg("tspan", { class: "pcode", dx: (font * 0.32).toFixed(2) }, t);
        ts.textContent = code;
      };
      addCode(label);
      const extra: SVGTextElement[] = [];
      for (const e of p.labels_extra ?? []) {
        const t = svg("text", {
          x: e[0], y: e[1] + font * 0.34, class: "sea-label", "font-size": font.toFixed(2),
        }, this.layer.labels);
        t.textContent = text;
        addCode(t);
        extra.push(t);
      }
      const bb = this.paths.get(pid)!.getBBox?.() ?? { width: 100 };
      const textW = text.length * font * (sea ? 0.8 : 0.52) + (code ? (code.length * 0.38 + 0.4) * font : 0);
      this.blocks.set(pid, {
        pid, c, g, label, extra, marker, ring, control, controlText, controlFace, controlEdge, controlSplit, choke,
        width: bb.width || 100, textW, font, sea,
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
    // the word sits inboard of the drawn chevrons
    const t = svg("text", { x: -dir * 6, y: 1.3, "text-anchor": dir < 0 ? "start" : "end" }, g);
    t.textContent = "continues";
    const title = svg("title", {}, g);
    title.textContent = `${this.map.provinces[id]?.name ?? id} continues across the board edge`;
  }

  // ------------------------------------------------------------------ phase styling
  paintPhase(phase: Phase) {
    for (const [pid, el] of this.paths) {
      const mp = this.map.provinces[pid];
      if (mp.kind === "sea") continue;
      const owner = phase.ownership[pid] ?? null;
      let fill = "";
      if (mp.impassable) fill = "url(#hatch-impassable)";
      else if (mp.centre === "home") fill = homeFill(owner ?? mp.home_of!);
      else if (mp.centre === "swing" && owner) fill = swingFill(owner);
      el.style.fill = fill;
      el.classList.toggle("aligned", mp.centre === "swing" && !!owner);
      el.classList.toggle("not-open", !!mp.opens && phase.year < mp.opens);
      const b = this.blocks.get(pid);
      if (b?.label) {
        b.label.classList.toggle("on-home", mp.centre === "home");
        // knock-out halo in the province's own printed colour: invisible except where linework crosses
        b.label.style.stroke = fill && !fill.startsWith("url") ? fill : "";
      }
      if (b?.marker) {
        const held = mp.centre === "home" ? (owner && owner !== mp.home_of ? owner : null) : owner;
        b.marker.classList.toggle("home", mp.centre === "home");
        b.marker.classList.toggle("held", !!held);
        if (b.ring) b.ring.style.fill = mp.centre === "home" ? powerColour(owner ?? mp.home_of) : "";
        if (held && b.controlText && b.controlFace && b.controlEdge) {
          b.controlText.textContent = held;
          b.controlFace.style.fill = powerColour(held);
          b.controlEdge.style.fill = edgeColour(held);
        }
        // contested: an aligned swing state with another power's unit in either slot pays no supply
        const occupier = mp.centre === "swing" && owner
          ? phase.units.find((u) => u.at.split("/")[0] === pid && u.power !== owner && !u.dislodged)?.power
          : undefined;
        b.marker.classList.toggle("contested", !!occupier);
        el.classList.toggle("contested", !!occupier);
        if (b.controlSplit) b.controlSplit.style.fill = occupier ? powerColour(occupier) : "";
      }
    }
    for (const [pid, el] of this.paths) {
      const mp = this.map.provinces[pid];
      if (mp.kind !== "sea") continue;
      el.classList.toggle("not-open", !!mp.opens && phase.year < mp.opens);
      this.blocks.get(pid)?.choke?.classList.toggle("closed", !!(phase.closed || {})[pid]);
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
      svg("path", { d: r.d, class: "route-line" }, g);
      const t = svg("title", {}, g);
      t.textContent = `${r.name} trade route: ${r.ends[0]} to ${r.ends[1]} via ${r.path.join(", ")}${
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
      // province codes print only once they are legible (9px on screen)
      const showCode = b.font * fl * k * 0.62 >= 9;
      b.label.classList.toggle("show-code", showCode);
      for (const e of b.extra) e.classList.toggle("show-code", showCode);
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

  /** Board y of a latitude (Equal Earth, as projected by the geo pipeline: x scale 369.5, equator at 483.3). */
  private latY(lat: number): number {
    const th = Math.asin((Math.sqrt(3) / 2) * Math.sin((lat * Math.PI) / 180));
    const t2 = th * th, t6 = t2 * t2 * t2;
    const y = th * (1.340264 - 0.081106 * t2 + t6 * (0.000893 + 0.003796 * t2));
    return clamp(483.3 - y * 369.5, 0, this.board.height);
  }

  /** The inhabited latitudes (about 60°S to 75°N): what a default view should fill. */
  band(): [number, number] {
    return [this.latY(75), this.latY(-60)];
  }

  fit(animate = true) {
    const r = this.svg.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const pad = 12;
    const W = this.board.width, Hh = this.board.height;
    let k = Math.min((r.width - pad * 2) / W, (r.height - pad * 2) / Hh);
    const minK = k;
    const [b0, b1] = this.band();
    let x: number, y: number;
    if (r.height > r.width * 0.9) {
      // portrait: the inhabited latitudes fill the height; centred on the prime meridian (pan for the rest)
      k = r.height / (b1 - b0);
      x = r.width / 2 - (W / 2) * k;
      y = -b0 * k;
    } else {
      // landscape: the whole sheet, sitting high to leave the bottom margin for the terrain & unit key
      const slack = r.height - Hh * k;
      x = (r.width - W * k) / 2;
      y = Math.min(slack / 2, Math.max(pad, slack - 300));
    }
    const wasFit = Math.abs(this.k - this.fitT.k) < 1e-6 || this.fitT.k === 1;
    this.fitT = zoomIdentity.translate(x, y).scale(k);
    // pan and zoom stay on the printed sheet
    this.zoomer
      .scaleExtent([minK * 0.95, Math.max(minK * 14, k * 6)])
      .translateExtent([[-W * 0.04, Math.min(-Hh * 0.04, b0)], [W * 1.04, Math.max(Hh * 1.04, b1)]]);
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

  /** Fit a group of provinces (e.g. a power's homes) with generous padding. */
  zoomToProvinces(pids: string[]) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const pid of pids) {
      const el = this.paths.get(pid);
      if (!el) continue;
      const bb = el.getBBox();
      x0 = Math.min(x0, bb.x); y0 = Math.min(y0, bb.y);
      x1 = Math.max(x1, bb.x + bb.width); y1 = Math.max(y1, bb.y + bb.height);
    }
    if (!isFinite(x0)) return;
    const r = this.svg.getBoundingClientRect();
    const w = Math.max(x1 - x0, 80) * 2.6, h = Math.max(y1 - y0, 50) * 2.6;
    const [b0, b1] = this.band();
    const k = clamp(Math.min(r.width / w, r.height / h), Math.max(this.fitT.k, r.height / (b1 - b0)), this.fitT.k * 5);
    // fill the screen: never show beyond the inhabited latitudes
    const vh = r.height / k;
    const cy = clamp((y0 + y1) / 2, b0 + vh / 2, Math.max(b0 + vh / 2, b1 - vh / 2));
    const t = zoomIdentity.translate(r.width / 2, r.height / 2).scale(k).translate(-(x0 + x1) / 2, -cy);
    select(this.svg).transition().duration(600).call(this.zoomer.transform, t);
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
