// The player-aid column: printed sections headed with the rulebook's section numbers.
import { codeCounter } from "./counter";
import { POWER_ORDER, UNIT_NAMES } from "./theme";
import type { MapData, Order, Phase } from "./types";
import { html, seasonLabel, svg } from "./util";

const RESULT_LABEL: Record<string, string> = {
  ok: "succeeds", bounce: "bounced", cut: "cut", void: "void", dislodged: "dislodged", failed: "failed",
};

/** Rulebook section numbers (the player aid cross-references them). */
export const RULE = {
  orders: 5, resolution: 6, swing: 7, chokepoints: 8, security: 9, crisis: 10, assembly: 11, winter: 12, winning: 13,
} as const;

export const CRISIS_STEPS: Record<number, string> = { 6: "Recession", 8: "State failure", 10: "Collapse" };

export interface PanelHandlers {
  onOrderHover(index: number | null): void;
  onProvinceFocus(pid: string): void;
}

/** A player-aid section: a black rule, the section number in red, a condensed-caps heading. */
export function aidSection(parent: HTMLElement, ref: number | null, title: string, note?: string, cls = ""): HTMLElement {
  const s = html("section", { class: `aid ${cls}`.trim() }, parent);
  const h = html("h3", { class: "aid-h" }, s);
  if (ref !== null) html("span", { class: "ref" }, h, String(ref));
  html("span", { class: "aid-t" }, h, title);
  if (note) html("span", { class: "aid-note" }, h, note);
  return s;
}

/** The Crisis track: boxes 0-10, a marker counter on the current value, thresholds printed. */
export function crisisTrack(parent: HTMLElement, level: number) {
  const v = Math.max(0, Math.min(10, level));
  const step = v >= 10 ? 10 : v >= 8 ? 8 : v >= 6 ? 6 : 0;
  const track = html("ol", {
    class: "crisis-track", role: "meter", "aria-valuemin": 0, "aria-valuemax": 10, "aria-valuenow": v,
    "aria-label": `Crisis ${v} of 10${step ? `: ${CRISIS_STEPS[step]}` : ""}`,
  }, parent);
  for (let i = 0; i <= 10; i++) {
    const cls = ["cbox"];
    if (i === 6 || i === 7) cls.push("t6");
    if (i === 8 || i === 9) cls.push("t8");
    if (i === 10) cls.push("t10");
    if (CRISIS_STEPS[i]) cls.push("step");
    if (i < v) cls.push("past");
    const li = html("li", { class: cls.join(" ") }, track);
    html("span", { class: "cnum" }, li, String(i));
    if (i === v) {
      li.classList.add("on");
      const m = html("span", { class: `cmarker${v >= 6 ? " danger" : ""}`, title: `Crisis ${v}` }, li);
      html("span", {}, m, String(v));
    }
  }
  const key = html("ul", { class: "crisis-key" }, parent);
  for (const [n, label, effect] of [[6, "Recession", "builds −1"], [8, "State failure", ""], [10, "Collapse", "all lose"]] as const) {
    const li = html("li", { class: `t${n}${v >= n ? " reached" : ""}` }, key);
    html("b", {}, li, String(n));
    html("span", {}, li, label);
    if (effect) html("span", { class: "eff" }, li, effect);
  }
}

export class Panel {
  constructor(
    private root: HTMLElement,
    private map: MapData,
    private h: PanelHandlers,
  ) {}

  /** Render a phase. `opts.root` renders into another element (without clearing it);
   * `opts.header`/`opts.orders` false leave out the phase title and the order list (play mode). */
  render(ph: Phase, index: number, total: number, opts: { root?: HTMLElement; header?: boolean; orders?: boolean; you?: string } = {}) {
    const r = opts.root ?? this.root;
    if (!opts.root) r.replaceChildren();
    const P = this.map.powers;
    const pname = (p: string) => P[p]?.name ?? p;

    // ---- phase header
    if (opts.header !== false) {
      const head = html("section", { class: "phase-head" }, r);
      const h = html("h2", { class: "phase-title" }, head);
      h.append(document.createTextNode(`${seasonLabel(ph.season, ph.kind)} `));
      html("span", { class: "num" }, h, String(ph.year));
      html("p", { class: "phase-sub" }, head,
        `${ph.kind === "movement" ? "Movement" : ph.kind === "retreat" ? "Retreats" : "Adjustments and Assembly"}, phase ${index + 1} of ${total}`);
    }

    // ---- what happened comes first in replay: consequences before detail
    if (ph.events?.length && opts.orders !== false) this.events(r, ph);
    // the Assembly is winter's headline decision: right under what happened
    this.assembly(r, ph);

    // ---- crisis
    const cr = aidSection(r, RULE.crisis, "Crisis track", undefined, "crisis");
    crisisTrack(cr, ph.crisis);
    const commons = Object.entries(ph.commons ?? {}).filter(([, n]) => n > 0);
    if (commons.length) {
      const c = html("div", { class: "aid-row" }, cr);
      html("span", { class: "k" }, c, "Commons");
      const v = html("div", { class: "v" }, c);
      for (const [p, n] of commons) {
        const s = html("span", { class: "code-n", title: `${pname(p)}: ${n} COMMONS order${n === 1 ? "" : "s"} this year` }, v);
        s.append(codeCounter(p, 18));
        html("span", { class: "num" }, s, `×${n}`);
      }
    }

    // ---- supply centres
    const counts: Record<string, number> = {};
    const swingCounts: Record<string, number> = {};
    let swingTotal = 0;
    let swingAligned = 0;
    for (const mp of Object.values(this.map.provinces)) if (mp.centre === "swing") swingTotal++;
    for (const [pid, owner] of Object.entries(ph.ownership)) {
      if (!owner) continue;
      const mp = this.map.provinces[pid];
      if (!mp || mp.centre === "none") continue;
      counts[owner] = (counts[owner] ?? 0) + 1;
      if (mp.centre === "swing") {
        swingCounts[owner] = (swingCounts[owner] ?? 0) + 1;
        swingAligned++;
      }
    }
    const units: Record<string, number> = {};
    for (const u of ph.units) units[u.power] = (units[u.power] ?? 0) + 1;
    const cc = aidSection(r, RULE.winter, "Supply centres");
    const list = html("ul", { class: "centres" }, cc);
    const order = [...POWER_ORDER].filter((p) => P[p]).sort((a, b) => (counts[b] ?? 0) - (counts[a] ?? 0));
    for (const p of order) {
      const li = html("li", { class: p === opts.you ? "you" : "" }, list);
      li.title = `${pname(p)}${p === opts.you ? " (you)" : ""}: ${counts[p] ?? 0} centres (${swingCounts[p] ?? 0} swing), ${units[p] ?? 0} units`;
      li.append(codeCounter(p, 22));
      html("span", { class: "pname" }, li, P[p].name);
      html("b", { class: "num" }, li, String(counts[p] ?? 0));
    }
    const un = html("li", { class: "unaligned" }, list);
    un.title = "Swing states no power holds";
    const ring = svg("svg", { viewBox: "0 0 16 16", width: 22, height: 22, "aria-hidden": "true", class: "ring-sw" }, un);
    svg("circle", { cx: 8, cy: 8, r: 4.4 }, ring);
    html("span", { class: "pname" }, un, "Unaligned swing");
    html("b", { class: "num" }, un, String(swingTotal - swingAligned));

    // ---- security: umbrellas, closures, sanctions
    const st = aidSection(r, RULE.security, "Security");
    const row = (k: string, ref?: number) => {
      const d = html("div", { class: "aid-row" }, st);
      const kk = html("span", { class: "k" }, d, k);
      if (ref) html("span", { class: "xref" }, kk, ` §${ref}`);
      return html("div", { class: "v" }, d);
    };
    const v1 = row("Umbrellas");
    if (!ph.umbrellas.length) html("span", { class: "muted" }, v1, "None");
    for (const [a, b] of ph.umbrellas) {
      const c = html("span", { class: "umb", title: `${pname(a)} extends its nuclear umbrella over ${pname(b)}` }, v1);
      c.append(codeCounter(a, 18), umbrellaIcon(), codeCounter(b, 18));
    }
    const v2 = row("Closed", RULE.chokepoints);
    const closed = Object.entries(ph.closed || {});
    if (!closed.length) html("span", { class: "muted" }, v2, "All chokepoints open");
    for (const [z, against] of closed) {
      const closer = ph.units.find((u) => u.at === z && u.type === "F")?.power;
      const c = html("button", {
        class: "closed-row", type: "button",
        title: `${this.map.provinces[z]?.name ?? z}${closer ? ` closed by ${pname(closer)}` : ""}${against.length ? ` to ${against.join(", ")}` : " to everyone"}. Click to find it on the map.`,
      }, v2);
      if (closer) c.append(codeCounter(closer, 18));
      html("span", { class: "cname" }, c, this.map.provinces[z]?.name ?? z);
      html("span", { class: "muted" }, c, against.length ? `to ${against.join(" ")}` : "to all");
      c.addEventListener("click", () => this.h.onProvinceFocus(z));
    }
    const sanc = Object.entries(ph.sanctions ?? {}).filter(([, n]) => n > 0);
    if (sanc.length) {
      const v3 = row("Sanctions", RULE.chokepoints);
      for (const [p, n] of sanc) {
        const s = html("span", { class: "code-n", title: `${pname(p)} is under ${n} sanction${n === 1 ? "" : "s"}: builds −${n}` }, v3);
        s.append(codeCounter(p, 18));
        html("span", { class: "num" }, s, `−${n}`);
      }
    }

    if (ph.events?.length && opts.orders === false) this.events(r, ph);

    // ---- orders
    if (opts.orders === false) return;
    const os = aidSection(r, RULE.orders, "Orders", ph.orders.length ? `${ph.orders.length} written` : undefined, "orders");
    if (!ph.orders.length) html("p", { class: "muted" }, os, "No orders recorded for this phase.");
    const byPower = new Map<string, { o: Order; i: number }[]>();
    ph.orders.forEach((o, i) => {
      if (!byPower.has(o.power)) byPower.set(o.power, []);
      byPower.get(o.power)!.push({ o, i });
    });
    for (const p of POWER_ORDER) {
      const items = byPower.get(p);
      if (!items) continue;
      const grp = html("div", { class: "order-group" }, os);
      const gh = html("div", { class: "order-power" }, grp);
      gh.append(codeCounter(p, 18));
      html("span", {}, gh, pname(p));
      const ul = html("ul", {}, grp);
      for (const { o, i } of items) {
        const ok = ["ok", "success", "succeeded"].includes(o.result);
        const li = html("li", { class: `order-item${ok ? "" : " failed"}${p === opts.you ? " yours" : ""}`, tabindex: 0 }, ul);
        html("span", { class: "notation" }, li, o.text);
        if (!ok) {
          const b = html("span", { class: `result r-${o.result}` }, li, RESULT_LABEL[o.result] ?? o.result);
          if (o.note) b.title = o.note;
        }
        li.addEventListener("mouseenter", () => this.h.onOrderHover(i));
        li.addEventListener("focus", () => this.h.onOrderHover(i));
        li.addEventListener("mouseleave", () => this.h.onOrderHover(null));
        li.addEventListener("blur", () => this.h.onOrderHover(null));
        const target = o.to ?? o.unit?.at;
        if (target) li.addEventListener("click", () => this.h.onProvinceFocus(target));
        li.title = describe(o, this.map);
      }
    }
  }

  private assembly(r: HTMLElement, ph: Phase) {
    if (!ph.assembly) return;
    const P = this.map.powers;
    const pname = (p: string) => P[p]?.name ?? p;
    const a = ph.assembly;
    const as = aidSection(r, RULE.assembly, "Assembly", undefined, "assembly");
    html("span", { class: `stamp ${a.passed ? "pass" : "fail"}` }, as.querySelector("h3")!, a.passed ? "Passed" : "Failed");
    const chair = html("div", { class: "aid-row" }, as);
    html("span", { class: "k" }, chair, "Chair");
    const cv = html("div", { class: "v" }, chair);
    const cs = html("span", { class: "code-n" }, cv);
    cs.append(codeCounter(a.chair, 18));
    html("span", {}, cs, pname(a.chair));
    html("p", { class: "proposal" }, as, a.proposal);
    const votes = html("ul", { class: "votes" }, as);
    for (const p of POWER_ORDER) {
      const v = a.votes[p];
      if (!v) continue;
      const li = html("li", { class: `vote ${v.toLowerCase()}`, title: `${pname(p)}: ${v}` }, votes);
      li.append(codeCounter(p, 18));
      html("b", {}, li, v.charAt(0) + v.slice(1).toLowerCase());
    }
  }

  private events(r: HTMLElement, ph: Phase) {
    const ev = aidSection(r, ph.kind === "winter" ? RULE.winter : RULE.resolution, "What happened", undefined, "events");
    const ul = html("ul", { class: "event-list" }, ev);
    for (const e of ph.events ?? []) html("li", {}, ul, e);
  }
}

export function umbrellaIcon(): SVGSVGElement {
  const s = svg("svg", { viewBox: "0 0 16 16", width: 14, height: 14, class: "icon umbrella", "aria-hidden": "true" });
  svg("path", { d: "M8 2.2C4.6 2.2 2.2 4.6 1.8 7.6h12.4C13.8 4.6 11.4 2.2 8 2.2Zm0 5.4v5a1.4 1.4 0 0 1-2.8 0", fill: "none", stroke: "currentColor", "stroke-width": 1.3, "stroke-linecap": "square" }, s);
  return s;
}

function describe(o: Order, map: MapData): string {
  const name = (pid?: string | null) => (pid ? map.provinces[pid]?.name ?? pid : "?");
  const u = o.unit ? `${UNIT_NAMES[o.unit.type]} in ${name(o.unit.at)}` : o.power;
  switch (o.kind) {
    case "move": return `${u} moves to ${name(o.to)}${o.via === "convoy" ? " by convoy" : ""}`;
    case "support": return `${u} supports ${o.target ? `${UNIT_NAMES[o.target.type]} in ${name(o.target.at)}` : ""}${o.to ? ` into ${name(o.to)}` : " holding"}`;
    case "convoy": return `${u} convoys ${o.target ? name(o.target.at) : ""} to ${name(o.to)}`;
    case "close": return `${u} closes ${name(o.unit?.at)}${o.against?.length ? ` to ${o.against.join(", ")}` : " to everyone else"}`;
    case "sanction": return `${u} sanctions ${o.target_power}`;
    case "disrupt": return `${u} disrupts ${name(o.to)}`;
    case "commons": return `${u} works on the commons`;
    default: return o.text;
  }
}
