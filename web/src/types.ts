export type Pt = [number, number];
export type UnitType = "A" | "F" | "C" | "I";

export interface BoardProvince {
  name: string;
  kind: "home" | "swing" | "filler" | "impassable" | "sea" | "chokepoint";
  d: string;
  label: Pt;
  slots: { military: Pt; civil?: Pt };
  centre: Pt | null;
  label_size: number;
  area_km2: number;
  labels_extra?: Pt[];
  ocean?: boolean;
}

export interface BoardRoute {
  d: string;
  name: string;
  ends: [string, string];
  path: string[];
  chokepoints: string[];
  opens: number | null;
  seam_points: [Pt, Pt][];
}

export interface WrapMark {
  left: string;
  right: string;
  y: Pt;
  at: [Pt, Pt];
}

export interface Board {
  version: number;
  width: number;
  height: number;
  token_r: number;
  outline: string;
  provinces: Record<string, BoardProvince>;
  lakes: string;
  decor: string;
  routes: Record<string, BoardRoute>;
  seam: { left_x: number; right_x: number; left: string; right: string };
  wraps: [string, string][];
  wrap_marks: WrapMark[];
  graticule: string;
}

export interface MapProvince {
  name: string;
  kind: "land" | "sea";
  group: string;
  centre: "home" | "swing" | "none";
  home_of?: string;
  coastal: boolean;
  chokepoint: boolean;
  ocean?: boolean;
  impassable: boolean;
  opens: number | null;
}

export interface Power {
  name: string;
  adjective?: string;
  nuclear: boolean;
  homes: string[];
}

export interface MapData {
  version: number;
  powers: Record<string, Power>;
  provinces: Record<string, MapProvince>;
  adj: { army: Record<string, string[]>; fleet: Record<string, string[]>; any: Record<string, string[]> };
  trade_routes: { id: string; name: string; ends: [string, string]; path: string[]; opens: number | null }[];
  start: { year: number; crisis: number; umbrellas: [string, string][]; units: Unit[] };
}

export interface Unit {
  power: string;
  type: UnitType;
  at: string;
  dislodged?: boolean;
}

export interface Order {
  power: string;
  text: string;
  unit: { type: UnitType; at: string } | null;
  kind: string;
  to: string | null;
  target: { type: UnitType; at: string } | null;
  result: string;
  against?: string[];
  target_power?: string;
  via?: string;
  note?: string;
  reason?: string;
}

export interface Assembly {
  chair: string;
  proposal: string;
  votes: Record<string, string>;
  passed: boolean;
}

export interface Phase {
  name: string;
  year: number;
  season: string;
  kind: "movement" | "retreat" | "winter";
  units: Unit[];
  orders: Order[];
  ownership: Record<string, string | null>;
  crisis: number;
  umbrellas: [string, string][];
  closed: Record<string, string[]>;
  sanctions?: Record<string, number>;
  commons?: Record<string, number>;
  assembly: Assembly | null;
  dislodged?: { power: string; type: UnitType; at: string; from: string }[];
  events?: string[];
}

export interface GameRecord {
  map: string;
  seed?: number;
  note?: string;
  players?: Record<string, string>;
  phases: Phase[];
  result: { kind: string; winner: string | null; scores?: Record<string, number> } | null;
}
