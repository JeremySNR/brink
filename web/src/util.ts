import type { GameRecord, MapData, Order, Unit, UnitType } from "./types";

export const SVG_NS = "http://www.w3.org/2000/svg";

type Attrs = Record<string, string | number | null | undefined | boolean>;

export function svg<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  parent?: Element | null,
): SVGElementTagNameMap[K] {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    e.setAttribute(k, String(v));
  }
  if (parent) parent.appendChild(e);
  return e;
}

export function html<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  parent?: Element | null,
  text?: string,
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = String(v);
    else e.setAttribute(k, String(v));
  }
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

export async function loadJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

/** Fill in fields the record may leave out, using the text notation. */
export function normaliseOrder(o: Order): Order {
  const t = o.text.trim().toUpperCase().split(/\s+/);
  const out: Order = { ...o };
  const isUnit = (s: string | undefined) => !!s && /^[AFCI]$/.test(s);
  if (!out.unit && isUnit(t[0]) && t[1] && !["UMBRELLA", "WITHDRAW"].includes(t[1])) {
    out.unit = { type: t[0] as UnitType, at: t[1] };
  }
  if (!out.kind) {
    if (t[2] === "-") out.kind = "move";
    else if (t[2] === "S") out.kind = "support";
    else if (t[2] === "C") out.kind = "convoy";
    else if (t[2] === "H") out.kind = "hold";
    else if (t[2] === "R") out.kind = "retreat";
    else if (t[2]) out.kind = t[2].toLowerCase();
  }
  if (out.kind === "sanction" && !out.target_power) out.target_power = t[3];
  if (out.kind === "disrupt" && !out.to) out.to = t[3];
  if (out.kind === "close" && !out.against) out.against = t.slice(3);
  if ((out.kind === "move" || out.kind === "retreat") && !out.to) out.to = t[3];
  if ((out.kind === "support" || out.kind === "convoy") && !out.target && isUnit(t[3])) {
    out.target = { type: t[3] as UnitType, at: t[4] };
    if (out.to === undefined || out.to === null) out.to = t[5] === "-" ? t[6] : null;
  }
  if (!out.result) out.result = "ok";
  return out;
}

export function normaliseRecord(rec: GameRecord, map: MapData): GameRecord {
  if (!rec.phases || rec.phases.length === 0) {
    rec.phases = [
      {
        name: `S${map.start.year}M`,
        year: map.start.year,
        season: "spring",
        kind: "movement",
        units: map.start.units,
        orders: [],
        ownership: {},
        crisis: map.start.crisis,
        umbrellas: map.start.umbrellas,
        closed: {},
        assembly: null,
      },
    ];
  }
  for (const ph of rec.phases) {
    ph.orders = (ph.orders || []).map(normaliseOrder);
    ph.units = (ph.units || []) as Unit[];
    ph.closed = ph.closed || {};
    ph.umbrellas = ph.umbrellas || [];
    if (!ph.ownership || Object.keys(ph.ownership).length === 0) {
      ph.ownership = {};
      for (const [pid, p] of Object.entries(map.provinces)) {
        if (p.centre === "home") ph.ownership[pid] = p.home_of ?? null;
      }
    }
  }
  return rec;
}

export function seasonLabel(season: string, kind: string): string {
  const s = season.charAt(0).toUpperCase() + season.slice(1);
  if (kind === "retreat") return `${s} retreats`;
  if (kind === "winter") return "Winter";
  return s;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
