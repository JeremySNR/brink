import { POWER_ORDER, UNIT_NAMES, powerColour } from "./theme";
import type { MapData, Order, Phase } from "./types";
import { html, seasonLabel, svg } from "./util";

const RESULT_LABEL: Record<string, string> = {
  ok: "succeeds", bounce: "bounced", cut: "cut", void: "void", dislodged: "dislodged", failed: "failed",
};

export interface PanelHandlers {
  onOrderHover(index: number | null): void;
  onProvinceFocus(pid: string): void;
}

export class Panel {
  constructor(
    private root: HTMLElement,
    private map: MapData,
    private h: PanelHandlers,
  ) {}

  render(ph: Phase, index: number, total: number) {
    const r = this.root;
    r.replaceChildren();
    const P = this.map.powers;
    const pname = (p: string) => P[p]?.name ?? p;

    // ---- phase header
    const head = html("section", { class: "phase-head" }, r);
    html("div", { class: "eyebrow" }, head, `Phase ${index + 1} of ${total}`);
    const h = html("h2", { class: "phase-title" }, head);
    html("span", { class: "phase-season" }, h, seasonLabel(ph.season, ph.kind));
    html("span", { class: "phase-year" }, h, ` ${ph.year}`);
    html("div", { class: "phase-kind" }, head, ph.kind === "movement" ? "Movement" : ph.kind === "retreat" ? "Retreats" : "Adjustments & Assembly");

    // ---- crisis gauge
    const cr = html("section", { class: "card crisis" }, r);
    const crHead = html("div", { class: "card-head" }, cr);
    html("h3", {}, crHead, "Crisis");
    const level = Math.max(0, Math.min(10, ph.crisis));
    html("span", { class: `crisis-value ${level >= 8 ? "danger" : level >= 6 ? "warn" : ""}` }, crHead, `${level} / 10`);
    const gauge = html("div", {
      class: "gauge", role: "meter", "aria-valuemin": 0, "aria-valuemax": 10, "aria-valuenow": level,
      "aria-label": `Crisis ${level} of 10`,
    }, cr);
    for (let i = 1; i <= 10; i++) {
      const seg = html("span", { class: `seg${i <= level ? " on" : ""}${i >= 10 ? " t10" : i >= 8 ? " t8" : i >= 6 ? " t6" : ""}` }, gauge);
      if (i === 6 || i === 8 || i === 10) seg.dataset.threshold = String(i);
    }
    const legend = html("div", { class: "gauge-legend" }, cr);
    html("span", {}, legend, "6 · builds −1");
    html("span", {}, legend, "8 · brink");
    html("span", {}, legend, "10 · collapse");

    // ---- centres per power
    const cc = html("section", { class: "card" }, r);
    const ccHead = html("div", { class: "card-head" }, cc);
    html("h3", {}, ccHead, "Supply centres");
    html("span", { class: "muted" }, ccHead, "homes + aligned swing");
    const counts: Record<string, number> = {};
    const swingCounts: Record<string, number> = {};
    for (const [pid, owner] of Object.entries(ph.ownership)) {
      if (!owner) continue;
      const mp = this.map.provinces[pid];
      if (!mp || mp.centre === "none") continue;
      counts[owner] = (counts[owner] ?? 0) + 1;
      if (mp.centre === "swing") swingCounts[owner] = (swingCounts[owner] ?? 0) + 1;
    }
    const units: Record<string, number> = {};
    for (const u of ph.units) units[u.power] = (units[u.power] ?? 0) + 1;
    const max = Math.max(11, ...Object.values(counts));
    const list = html("ul", { class: "centres" }, cc);
    const order = [...POWER_ORDER].sort((a, b) => (counts[b] ?? 0) - (counts[a] ?? 0));
    for (const p of order) {
      if (!P[p]) continue;
      const li = html("li", {}, list);
      const sw = html("span", { class: "swatch" }, li);
      sw.style.background = powerColour(p);
      html("span", { class: "pname" }, li, P[p].name);
      const bar = html("span", { class: "bar", "aria-hidden": "true" }, li);
      const fill = html("span", { class: "bar-fill" }, bar);
      fill.style.width = `${((counts[p] ?? 0) / max) * 100}%`;
      fill.style.background = powerColour(p);
      const n = html("span", { class: "num" }, li, String(counts[p] ?? 0));
      n.title = `${counts[p] ?? 0} centres (${swingCounts[p] ?? 0} swing) · ${units[p] ?? 0} units`;
      const sanc = ph.sanctions?.[p];
      if (sanc) {
        const s = html("span", { class: "chip tiny sanction", title: `${sanc} sanction(s): builds −${sanc}` }, li);
        s.textContent = `⊘${sanc}`;
      }
    }

    // ---- umbrellas / closures
    const st = html("section", { class: "card" }, r);
    html("h3", {}, st, "Security");
    const row1 = html("div", { class: "kv" }, st);
    html("span", { class: "k" }, row1, "Umbrellas");
    const v1 = html("span", { class: "v chips" }, row1);
    if (!ph.umbrellas.length) html("span", { class: "muted" }, v1, "none");
    for (const [a, b] of ph.umbrellas) {
      const c = html("span", { class: "chip", title: `${pname(a)} extends its nuclear umbrella over ${pname(b)}` }, v1);
      c.append(dot(a), document.createTextNode(` ${a} `), umbrellaIcon(), document.createTextNode(` ${b} `), dot(b));
    }
    const row2 = html("div", { class: "kv" }, st);
    html("span", { class: "k" }, row2, "Closed");
    const v2 = html("span", { class: "v chips" }, row2);
    const closed = Object.entries(ph.closed || {});
    if (!closed.length) html("span", { class: "muted" }, v2, "all chokepoints open");
    for (const [z, against] of closed) {
      const closer = ph.units.find((u) => u.at === z && u.type === "F")?.power;
      const c = html("button", {
        class: "chip closed", type: "button",
        title: `${this.map.provinces[z]?.name ?? z}${closer ? ` closed by ${pname(closer)}` : ""}${against.length ? ` to ${against.join(", ")}` : ""}`,
      }, v2);
      if (closer) c.append(dot(closer));
      c.append(document.createTextNode(` ${this.map.provinces[z]?.name.split(" ")[0] ?? z}`));
      if (against.length) html("span", { class: "muted" }, c, ` → ${against.join(" ")}`);
      c.addEventListener("click", () => this.h.onProvinceFocus(z));
    }

    // ---- assembly
    if (ph.assembly) {
      const a = ph.assembly;
      const as = html("section", { class: "card assembly" }, r);
      const ah = html("div", { class: "card-head" }, as);
      html("h3", {}, ah, "Assembly");
      html("span", { class: `pill ${a.passed ? "pass" : "fail"}` }, ah, a.passed ? "Passed" : "Failed");
      const chair = html("div", { class: "chair" }, as);
      chair.append(document.createTextNode("Chair "), dot(a.chair), document.createTextNode(` ${pname(a.chair)}`));
      html("p", { class: "proposal" }, as, a.proposal);
      const votes = html("div", { class: "votes" }, as);
      for (const p of POWER_ORDER) {
        const v = a.votes[p];
        if (!v) continue;
        const vv = html("span", { class: `vote ${v.toLowerCase()}`, title: `${pname(p)}: ${v}` }, votes);
        vv.append(dot(p), document.createTextNode(` ${p} `));
        html("b", {}, vv, v.toUpperCase());
      }
    }

    // ---- events
    if (ph.events?.length) {
      const ev = html("section", { class: "card events" }, r);
      html("h3", {}, ev, "What happened");
      const ul = html("ul", {}, ev);
      for (const e of ph.events) html("li", {}, ul, e);
    }

    // ---- orders
    const os = html("section", { class: "card orders" }, r);
    const oh = html("div", { class: "card-head" }, os);
    html("h3", {}, oh, "Orders");
    html("span", { class: "muted" }, oh, `${ph.orders.length}`);
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
      gh.append(dot(p), document.createTextNode(` ${pname(p)}`));
      const ul = html("ul", {}, grp);
      for (const { o, i } of items) {
        const ok = ["ok", "success", "succeeded"].includes(o.result);
        const li = html("li", { class: `order-item${ok ? "" : " failed"}`, tabindex: 0 }, ul);
        html("code", {}, li, o.text);
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
}

function dot(p: string): HTMLElement {
  const d = document.createElement("span");
  d.className = "dot";
  d.style.background = powerColour(p);
  return d;
}

function umbrellaIcon(): SVGSVGElement {
  const s = svg("svg", { viewBox: "0 0 16 16", width: 12, height: 12, class: "icon", "aria-hidden": "true" });
  svg("path", { d: "M8 2C4.4 2 2 4.6 1.6 7.6h12.8C14 4.6 11.6 2 8 2Zm0 5.6V13a1.4 1.4 0 0 1-2.8 0", fill: "none", stroke: "currentColor", "stroke-width": 1.4, "stroke-linecap": "round" }, s);
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
