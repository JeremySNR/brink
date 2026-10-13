// Play mode: one human against six bots through `brink serve` (src/brink/server.py).
//
// The server runs the engine and publishes the open decision ("pending"); this controller turns it into
// board interactions (Backstabbr-style order entry), drafts, validation and submission. The board shows the
// game record's phases followed by one "live" phase: the current position with the drafted orders on it.
import type { BoardView } from "./board";
import type { Overlay } from "./overlay";
import { POWER_ORDER, UNIT_NAMES, powerColour } from "./theme";
import type { GameRecord, MapData, Order, Phase, Unit, UnitType } from "./types";
import { html, normaliseOrder, seasonLabel, svg } from "./util";

// ------------------------------------------------------------------ API shapes
interface URef { type: UnitType; at: string }
interface Opt {
  text: string;
  kind: string;
  unit: URef | null;
  to: string | null;
  target: URef | null;
  about?: unknown;
}
interface UnitChoice { unit: URef; name: string; options: Opt[] }
interface RetreatChoice { unit: URef; name: string; to: string[] }
interface BuildChoice { type: UnitType; at: string; text: string; forward_base: boolean }
interface DisbandChoice { unit: URef; name: string; text: string }
interface Status {
  centres: number; units: number; swing: number; protected_by: string[]; protecting: string[];
  vetoes_left: number; vote_weight: number; nuclear: boolean; effects: string[];
}
interface Pending {
  id: number;
  kind: "orders" | "retreats" | "propose" | "vote" | "adjust" | "umbrellas";
  power: string;
  phase: Phase;
  status: Status;
  units?: (UnitChoice | RetreatChoice)[];
  withdraw?: string[];
  resolutions?: string[];
  chair?: string;
  proposal?: string;
  can_veto?: boolean;
  weight?: number;
  change?: number;
  builds?: BuildChoice[];
  disband?: DisbandChoice[];
  candidates?: string[];
  current?: string[];
}
interface PlayRecord extends GameRecord {
  human?: string;
  result: (GameRecord["result"] & { year?: number; centres?: Record<string, number> }) | null;
}
interface GameState {
  id: string; power: string; seed: number; status: "waiting" | "thinking" | "over" | "error" | "stopped";
  record: PlayRecord; pending: Pending | null; error: string | null;
}
interface Check { text: string; ok: boolean; reason: string | null }

export interface PlayHost {
  view: BoardView;
  map: MapData;
  overlay: Overlay;
  setRecord(rec: GameRecord, live: Phase | null, goTo?: number): void;
  go(i: number): void;
  redraw(): void;
  current(): number;
  total(): number;
  liveIndex(): number | null;
  phase(i: number): Phase;
  /** Crisis, centres and security cards for a phase (the replay panel's, without header and orders). */
  stateCards(root: HTMLElement, ph: Phase): void;
}

const CHARACTER: Record<string, string> = {
  USA: "Two fleets on two oceans and an umbrella over Japan. The Americas are yours to lose.",
  CHN: "No military at the start: two Capitals and Influence to buy Asia, then the world.",
  EU: "Two Armies and a Capital at the centre of the board, with Ukraine, Turkey and Africa next door.",
  RUS: "Armies across the largest landmass and Influence in the Far East: strong at home, short of reach.",
  IND: "Army, Fleet and Capital on the Indian Ocean, with Pakistan, Iran and the south in reach.",
  JPN: "Not nuclear and sheltered by the US umbrella: two Influence and a Capital make it a broker.",
  GLF: "Not nuclear, rich and exposed: a Fleet on Hormuz and two Capitals to buy friends.",
};

const RES_HELP: Record<string, string> = {
  SANCTIONS: "its Capitals cannot move next year",
  PEACEKEEPING: "no Military moves in; Military there may only hold or support",
  NAVIGATION: "the chokepoint cannot be closed",
  ACCORD: "every power that scores no COMMONS next year loses a build",
  RECOGNITION: "becomes unaligned now; no civil moves in next year",
};

const keyOf = (u: URef) => `${u.type} ${u.at}`;
const provOf = (loc: string) => loc.split("/")[0];

type Mode = "move" | "support-unit" | "support-dest" | "convoy-army" | "convoy-dest" | "disrupt";

interface UnitIndex {
  choice: UnitChoice;
  hold: Opt | null;
  moves: Map<string, Opt>;
  supHold: Map<string, Opt>; // target unit key -> option
  supMove: Map<string, Map<string, Opt>>; // target unit key -> dest province -> option
  convoys: Map<string, Map<string, Opt>>; // army key -> dest -> option
  commons: Opt | null;
  sanctions: Map<string, Opt>;
  disrupts: Map<string, Opt>;
  closes: Opt[];
}

export class PlayController {
  power: string | null = null;
  private id: string | null = null;
  private state: GameState | null = null;
  private pending: Pending | null = null;
  private busy = false;
  private reviewFrom: number | null = null; // first phase the player hasn't seen yet
  private overShown = false;

  // drafts for the open decision
  private orders = new Map<string, Opt>();
  private withdraw = new Set<string>();
  private retreats = new Map<string, string>();
  private builds: BuildChoice[] = [];
  private disbands = new Set<string>();
  private proposal: string | null = null;
  private vote: string | null = null;
  private umbrellas = new Set<string>();
  private checks = new Map<string, Check>();
  private validateTimer = 0;

  // selection
  private index = new Map<string, UnitIndex>();
  private sel: { key: string; mode: Mode; target?: string } | null = null;
  private retreatSel: string | null = null;
  private buildSite: string | null = null;

  private statusChip: HTMLElement;
  private fab: HTMLButtonElement;
  private toast: HTMLElement;

  constructor(private host: PlayHost) {
    const top = document.querySelector(".top-actions")!;
    this.statusChip = html("span", { class: "you-chip", id: "play-status", hidden: true });
    top.prepend(this.statusChip);
    const stage = document.getElementById("stage")!;
    this.fab = html("button", { class: "btn primary fab", type: "button", hidden: true }, stage, "Commit");
    this.fab.addEventListener("click", () => this.commit());
    this.toast = html("div", { class: "play-toast", role: "status", "aria-live": "polite", hidden: true }, stage);
    host.overlay.showHolds = true;
    (window as unknown as { __brink: unknown }).__brink = this; // dev/testing hook (see web/README.md)
  }

  // ================================================================== lifecycle
  async start(param: string | null) {
    const id = param && !["", "1", "new", "true"].includes(param) ? param : null;
    if (id) {
      try {
        const st = await api<GameState>("GET", `/api/games/${id}`);
        this.reviewFrom = null;
        this.accept(st, true);
        this.focusHome();
        return;
      } catch {
        /* unknown game (server restarted): fall through to a new one */
      }
    }
    this.showStart();
  }

  private async create(power: string, seed: string, alliances: boolean, lastYear: string) {
    this.setBusy(true, "Setting up the board…");
    try {
      const st = await api<GameState>("POST", "/api/games", {
        power, seed: seed === "" ? null : Number(seed), alliances, last_year: lastYear === "" ? null : Number(lastYear),
      });
      closeModal();
      const url = new URL(location.href);
      url.searchParams.set("play", st.id);
      url.hash = "";
      history.replaceState(null, "", url);
      this.overShown = false;
      this.accept(st, true);
      this.focusHome();
    } catch (e) {
      this.showError(String(e));
    } finally {
      this.setBusy(false);
    }
  }

  /** Zoom to the player's home provinces. */
  focusHome() {
    const homes = this.host.map.powers[this.power ?? ""]?.homes ?? [];
    if (homes.length) this.host.view.zoomToProvinces(homes);
  }

  /** Take a new server state: rebuild the record + live phase and move the view. */
  private accept(st: GameState, fresh = false) {
    const before = fresh ? null : (this.state?.record.phases.length ?? 0);
    this.state = st;
    this.id = st.id;
    this.power = st.power;
    const newDecision = st.pending?.id !== this.pending?.id;
    this.pending = st.pending;
    if (newDecision) this.resetDrafts();
    const rec: PlayRecord = st.record;
    const live = st.pending ? livePhase(st.pending.phase) : null;
    const n = rec.phases.length;
    let goTo = live ? n : Math.max(0, n - 1);
    if (before !== null && n > before) {
      this.reviewFrom = before;
      goTo = before; // step through what happened since the last decision
    } else if (fresh) this.reviewFrom = null;
    this.host.setRecord({ ...rec, phases: [...rec.phases] }, live, goTo);
    this.updateChip();
    if (st.status === "thinking") window.setTimeout(() => this.poll(), 700);
    if (st.status === "error") this.showError(st.error ?? "the game stopped with an error");
    if (st.status === "over" && !this.overShown) {
      this.overShown = true;
      this.showGameOver();
    }
  }

  private async poll() {
    if (!this.id) return;
    try {
      const st = await api<GameState>("GET", `/api/games/${this.id}`);
      this.accept(st);
    } catch (e) {
      this.showError(String(e));
    }
  }

  async commit() {
    const p = this.pending;
    if (!p || this.busy || !this.id) return;
    const texts = this.texts();
    this.setBusy(true, p.kind === "orders" ? "Orders committed. The other powers are moving…" : "Committed…");
    try {
      const r = await api<{ state: GameState; accepted: boolean }>("POST", `/api/games/${this.id}/orders`, {
        orders: texts, decision: p.id, submit: true,
      });
      try {
        localStorage.removeItem(this.draftKey());
      } catch {
        /* ignore */
      }
      this.accept(r.state);
    } catch (e) {
      // 409: the decision moved on (another tab?): resync
      await this.poll();
      if (!String(e).includes("409")) this.showError(String(e));
    } finally {
      this.setBusy(false);
    }
  }

  private setBusy(on: boolean, msg = "") {
    this.busy = on;
    document.body.classList.toggle("play-busy", on);
    this.toast.hidden = !on;
    this.toast.textContent = msg;
    this.fab.disabled = on;
    document.querySelectorAll<HTMLButtonElement>(".commit").forEach((b) => (b.disabled = on));
  }

  // ================================================================== drafts
  private resetDrafts() {
    this.orders.clear();
    this.withdraw.clear();
    this.retreats.clear();
    this.builds = [];
    this.disbands.clear();
    this.proposal = null;
    this.vote = null;
    this.umbrellas = new Set(this.pending?.current ?? []);
    this.checks.clear();
    this.sel = null;
    this.retreatSel = null;
    this.buildSite = null;
    this.index.clear();
    const p = this.pending;
    if (p?.kind === "orders") {
      for (const c of p.units as UnitChoice[]) this.index.set(keyOf(c.unit), indexUnit(c));
    }
    if (p?.kind === "retreats") {
      const first = (p.units as RetreatChoice[])[0];
      if (first) this.retreatSel = keyOf(first.unit);
    }
    // restore an unsent draft (page reload)
    try {
      const saved = localStorage.getItem(this.draftKey());
      if (saved) this.applyTexts(JSON.parse(saved) as string[]);
    } catch {
      /* ignore */
    }
  }

  private draftKey() {
    return `brink.play.${this.id}.${this.pending?.id}`;
  }

  /** Re-apply order texts to the drafts (restoring a saved draft). */
  private applyTexts(texts: string[]) {
    const p = this.pending;
    if (!p) return;
    for (const t of texts) {
      const T = t.toUpperCase();
      if (p.kind === "orders") {
        if (T.includes(" WITHDRAW ")) this.withdraw.add(T.split(/\s+/)[2]);
        else
          for (const [k, ix] of this.index) {
            const o = ix.choice.options.find((x) => x.text === T);
            if (o) this.orders.set(k, o);
          }
      } else if (p.kind === "retreats") {
        const [ty, at] = T.split(/\s+/);
        this.retreats.set(`${ty} ${at}`, T);
      } else if (p.kind === "adjust") {
        const b = p.builds?.find((x) => x.text === T);
        if (b) this.builds.push(b);
        const d = p.disband?.find((x) => x.text === T);
        if (d) this.disbands.add(keyOf(d.unit));
      } else if (p.kind === "propose") this.proposal = T;
      else if (p.kind === "vote") this.vote = T.replace("VOTE ", "");
      else if (p.kind === "umbrellas") {
        this.umbrellas.clear();
        this.umbrellas.add(T.split(/\s+/)[2]);
      }
    }
    if (p.kind === "umbrellas" && texts.length === 0) this.umbrellas.clear();
  }

  /** The order texts the open decision would submit now. */
  texts(): string[] {
    const p = this.pending;
    if (!p) return [];
    switch (p.kind) {
      case "orders":
        return [...[...this.orders.values()].map((o) => o.text), ...[...this.withdraw].map((b) => `${p.power} WITHDRAW ${b}`)];
      case "retreats":
        return [...this.retreats.values()];
      case "adjust":
        return (p.change ?? 0) > 0 ? this.builds.map((b) => b.text)
          : (p.disband ?? []).filter((d) => this.disbands.has(keyOf(d.unit))).map((d) => d.text);
      case "propose":
        return [this.proposal ?? "PROPOSE NOTHING"];
      case "vote":
        return this.vote ? [`VOTE ${this.vote}`] : [];
      case "umbrellas":
        return [...this.umbrellas].map((b) => `${p.power} UMBRELLA ${b}`);
    }
    return [];
  }

  private changed() {
    try {
      localStorage.setItem(this.draftKey(), JSON.stringify(this.texts()));
    } catch {
      /* ignore */
    }
    this.host.redraw();
    window.clearTimeout(this.validateTimer);
    this.validateTimer = window.setTimeout(() => this.validate(), 120);
  }

  private async validate() {
    const p = this.pending;
    if (!p || !this.id) return;
    const texts = this.texts();
    if (!texts.length) return;
    try {
      const r = await api<{ results: Check[] }>("POST", `/api/games/${this.id}/validate`, { orders: texts });
      if (this.pending?.id !== p.id) return;
      let dirty = false;
      for (const c of r.results) {
        const prev = this.checks.get(c.text);
        if (!prev || prev.ok !== c.ok) dirty = true;
        this.checks.set(c.text, c);
      }
      if (dirty) this.host.redraw();
    } catch {
      /* validation is advisory */
    }
  }

  private voidReason(text: string): string | null {
    const c = this.checks.get(text);
    return c && !c.ok ? c.reason ?? "void" : null;
  }

  // ================================================================== board rendering
  /** The live phase with the drafted orders drawn as arrows. */
  displayPhase(): Phase {
    const i = this.host.liveIndex()!;
    const base = this.host.phase(i);
    const p = this.pending!;
    const orders: Order[] = [];
    const add = (o: Partial<Order> & { text: string; kind: string }) => {
      const n = normaliseOrder({ power: p.power, unit: null, to: null, target: null, result: "ok", ...o } as Order);
      n.result = this.voidReason(o.text) ? "void" : "ok";
      orders.push(n);
    };
    if (p.kind === "orders") {
      for (const o of this.orders.values()) add({ text: o.text, kind: o.kind, unit: o.unit, to: o.to, target: o.target });
    } else if (p.kind === "retreats") {
      for (const [k, t] of this.retreats) {
        const [type, at] = k.split(" ");
        const T = t.split(/\s+/);
        add({ text: t, kind: T[2] === "R" ? "retreat" : "disband", unit: { type: type as UnitType, at }, to: T[2] === "R" ? T[3] : null });
      }
    } else if (p.kind === "adjust") {
      for (const b of this.builds) add({ text: b.text, kind: "build", unit: { type: b.type, at: b.at } });
      for (const d of p.disband ?? []) if (this.disbands.has(keyOf(d.unit))) add({ text: d.text, kind: "disband", unit: d.unit });
    }
    return { ...base, orders };
  }

  /** Highlights + the selection bar for the live phase. */
  paint() {
    const view = this.host.view;
    const L = view.layer.reach;
    L.replaceChildren();
    const p = this.pending;
    const bar = document.getElementById("selection")!;
    bar.classList.add("play-bar");
    bar.replaceChildren();
    bar.hidden = true;
    this.fab.hidden = !p;
    if (!p) return;
    const col = powerColour(p.power);
    const mark = (pid: string, cls = "") => {
      const bp = view.board.provinces[provOf(pid)];
      if (!bp) return;
      const el = svg("path", { d: bp.d, class: `reach ${cls}` }, L);
      el.style.setProperty("--c", col);
    };
    let active = false;
    if (p.kind === "orders" && this.sel) {
      active = true;
      const ix = this.index.get(this.sel.key)!;
      const here = provOf(ix.choice.unit.at);
      const s = this.sel;
      if (s.mode === "move") {
        mark(here, "origin");
        for (const d of ix.moves.keys()) mark(d);
      } else if (s.mode === "support-unit") {
        mark(here, "origin");
        const targets = new Set([...ix.supHold.keys(), ...ix.supMove.keys()]);
        for (const t of targets) mark(t.split(" ")[1], "pick");
      } else if (s.mode === "support-dest" && s.target) {
        mark(here, "origin");
        if (ix.supHold.has(s.target)) mark(s.target.split(" ")[1], "pick");
        for (const d of ix.supMove.get(s.target)?.keys() ?? []) mark(d);
      } else if (s.mode === "convoy-army") {
        mark(here, "origin");
        for (const t of ix.convoys.keys()) mark(t.split(" ")[1], "pick");
      } else if (s.mode === "convoy-dest" && s.target) {
        mark(here, "origin");
        mark(s.target.split(" ")[1], "pick");
        for (const d of ix.convoys.get(s.target)?.keys() ?? []) mark(d);
      } else if (s.mode === "disrupt") {
        mark(here, "origin");
        for (const d of ix.disrupts.keys()) mark(d);
      }
      this.orderBar(bar, ix);
    } else if (p.kind === "retreats" && this.retreatSel) {
      active = true;
      const c = (p.units as RetreatChoice[]).find((x) => keyOf(x.unit) === this.retreatSel);
      if (c) {
        mark(c.unit.at, "origin");
        for (const d of c.to) mark(d);
        this.retreatBar(bar, c);
      }
    } else if (p.kind === "adjust" && (p.change ?? 0) > 0) {
      const sites = new Set((p.builds ?? []).map((b) => provOf(b.at)));
      for (const s of sites) mark(s, s === this.buildSite ? "origin" : "");
      active = !!this.buildSite;
      if (this.buildSite) this.buildBar(bar, this.buildSite);
    } else if (p.kind === "adjust") {
      for (const d of p.disband ?? []) mark(d.unit.at, this.disbands.has(keyOf(d.unit)) ? "pick" : "");
    }
    view.svg.classList.toggle("has-selection", active);
    const selUnit = p.kind === "orders" && this.sel ? this.index.get(this.sel.key)?.choice.unit
      : p.kind === "retreats" && this.retreatSel ? (p.units as RetreatChoice[]).find((x) => keyOf(x.unit) === this.retreatSel)?.unit : null;
    const ov = this.host.overlay;
    const want = selUnit ? ({ power: p.power, type: selUnit.type, at: selUnit.at } as Unit) : null;
    if ((ov.selected?.at ?? null) !== (want?.at ?? null) || ov.selected?.type !== want?.type) {
      ov.selected = want;
      ov.draw();
    }
  }

  private barHead(bar: HTMLElement, name: string, hint: string) {
    bar.hidden = false;
    const sw = html("span", { class: "dot" }, bar);
    sw.style.background = powerColour(this.power);
    html("strong", {}, bar, name);
    html("span", { class: "muted hint" }, bar, hint);
  }

  private btn(parent: HTMLElement, label: string, on: () => void, opts: { key?: string; active?: boolean; title?: string } = {}) {
    const b = html("button", { class: `chip-btn${opts.active ? " active" : ""}`, type: "button", title: opts.title ?? "" }, parent);
    b.append(document.createTextNode(label));
    if (opts.key) html("kbd", {}, b, opts.key);
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      b.blur(); // keep Enter for "commit"
      on();
    });
    return b;
  }

  private orderBar(bar: HTMLElement, ix: UnitIndex) {
    const s = this.sel!;
    const hints: Record<Mode, string> = {
      move: "click a highlighted province to move, its own to hold",
      "support-unit": "support: click the unit to support",
      "support-dest": "support: click where it goes (its own province: support hold)",
      "convoy-army": "convoy: click the Army to carry",
      "convoy-dest": "convoy: click the Army's destination",
      disrupt: "disrupt: click the target province",
    };
    this.barHead(bar, ix.choice.name, hints[s.mode]);
    const acts = html("span", { class: "bar-actions" }, bar);
    if (s.mode !== "move") {
      this.btn(acts, "Back", () => this.setMode("move"), { key: "Esc" });
    } else {
      if (ix.hold) this.btn(acts, "Hold", () => this.setOrder(ix.hold!), { key: "H" });
      if (ix.supHold.size || ix.supMove.size) this.btn(acts, "Support", () => this.setMode("support-unit"), { key: "S" });
      if (ix.convoys.size) this.btn(acts, "Convoy", () => this.setMode("convoy-army"), { key: "C" });
      if (ix.commons) this.btn(acts, "Commons", () => this.setOrder(ix.commons!), { title: "Work on the global commons (lowers the Crisis)" });
      if (ix.disrupts.size) this.btn(acts, "Disrupt", () => this.setMode("disrupt"), { key: "D" });
      if (ix.sanctions.size) {
        const sel = html("select", { class: "chip-select", "aria-label": "Sanction a power" }, acts);
        html("option", { value: "" }, sel, "Sanction…");
        for (const p of ix.sanctions.keys()) html("option", { value: p }, sel, `Sanction ${this.host.map.powers[p]?.name ?? p}`);
        sel.addEventListener("click", (ev) => ev.stopPropagation());
        sel.addEventListener("change", () => {
          const o = ix.sanctions.get(sel.value);
          if (o) this.setOrder(o);
        });
      }
      if (ix.closes.length) {
        const cur = this.orders.get(s.key);
        const curAgainst = cur?.kind === "close" ? cur.text.split(/\s+/).slice(3) : null;
        const sel = html("select", { class: "chip-select", "aria-label": "Close the chokepoint" }, acts);
        html("option", { value: "" }, sel, curAgainst ? (curAgainst.length ? `Closed to ${curAgainst.join(" ")} · add…` : "Closed to all") : "Close…");
        html("option", { value: "*" }, sel, "Close to everyone");
        for (const p of POWER_ORDER) {
          if (p === this.power) continue;
          html("option", { value: p }, sel, `${curAgainst?.length ? "Also close to" : "Close to"} ${this.host.map.powers[p]?.name ?? p}`);
        }
        sel.addEventListener("click", (ev) => ev.stopPropagation());
        sel.addEventListener("change", () => {
          const u = ix.choice.unit;
          let against: string[] = [];
          if (sel.value !== "*") against = [...new Set([...(curAgainst ?? []), sel.value])].filter(Boolean);
          const text = `${u.type} ${u.at} CLOSE${against.map((x) => ` ${x}`).join("")}`;
          this.setOrder({ text, kind: "close", unit: u, to: null, target: null, about: against });
        });
      }
      if (this.orders.has(s.key)) this.btn(acts, "Clear", () => this.clearOrder(s.key), { key: "⌫", title: "Remove this unit's order (it holds)" });
    }
    const x = html("button", { class: "icon-btn small", type: "button", "aria-label": "Cancel selection" }, bar, "×");
    x.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.cancel();
    });
  }

  private retreatBar(bar: HTMLElement, c: RetreatChoice) {
    this.barHead(bar, c.name, c.to.length ? "dislodged: click where it retreats" : "dislodged with nowhere to go");
    const acts = html("span", { class: "bar-actions" }, bar);
    this.btn(acts, "Disband", () => {
      this.retreats.set(keyOf(c.unit), `${c.unit.type} ${c.unit.at} D`);
      this.nextRetreat();
    }, { key: "X" });
  }

  private buildBar(bar: HTMLElement, pid: string) {
    const p = this.pending!;
    const opts = (p.builds ?? []).filter((b) => provOf(b.at) === pid);
    this.barHead(bar, this.host.map.provinces[pid]?.name ?? pid, "build:");
    const acts = html("span", { class: "bar-actions" }, bar);
    for (const b of opts) {
      const label = `${UNIT_NAMES[b.type]}${b.at.includes("/") ? ` (${b.at.split("/")[1]})` : ""}${b.forward_base ? " · forward base" : ""}`;
      const active = this.builds.some((x) => x.text === b.text);
      this.btn(acts, label, () => this.toggleBuild(b), { key: b.type, active });
    }
    const x = html("button", { class: "icon-btn small", type: "button", "aria-label": "Cancel" }, bar, "×");
    x.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.cancel();
    });
  }

  // ================================================================== interactions
  private unitsAt(pid: string): Unit[] {
    const i = this.host.liveIndex();
    if (i === null) return [];
    return this.host.phase(i).units.filter((u) => provOf(u.at) === pid);
  }

  private select(key: string | null) {
    this.sel = key ? { key, mode: "move" } : null;
    this.host.redraw();
  }

  private setMode(mode: Mode, target?: string) {
    if (!this.sel) return;
    this.sel = { ...this.sel, mode, target };
    this.host.redraw();
  }

  private setOrder(o: Opt) {
    if (!this.sel) return;
    this.orders.set(this.sel.key, o);
    this.sel = null;
    this.changed();
  }

  private clearOrder(key: string) {
    this.orders.delete(key);
    if (this.sel?.key === key) this.sel = null;
    this.changed();
  }

  private toggleBuild(b: BuildChoice) {
    const p = this.pending!;
    const i = this.builds.findIndex((x) => x.text === b.text);
    if (i >= 0) this.builds.splice(i, 1);
    else {
      // one unit per slot: replace a build in the same province and layer
      const layer = (t: string) => (t === "A" || t === "F" ? "M" : "C");
      this.builds = this.builds.filter((x) => !(provOf(x.at) === provOf(b.at) && layer(x.type) === layer(b.type)));
      if (this.builds.length >= (p.change ?? 0)) {
        if ((p.change ?? 0) !== 1) {
          this.flash(`No builds left: remove one first (you have ${p.change}).`);
          return;
        }
        this.builds = []; // a single build: the new choice replaces the old one
      }
      this.builds.push(b);
    }
    this.buildSite = null;
    this.changed();
  }

  private nextRetreat() {
    const p = this.pending!;
    const next = (p.units as RetreatChoice[]).find((c) => !this.retreats.has(keyOf(c.unit)));
    this.retreatSel = next ? keyOf(next.unit) : null;
    this.changed();
  }

  onUnitClick(u: Unit) {
    const p = this.pending;
    if (!p || this.busy) return;
    const key = `${u.type} ${u.at}`;
    if (p.kind === "orders") {
      const s = this.sel;
      if (s) {
        const ix = this.index.get(s.key)!;
        if (s.mode === "support-unit" && (ix.supHold.has(key) || ix.supMove.has(key))) return this.setMode("support-dest", key);
        if (s.mode === "convoy-army" && ix.convoys.has(key)) return this.setMode("convoy-dest", key);
        if (s.key === key && s.mode === "move") return ix.hold ? this.setOrder(ix.hold) : undefined;
        if (s.mode === "move" && u.power === p.power && !ix.moves.has(provOf(u.at))) return this.select(key);
        return this.onProvinceClick(provOf(u.at));
      }
      if (u.power === p.power && this.index.has(key)) return this.select(key);
      return;
    }
    if (p.kind === "retreats") {
      if (u.dislodged && u.power === p.power && (p.units as RetreatChoice[]).some((c) => keyOf(c.unit) === key)) {
        this.retreatSel = key;
        return this.host.redraw();
      }
      return this.onProvinceClick(provOf(u.at));
    }
    if (p.kind === "adjust") {
      if ((p.change ?? 0) < 0 && u.power === p.power) return this.toggleDisband(key);
      return this.onProvinceClick(provOf(u.at));
    }
  }

  private toggleDisband(key: string) {
    const p = this.pending!;
    if (this.disbands.has(key)) this.disbands.delete(key);
    else if (this.disbands.size >= -(p.change ?? 0)) return this.flash(`You disband ${-(p.change ?? 0)}: unmark one first.`);
    else this.disbands.add(key);
    this.changed();
  }

  onProvinceClick(pid: string) {
    const p = this.pending;
    if (!p || this.busy) return;
    if (p.kind === "orders") {
      const s = this.sel;
      if (!s) {
        const mine = this.unitsAt(pid).filter((u) => u.power === p.power && this.index.has(`${u.type} ${u.at}`));
        if (!mine.length) return;
        const unordered = mine.find((u) => !this.orders.has(`${u.type} ${u.at}`));
        const u = unordered ?? mine[0];
        return this.select(`${u.type} ${u.at}`);
      }
      const ix = this.index.get(s.key)!;
      const here = provOf(ix.choice.unit.at);
      switch (s.mode) {
        case "move": {
          if (pid === here) return ix.hold ? this.setOrder(ix.hold) : undefined;
          const mv = ix.moves.get(pid);
          if (mv) return this.setOrder(mv);
          const mine = this.unitsAt(pid).find((u) => u.power === p.power && this.index.has(`${u.type} ${u.at}`));
          if (mine) return this.select(`${mine.type} ${mine.at}`);
          this.flash(`${ix.choice.name.replace(/^\S+ /, "")} can't move to ${this.host.map.provinces[pid]?.name ?? pid}.`);
          return this.cancel();
        }
        case "support-unit": {
          const cands = this.unitsAt(pid).map((u) => `${u.type} ${u.at}`).filter((k) => ix.supHold.has(k) || ix.supMove.has(k));
          if (cands.length) return this.setMode("support-dest", cands[0]);
          return this.flash("That unit can't be supported from here.");
        }
        case "support-dest": {
          const t = s.target!;
          if (pid === provOf(t.split(" ")[1]) && ix.supHold.has(t)) return this.setOrder(ix.supHold.get(t)!);
          const o = ix.supMove.get(t)?.get(pid);
          if (o) return this.setOrder(o);
          return this.flash("Not a destination this unit can support.");
        }
        case "convoy-army": {
          const cands = this.unitsAt(pid).map((u) => `${u.type} ${u.at}`).filter((k) => ix.convoys.has(k));
          if (cands.length) return this.setMode("convoy-dest", cands[0]);
          return this.flash("No Army there that this Fleet can convoy.");
        }
        case "convoy-dest": {
          const o = ix.convoys.get(s.target!)?.get(pid);
          if (o) return this.setOrder(o);
          return this.flash("The convoy can't reach there.");
        }
        case "disrupt": {
          const o = ix.disrupts.get(pid);
          if (o) return this.setOrder(o);
          return this.flash("Out of Disrupt range.");
        }
      }
      return;
    }
    if (p.kind === "retreats") {
      const c = (p.units as RetreatChoice[]).find((x) => keyOf(x.unit) === this.retreatSel);
      if (!c) return;
      const dest = c.to.find((d) => provOf(d) === pid);
      if (dest) {
        this.retreats.set(keyOf(c.unit), `${c.unit.type} ${c.unit.at} R ${dest}`);
        return this.nextRetreat();
      }
      return;
    }
    if (p.kind === "adjust") {
      if ((p.change ?? 0) > 0) {
        if ((p.builds ?? []).some((b) => provOf(b.at) === pid)) {
          this.buildSite = this.buildSite === pid ? null : pid;
          return this.host.redraw();
        }
        this.buildSite = null;
        return this.host.redraw();
      }
      const mine = this.unitsAt(pid).filter((u) => u.power === p.power);
      if (mine.length === 1) this.toggleDisband(`${mine[0].type} ${mine[0].at}`);
    }
  }

  cancel() {
    if (this.sel && this.sel.mode !== "move") return this.setMode("move");
    this.sel = null;
    this.buildSite = null;
    if (this.pending?.kind !== "retreats") this.retreatSel = null;
    this.host.redraw();
  }

  /** Keyboard. Returns true when the key was handled. */
  onKey(ev: KeyboardEvent, live: boolean): boolean {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return false;
    if (document.querySelector(".modal-backdrop")) {
      if (ev.key === "Escape") {
        const m = document.querySelector<HTMLElement>(".modal-backdrop[data-dismiss]");
        if (m) closeModal();
      }
      return true;
    }
    const k = ev.key.toLowerCase();
    if (!live) {
      if (ev.key === "Enter" && this.pending) {
        ev.preventDefault();
        this.host.go(this.host.liveIndex()!);
        return true;
      }
      return false;
    }
    if (ev.key === "Enter") {
      const t = ev.target as HTMLElement;
      if (t?.tagName === "BUTTON" && !t.classList.contains("commit")) return false;
      ev.preventDefault();
      this.commit();
      return true;
    }
    if (ev.key === "Escape") {
      this.cancel();
      return true;
    }
    const p = this.pending;
    if (p?.kind === "orders" && this.sel) {
      const ix = this.index.get(this.sel.key)!;
      if (k === "s" && (ix.supHold.size || ix.supMove.size)) return this.setMode("support-unit"), true;
      if (k === "c" && ix.convoys.size) return this.setMode("convoy-army"), true;
      if (k === "d" && ix.disrupts.size) return this.setMode("disrupt"), true;
      if (k === "h" && ix.hold) return this.setOrder(ix.hold), true;
      if (ev.key === "Backspace" || ev.key === "Delete") return this.clearOrder(this.sel.key), true;
    }
    if (p?.kind === "retreats" && this.retreatSel && k === "x") {
      const c = (p.units as RetreatChoice[]).find((x) => keyOf(x.unit) === this.retreatSel)!;
      this.retreats.set(this.retreatSel, `${c.unit.type} ${c.unit.at} D`);
      this.nextRetreat();
      return true;
    }
    if (p?.kind === "adjust" && this.buildSite && "afci".includes(k) && k.length === 1) {
      const b = (p.builds ?? []).find((x) => provOf(x.at) === this.buildSite && x.type === k.toUpperCase());
      if (b) this.toggleBuild(b);
      return true;
    }
    if (p?.kind === "vote" && ["y", "n", "v"].includes(k)) {
      const choice = { y: "YES", n: "NO", v: "VETO" }[k as "y" | "n" | "v"];
      if (choice !== "VETO" || p.can_veto) {
        this.vote = choice;
        this.changed();
      }
      return true;
    }
    return false;
  }

  leaveLive() {
    this.host.view.layer.reach.replaceChildren();
    const bar = document.getElementById("selection")!;
    bar.classList.remove("play-bar");
    this.fab.hidden = true;
  }

  private flash(msg: string) {
    this.toast.textContent = msg;
    this.toast.hidden = false;
    this.toast.classList.add("hint");
    window.clearTimeout((this.toast as unknown as { _t: number })._t);
    (this.toast as unknown as { _t: number })._t = window.setTimeout(() => {
      if (!this.busy) this.toast.hidden = true;
      this.toast.classList.remove("hint");
    }, 2200);
  }

  // ================================================================== panel
  renderBanner(root: HTMLElement, i: number) {
    const live = this.host.liveIndex();
    const st = this.state;
    if (!st) return;
    const b = html("section", { class: "card play-banner" });
    root.prepend(b);
    const ph = this.host.phase(i);
    if (this.reviewFrom !== null && i >= this.reviewFrom) {
      const n = (live ?? this.host.total()) - this.reviewFrom;
      const k = i - this.reviewFrom + 1;
      html("div", { class: "eyebrow" }, b, `What happened · ${k} of ${n}`);
      html("p", {}, b, ph.kind === "movement" ? "The orders below were resolved together. Arrows show each move; a cross marks one that failed."
        : ph.kind === "retreat" ? "Dislodged units retreated or were disbanded." : "Winter: alignment, the Crisis track, the Assembly and builds.");
    } else {
      html("div", { class: "eyebrow" }, b, "Reviewing an earlier phase");
    }
    const row = html("div", { class: "btn-row" }, b);
    if (live !== null) {
      if (i + 1 < live) {
        const nx = html("button", { class: "btn", type: "button" }, row, "Next ›");
        nx.addEventListener("click", () => this.host.go(i + 1));
      }
      const go = html("button", { class: "btn primary", type: "button" }, row, i + 1 < live ? "Skip to your move" : "Continue to your move");
      html("kbd", {}, go, "⏎");
      go.addEventListener("click", () => this.host.go(live));
    } else if (st.status === "over") {
      const r = html("button", { class: "btn primary", type: "button" }, row, "Final result");
      r.addEventListener("click", () => this.showGameOver());
    } else if (st.status === "thinking") {
      html("span", { class: "muted" }, row, "The other powers are still deciding…");
    }
  }

  renderPanel(root: HTMLElement) {
    root.replaceChildren();
    const p = this.pending!;
    const map = this.host.map;
    const ph = p.phase;
    const head = html("section", { class: "phase-head" }, root);
    const eb = html("div", { class: "eyebrow you-line" }, head);
    const d = html("span", { class: "dot" }, eb);
    d.style.background = powerColour(p.power);
    eb.append(document.createTextNode(` Your move · ${map.powers[p.power]?.name}`));
    const h = html("h2", { class: "phase-title" }, head);
    html("span", { class: "phase-season" }, h, seasonLabel(ph.season, ph.kind));
    html("span", { class: "phase-year" }, h, ` ${ph.year}`);
    const kindLabel: Record<string, string> = {
      orders: "Movement · order your units", retreats: "Retreats · move or disband dislodged units",
      propose: "Assembly · you chair: choose a proposal", vote: "Assembly · vote on the proposal",
      adjust: (p.change ?? 0) > 0 ? "Adjustments · build" : "Adjustments · disband",
      umbrellas: "Umbrellas · declare protection for next year",
    };
    html("div", { class: "phase-kind" }, head, kindLabel[p.kind]);

    const card = html("section", { class: "card decision" }, root);
    switch (p.kind) {
      case "orders": this.ordersCard(card, p); break;
      case "retreats": this.retreatsCard(card, p); break;
      case "adjust": this.adjustCard(card, p); break;
      case "propose": this.proposeCard(card, p); break;
      case "vote": this.voteCard(card, p); break;
      case "umbrellas": this.umbrellaCard(card, p); break;
    }
    const row = html("div", { class: "commit-row" }, card);
    const label = this.commitLabel();
    const c = html("button", { class: "btn primary commit", type: "button" }, row, label);
    html("kbd", {}, c, "⏎");
    c.disabled = this.busy;
    c.addEventListener("click", () => this.commit());
    this.fab.textContent = label;

    this.statusCard(root, p);
    // the usual state cards (Crisis, centres, security)
    const panelState = html("div", { class: "state-cards" }, root);
    this.host.stateCards(panelState, ph);
  }

  private commitLabel(): string {
    const p = this.pending!;
    switch (p.kind) {
      case "orders": return "Commit orders";
      case "retreats": return "Commit retreats";
      case "adjust": return (p.change ?? 0) > 0 ? `Commit builds (${this.builds.length}/${p.change})` : "Commit disbands";
      case "propose": return this.proposal && this.proposal !== "PROPOSE NOTHING" ? "Propose" : "Propose nothing";
      case "vote": return this.vote ? `Vote ${this.vote}` : "Abstain";
      case "umbrellas": return "Declare umbrellas";
    }
  }

  private ordersCard(card: HTMLElement, p: Pending) {
    const units = p.units as UnitChoice[];
    const n = units.filter((u) => this.orders.has(keyOf(u.unit))).length;
    const ch = html("div", { class: "card-head" }, card);
    html("h3", {}, ch, "Your orders");
    html("span", { class: "muted" }, ch, `${n} of ${units.length} ordered`);
    html("p", { class: "help" }, card, "Click a unit, then where it should go. With a unit selected: S support, C convoy, H hold, D disrupt; Esc to cancel. Unordered units hold.");
    const ul = html("ul", { class: "draft-list" }, card);
    for (const c of units) {
      const k = keyOf(c.unit);
      const o = this.orders.get(k);
      const li = html("li", { class: `draft${this.sel?.key === k ? " selected" : ""}`, tabindex: 0 }, ul);
      li.append(utoken(c.unit.type, p.power));
      const body = html("div", { class: "draft-body" }, li);
      html("div", { class: "draft-name" }, body, c.name.replace(/^\S+ /, ""));
      if (o) html("code", {}, body, o.text);
      else html("span", { class: "muted small" }, body, "holds (no order)");
      const why = o ? this.voidReason(o.text) : null;
      if (why) html("div", { class: "void" }, body, `Void: ${why}`);
      if (o) {
        const x = html("button", { class: "icon-btn small", type: "button", "aria-label": `Remove order for ${c.name}` }, li, "×");
        x.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this.clearOrder(k);
        });
      }
      li.addEventListener("click", () => this.select(this.sel?.key === k ? null : k));
      li.addEventListener("keydown", (ev) => {
        if (ev.key === " ") {
          ev.preventDefault();
          this.select(k);
        }
      });
    }
    if (p.withdraw?.length) {
      const w = html("div", { class: "umbrella-actions" }, card);
      html("div", { class: "sub-h" }, w, "Your umbrellas");
      for (const b of p.withdraw) {
        const lab = html("label", { class: "check" }, w);
        const cb = html("input", { type: "checkbox" }, lab) as HTMLInputElement;
        cb.checked = this.withdraw.has(b);
        lab.append(document.createTextNode(` Withdraw the umbrella over ${this.host.map.powers[b]?.name ?? b} (takes effect this turn)`));
        cb.addEventListener("change", () => {
          if (cb.checked) this.withdraw.add(b);
          else this.withdraw.delete(b);
          this.changed();
        });
      }
    }
  }

  private retreatsCard(card: HTMLElement, p: Pending) {
    const ch = html("div", { class: "card-head" }, card);
    html("h3", {}, ch, "Dislodged units");
    html("p", { class: "help" }, card, "Click a dislodged unit, then a highlighted province to retreat there, or Disband (X). Units without an order are disbanded.");
    const ul = html("ul", { class: "draft-list" }, card);
    for (const c of p.units as RetreatChoice[]) {
      const k = keyOf(c.unit);
      const t = this.retreats.get(k);
      const li = html("li", { class: `draft${this.retreatSel === k ? " selected" : ""}`, tabindex: 0 }, ul);
      li.append(utoken(c.unit.type, p.power));
      const body = html("div", { class: "draft-body" }, li);
      html("div", { class: "draft-name" }, body, c.name.replace(/^\S+ /, ""));
      if (t) html("code", {}, body, t);
      else html("span", { class: "muted small" }, body, c.to.length ? `can retreat to ${c.to.map((x) => this.host.map.provinces[provOf(x)]?.name ?? x).join(", ")}` : "no retreat: will be disbanded");
      const why = t ? this.voidReason(t) : null;
      if (why) html("div", { class: "void" }, body, `Void: ${why}`);
      if (t) {
        const x = html("button", { class: "icon-btn small", type: "button", "aria-label": "Clear" }, li, "×");
        x.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this.retreats.delete(k);
          this.retreatSel = k;
          this.changed();
        });
      }
      li.addEventListener("click", () => {
        this.retreatSel = k;
        this.host.redraw();
      });
    }
  }

  private adjustCard(card: HTMLElement, p: Pending) {
    const change = p.change ?? 0;
    const ch = html("div", { class: "card-head" }, card);
    if (change > 0) {
      html("h3", {}, ch, `Build up to ${change}`);
      html("span", { class: "muted" }, ch, `${this.builds.length} chosen`);
      html("p", { class: "help" }, card, "Click a highlighted province, then pick the unit type (A, F, C, I keys). Armies and Fleets build in your original homes; civil units in any owned home or aligned swing state; one forward-base Army a year where you have a civil unit.");
      const ul = html("ul", { class: "draft-list" }, card);
      for (const b of this.builds) {
        const li = html("li", { class: "draft" }, ul);
        li.append(utoken(b.type, p.power));
        const body = html("div", { class: "draft-body" }, li);
        html("div", { class: "draft-name" }, body, `${UNIT_NAMES[b.type]} in ${this.host.map.provinces[provOf(b.at)]?.name ?? b.at}${b.forward_base ? " (forward base)" : ""}`);
        html("code", {}, body, b.text);
        const why = this.voidReason(b.text);
        if (why) html("div", { class: "void" }, body, `Void: ${why}`);
        const x = html("button", { class: "icon-btn small", type: "button", "aria-label": "Remove build" }, li, "×");
        x.addEventListener("click", () => this.toggleBuild(b));
      }
      if (!this.builds.length) html("p", { class: "muted small" }, card, "No builds chosen yet. Unused builds are lost.");
    } else {
      html("h3", {}, ch, `Disband ${-change}`);
      html("span", { class: "muted" }, ch, `${this.disbands.size} chosen`);
      html("p", { class: "help" }, card, "You have more units than centres. Click units to mark them for disbanding; any shortfall is chosen for you (furthest from home first).");
      const ul = html("ul", { class: "draft-list" }, card);
      for (const d of p.disband ?? []) {
        const k = keyOf(d.unit);
        const on = this.disbands.has(k);
        const li = html("li", { class: `draft${on ? " selected" : ""}`, tabindex: 0 }, ul);
        li.append(utoken(d.unit.type, p.power));
        const body = html("div", { class: "draft-body" }, li);
        html("div", { class: "draft-name" }, body, d.name.replace(/^\S+ /, ""));
        html("span", { class: on ? "small strong" : "muted small" }, body, on ? "disband" : "keep");
        li.addEventListener("click", () => this.toggleDisband(k));
      }
    }
  }

  private proposeCard(card: HTMLElement, p: Pending) {
    const ch = html("div", { class: "card-head" }, card);
    html("h3", {}, ch, "Assembly proposal");
    html("span", { class: "muted" }, ch, "you chair");
    html("p", { class: "help" }, card, "As chair (fewest centres) you may put one resolution to the vote. It passes on a weighted majority unless someone vetoes, and lasts next Spring, Fall and Winter builds.");
    const all = p.resolutions ?? [];
    const kinds = [...new Set(all.map((r) => r.split(" ")[0]))];
    const cur = this.proposal?.replace(/^PROPOSE /, "") ?? "NOTHING";
    const curKind = cur === "NOTHING" ? "NOTHING" : cur.split(" ")[0];
    const grid = html("div", { class: "res-grid" }, card);
    const kindSel = html("select", { class: "field", "aria-label": "Resolution" }, grid);
    html("option", { value: "NOTHING" }, kindSel, "Propose nothing");
    for (const k of kinds) html("option", { value: k }, kindSel, k.charAt(0) + k.slice(1).toLowerCase());
    kindSel.value = curKind;
    const argSel = html("select", { class: "field", "aria-label": "Subject" }, grid);
    const args = all.filter((r) => r.split(" ")[0] === curKind).map((r) => r.split(" ")[1]).filter(Boolean);
    argSel.hidden = !args.length;
    for (const a of args) html("option", { value: a }, argSel, this.host.map.powers[a]?.name ?? this.host.map.provinces[a]?.name ?? a);
    if (curKind !== "NOTHING" && args.length) argSel.value = cur.split(" ")[1] ?? args[0];
    kindSel.addEventListener("change", () => {
      const k = kindSel.value;
      if (k === "NOTHING") this.proposal = "PROPOSE NOTHING";
      else {
        const first = all.find((r) => r.split(" ")[0] === k)!;
        this.proposal = `PROPOSE ${first}`;
      }
      this.changed();
    });
    argSel.addEventListener("change", () => {
      this.proposal = `PROPOSE ${kindSel.value} ${argSel.value}`;
      this.changed();
    });
    if (curKind !== "NOTHING") html("p", { class: "res-help" }, card, describeRes(cur, this.host.map));
  }

  private voteCard(card: HTMLElement, p: Pending) {
    const ch = html("div", { class: "card-head" }, card);
    html("h3", {}, ch, "Assembly vote");
    html("span", { class: "muted" }, ch, `your weight ${p.weight}`);
    const chair = html("div", { class: "chair" }, card);
    const dd = html("span", { class: "dot" }, chair);
    dd.style.background = powerColour(p.chair);
    chair.append(document.createTextNode(` ${this.host.map.powers[p.chair ?? ""]?.name ?? p.chair} proposes`));
    html("p", { class: "proposal big" }, card, p.proposal ?? "");
    html("p", { class: "res-help" }, card, describeRes(p.proposal ?? "", this.host.map));
    const row = html("div", { class: "vote-row" }, card);
    for (const [v, label, key] of [["YES", "Yes", "Y"], ["NO", "No", "N"], ["VETO", "Veto", "V"]] as const) {
      const b = html("button", { class: `vote-btn v-${v.toLowerCase()}${this.vote === v ? " active" : ""}`, type: "button", "aria-pressed": String(this.vote === v) }, row, label);
      html("kbd", {}, b, key);
      if (v === "VETO" && !p.can_veto) {
        b.disabled = true;
        b.title = "You have used your veto";
      }
      b.addEventListener("click", () => {
        this.vote = this.vote === v ? null : v;
        this.changed();
      });
    }
    html("p", { class: "muted small" }, card, p.can_veto ? "One veto per game: a veto kills the proposal outright." : "Your veto is spent.");
  }

  private umbrellaCard(card: HTMLElement, p: Pending) {
    const ch = html("div", { class: "card-head" }, card);
    html("h3", {}, ch, "Nuclear umbrellas");
    html("p", { class: "help" }, card, "Military units of other powers can't enter the homes of a power under your umbrella. Declare who you protect next year.");
    if (!p.candidates?.length) html("p", { class: "muted small" }, card, "No power can be protected.");
    for (const c of p.candidates ?? []) {
      const lab = html("label", { class: "check" }, card);
      const cb = html("input", { type: "checkbox" }, lab) as HTMLInputElement;
      cb.checked = this.umbrellas.has(c);
      const dd = html("span", { class: "dot" }, lab);
      dd.style.background = powerColour(c);
      lab.append(document.createTextNode(` ${this.host.map.powers[c]?.name ?? c}${p.current?.includes(c) ? " (current)" : ""}`));
      cb.addEventListener("change", () => {
        if (cb.checked) this.umbrellas.add(c);
        else this.umbrellas.delete(c);
        this.changed();
      });
    }
  }

  private statusCard(root: HTMLElement, p: Pending) {
    const s = p.status;
    const map = this.host.map;
    const card = html("section", { class: "card you-card" }, root);
    const ch = html("div", { class: "card-head" }, card);
    html("h3", {}, ch, "Your position");
    html("span", { class: "muted" }, ch, map.powers[p.power]?.nuclear ? "nuclear power" : "not nuclear");
    const stats = html("div", { class: "stats" }, card);
    for (const [k, v] of [["Centres", s.centres], ["Units", s.units], ["Swing", s.swing], ["Vote", s.vote_weight]] as const) {
      const d = html("div", { class: "stat" }, stats);
      html("b", {}, d, String(v));
      html("span", {}, d, k);
    }
    const kv = (k: string, v: string) => {
      const r = html("div", { class: "kv" }, card);
      html("span", { class: "k" }, r, k);
      html("span", { class: "v" }, r, v);
    };
    const names = (xs: string[]) => xs.map((x) => map.powers[x]?.name ?? x).join(", ");
    if (!s.nuclear) kv("Umbrella", s.protected_by.length ? `protected by ${names(s.protected_by)}` : "unprotected: foreign Military can take your homes");
    else kv("Umbrellas", s.protecting.length ? `you protect ${names(s.protecting)}` : "you protect no one");
    kv("Veto", s.vetoes_left ? "1 left" : "used");
    for (const e of s.effects) html("div", { class: "effect" }, card, e);
  }

  // ================================================================== chip, modals
  private updateChip() {
    const st = this.state;
    const c = this.statusChip;
    if (!st) {
      c.hidden = true;
      return;
    }
    c.hidden = false;
    c.replaceChildren();
    const d = html("span", { class: "dot" }, c);
    d.style.background = powerColour(st.power);
    const map = this.host.map;
    const last = st.pending?.phase ?? st.record.phases[st.record.phases.length - 1];
    const centres = last ? Object.entries(last.ownership).filter(([pid, o]) => o === st.power && map.provinces[pid]?.centre !== "none").length : 0;
    html("strong", {}, c, ` ${st.power}`);
    html("span", { class: "muted" }, c, ` · ${centres} centres`);
    const prot = st.pending?.status.protected_by ?? [];
    if (prot.length) {
      const u = html("span", { class: "umb", title: `Protected by ${prot.join(", ")}` }, c);
      u.textContent = ` ☂ ${prot.join(" ")}`;
    }
    c.title = `You play ${map.powers[st.power]?.name}`;
    const nb = html("button", { class: "linkish", type: "button", title: "Start a new game" }, c, "New");
    nb.addEventListener("click", () => this.showStart());
  }

  showStart() {
    const map = this.host.map;
    const m = modal("Choose your power", true);
    m.classList.add("start");
    html("p", { class: "lede" }, m, "Seven powers, one board. You play one; six bots play the rest. Align swing states, keep the Crisis track below 10, and reach Primacy (9 aligned swing states) or lead when the clock runs out.");
    const grid = html("div", { class: "power-grid" }, m);
    let chosen = this.power ?? "USA";
    const cards: HTMLElement[] = [];
    for (const p of POWER_ORDER) {
      const P = map.powers[p];
      if (!P) continue;
      const card = html("button", { class: `power-card${p === chosen ? " active" : ""}`, type: "button", "aria-pressed": String(p === chosen) }, grid);
      card.style.setProperty("--c", powerColour(p));
      const h = html("div", { class: "pc-head" }, card);
      html("span", { class: "pc-name" }, h, P.name);
      html("span", { class: "pc-tag" }, h, P.nuclear ? "nuclear" : "non-nuclear");
      html("div", { class: "pc-homes" }, card, P.homes.map((x) => map.provinces[x]?.name ?? x).join(" · "));
      const us = html("div", { class: "pc-units" }, card);
      for (const u of map.start.units.filter((x) => x.power === p)) {
        const s = html("span", { class: "pc-unit" }, us);
        s.append(utoken(u.type, p));
        s.append(document.createTextNode(` ${map.provinces[u.at]?.name ?? u.at}`));
      }
      html("p", { class: "pc-char" }, card, CHARACTER[p] ?? "");
      card.addEventListener("click", () => {
        chosen = p;
        cards.forEach((c) => {
          c.classList.remove("active");
          c.setAttribute("aria-pressed", "false");
        });
        card.classList.add("active");
        card.setAttribute("aria-pressed", "true");
      });
      card.addEventListener("dblclick", () => go());
      cards.push(card);
    }
    const opts = html("div", { class: "start-opts" }, m);
    const seedL = html("label", { class: "opt" }, opts, "Seed ");
    const seed = html("input", { class: "field", type: "number", placeholder: "random", inputmode: "numeric" }, seedL) as HTMLInputElement;
    const lenL = html("label", { class: "opt" }, opts, "Length ");
    const len = html("select", { class: "field" }, lenL) as HTMLSelectElement;
    for (const [v, t] of [["", "Full game (to 2040)"], ["2035", "Medium (to 2035)"], ["2032", "Short (to 2032)"]]) html("option", { value: v }, len, t);
    const allyL = html("label", { class: "opt check" }, opts);
    const ally = html("input", { type: "checkbox" }, allyL) as HTMLInputElement;
    allyL.append(document.createTextNode(" Bots form alliances with each other"));
    const row = html("div", { class: "btn-row end" }, m);
    const go = () => this.create(chosen, seed.value, ally.checked, len.value);
    const startBtn = html("button", { class: "btn primary big", type: "button" }, row, "Start game");
    startBtn.addEventListener("click", go);
    // ongoing games on this server
    api<{ id: string; power: string; status: string; year: number; season: string }[]>("GET", "/api/games").then((games) => {
      const open = games.filter((g) => g.status === "waiting" || g.status === "thinking" || g.status === "over");
      if (!open.length) return;
      const res = html("div", { class: "resume" });
      m.insertBefore(res, row);
      html("div", { class: "sub-h" }, res, "Games on this server");
      for (const g of open.slice(0, 5)) {
        const b = html("button", { class: "chip", type: "button" }, res);
        const dd = html("span", { class: "dot" }, b);
        dd.style.background = powerColour(g.power);
        b.append(document.createTextNode(` ${g.power} · ${g.status === "over" ? "finished" : `${g.season} ${g.year}`}`));
        b.addEventListener("click", () => {
          closeModal();
          const url = new URL(location.href);
          url.searchParams.set("play", g.id);
          history.replaceState(null, "", url);
          this.state = null;
          this.pending = null;
          this.overShown = g.status === "over";
          this.start(g.id);
        });
      }
    }).catch(() => {
      html("p", { class: "void" }, m, "Can't reach the game server. Start it with `brink serve` (and open the URL it prints).");
    });
    startBtn.focus();
  }

  showGameOver() {
    const st = this.state;
    const r = st?.record.result;
    if (!st || !r) return;
    const map = this.host.map;
    const m = modal("Game over", true);
    m.classList.add("over");
    const you = st.power;
    const scores = r.scores ?? {};
    const ranked = POWER_ORDER.filter((p) => map.powers[p]).sort((a, b) => (scores[b] ?? 0) - (scores[a] ?? 0));
    const rank = 1 + ranked.filter((p) => (scores[p] ?? 0) > (scores[you] ?? 0)).length;
    const tied = ranked.filter((p) => p !== you && (scores[p] ?? 0) === (scores[you] ?? 0)).length;
    const headline = r.kind === "primacy" ? (r.winner === you ? "Primacy: you win" : `Primacy for ${map.powers[r.winner ?? ""]?.name ?? r.winner}`)
      : r.kind === "collapse" ? "Collapse: the Crisis reached 10 and everyone loses" : `Time: the game ends after Winter ${r.year ?? ""}`;
    html("p", { class: "lede strong" }, m, headline);
    html("p", { class: "muted" }, m, r.kind === "time" ? `Scores are shares of the squared centre counts. You finish ${tied ? "joint " : ""}${ordinal(rank)} of ${ranked.length}.` : "");
    const tbl = html("table", { class: "scores" }, m);
    const hr = html("tr", {}, html("thead", {}, tbl));
    for (const t of ["", "Power", "Centres", "Score"]) html("th", {}, hr, t);
    const tb = html("tbody", {}, tbl);
    for (const p of ranked) {
      const tr = html("tr", { class: p === you ? "you" : "" }, tb);
      const c = html("td", {}, tr);
      const dd = html("span", { class: "dot" }, c);
      dd.style.background = powerColour(p);
      html("td", {}, tr, `${map.powers[p].name}${p === you ? " (you)" : ""}`);
      html("td", { class: "num" }, tr, String(r.centres?.[p] ?? ""));
      html("td", { class: "num" }, tr, (scores[p] ?? 0).toFixed(1));
    }
    const row = html("div", { class: "btn-row end" }, m);
    const rev = html("button", { class: "btn", type: "button" }, row, "Review the game");
    rev.addEventListener("click", () => {
      closeModal();
      this.host.go(0);
    });
    const nw = html("button", { class: "btn primary", type: "button" }, row, "New game");
    nw.addEventListener("click", () => {
      closeModal();
      this.showStart();
    });
  }

  private showError(msg: string) {
    const m = modal("Something went wrong", true);
    html("pre", { class: "err" }, m, msg.slice(-1500));
    const row = html("div", { class: "btn-row end" }, m);
    const b = html("button", { class: "btn primary", type: "button" }, row, "Start a new game");
    b.addEventListener("click", () => {
      closeModal();
      this.showStart();
    });
  }
}

// ------------------------------------------------------------------ helpers
async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`${method} ${path}: HTTP ${r.status} (not JSON — is \`brink serve\` running?)`);
  }
  if (!r.ok) throw new Error(`${r.status} ${(data as { error?: string })?.error ?? r.statusText}`);
  return data as T;
}

function indexUnit(c: UnitChoice): UnitIndex {
  const ix: UnitIndex = {
    choice: c, hold: null, moves: new Map(), supHold: new Map(), supMove: new Map(), convoys: new Map(),
    commons: null, sanctions: new Map(), disrupts: new Map(), closes: [],
  };
  for (const o of c.options) {
    switch (o.kind) {
      case "hold": ix.hold = o; break;
      case "move": {
        const d = provOf(o.to!);
        if (!ix.moves.has(d)) ix.moves.set(d, o);
        break;
      }
      case "support": {
        const t = keyOf(o.target!);
        if (o.to === null) ix.supHold.set(t, o);
        else {
          if (!ix.supMove.has(t)) ix.supMove.set(t, new Map());
          ix.supMove.get(t)!.set(provOf(o.to), o);
        }
        break;
      }
      case "convoy": {
        const t = keyOf(o.target!);
        if (!ix.convoys.has(t)) ix.convoys.set(t, new Map());
        ix.convoys.get(t)!.set(provOf(o.to!), o);
        break;
      }
      case "commons": ix.commons = o; break;
      case "sanction": ix.sanctions.set(String(o.about), o); break;
      case "disrupt": ix.disrupts.set(provOf(o.to!), o); break;
      case "close": ix.closes.push(o); break;
    }
  }
  return ix;
}

function livePhase(ph: Phase): Phase {
  return {
    ...ph, orders: [], units: ph.units ?? [], closed: ph.closed ?? {}, umbrellas: ph.umbrellas ?? [],
    events: [], assembly: null,
  };
}

function utoken(type: UnitType, power: string): SVGSVGElement {
  const s = svg("svg", { class: "utoken", viewBox: "0 0 20 20", width: 18, height: 18, "aria-hidden": "true" });
  const c = powerColour(power);
  const r = 7;
  if (type === "A") svg("circle", { cx: 10, cy: 10, r, fill: c }, s);
  else {
    const pts = type === "F" ? [[0, -r * 1.18], [r * 1.12, r * 0.78], [-r * 1.12, r * 0.78]]
      : type === "C" ? [[0, -r * 1.25], [r * 1.25, 0], [0, r * 1.25], [-r * 1.25, 0]]
      : [0, 60, 120, 180, 240, 300].map((a) => [r * 1.12 * Math.cos(((a + 30) * Math.PI) / 180), r * 1.12 * Math.sin(((a + 30) * Math.PI) / 180)]);
    svg("polygon", { points: pts.map(([x, y]) => `${10 + x},${10 + y + (type === "F" ? 1 : 0)}`).join(" "), fill: c }, s);
  }
  const t = svg("text", { x: 10, y: type === "F" ? 13.4 : 12.6, class: "utoken-glyph" }, s);
  t.textContent = type;
  return s;
}

function describeRes(res: string, map: MapData): string {
  const [kind, arg] = res.replace(/^PROPOSE /, "").split(" ");
  const name = arg ? map.powers[arg]?.name ?? map.provinces[arg]?.name ?? arg : "";
  const help = RES_HELP[kind] ?? "";
  return name ? `${kind.charAt(0)}${kind.slice(1).toLowerCase()} on ${name}: ${help}.` : `${kind.charAt(0)}${kind.slice(1).toLowerCase()}: ${help}.`;
}

function ordinal(n: number): string {
  return `${n}${n === 1 ? "st" : n === 2 ? "nd" : n === 3 ? "rd" : "th"}`;
}

function modal(title: string, dismissable = false): HTMLElement {
  closeModal();
  const back = html("div", { class: "modal-backdrop" }, document.body);
  if (dismissable) back.dataset.dismiss = "1";
  const m = html("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title }, back);
  const h = html("div", { class: "modal-head" }, m);
  html("h2", {}, h, title);
  if (dismissable) {
    const x = html("button", { class: "icon-btn small", type: "button", "aria-label": "Close" }, h, "×");
    x.addEventListener("click", closeModal);
    back.addEventListener("click", (ev) => {
      if (ev.target === back) closeModal();
    });
  }
  return m;
}

function closeModal() {
  document.querySelectorAll(".modal-backdrop").forEach((e) => e.remove());
}

