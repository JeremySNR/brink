// Play mode: the decision UI over the board, driven by a PlayBackend.
//
// Two backends: LocalBackend talks to `brink serve` (src/brink/server.py: one human against six bots, one
// decision at a time); HostedBackend (src/hosted/game.ts) talks to the hosted multiplayer API, where a
// decision is saved, marked ready, and resolved when every player is ready or the deadline passes.
// The backend publishes the open decision ("pending"); this controller turns it into board interactions
// (Backstabbr-style order entry), drafts and submission. The board shows the game record's
// phases followed by one "live" phase: the current position with the drafted orders on it.
import type { BoardView } from "./board";
import type { Overlay } from "./overlay";
import { codeCounter, counterSvg } from "./counter";
import { RULE, aidSection } from "./panel";
import { POWER_ORDER, UNIT_NAMES } from "./theme";
import type { GameRecord, MapData, Order, Phase, Unit, UnitType } from "./types";
import { html, normaliseOrder, seasonLabel, svg } from "./util";
import { storeSandbox } from "./workshop";

// ------------------------------------------------------------------ API shapes
export interface URef { type: UnitType; at: string }
export interface Opt {
  text: string;
  kind: string;
  unit: URef | null;
  to: string | null;
  target: URef | null;
  about?: unknown;
}
export interface UnitChoice { unit: URef; name: string; options: Opt[] }
export interface RetreatChoice { unit: URef; name: string; to: string[] }
export interface BuildChoice { type: UnitType; at: string; text: string; forward_base: boolean }
export interface DisbandChoice { unit: URef; name: string; text: string }
export interface Status {
  centres: number; units: number; swing: number; protected_by: string[]; protecting: string[];
  vetoes_left: number; vote_weight: number; nuclear: boolean; effects: string[];
  briefing?: { primacy: number; threatened: string[]; contested: string[];
    crisis: { current: number; baseline: number; closed: number; clash: number; commons: number;
      per_point: number; projected: number; applied: boolean } };
}
/** The open decision. Local play asks one kind at a time; hosted play asks a whole Winter at once
 * (kind "winter": the vote, adjustments and umbrellas together, flagged in `parts`). */
export interface Pending {
  id: number;
  kind: "orders" | "retreats" | "propose" | "vote" | "adjust" | "umbrellas" | "winter";
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
  change_if_passed?: number;
  builds?: BuildChoice[];
  disband?: DisbandChoice[];
  candidates?: string[];
  current?: string[];
  parts?: { vote: boolean; adjust: boolean; umbrellas: boolean };
}
export interface PlayRecord extends GameRecord {
  human?: string;
  result: (GameRecord["result"] & { year?: number; centres?: Record<string, number> }) | null;
}
export interface PlayState {
  id: string; power: string | null; seed?: number; status: "waiting" | "thinking" | "over" | "error" | "stopped";
  record: PlayRecord; pending: Pending | null; error: string | null;
  /** The live position when you have no decision open (hosted: others are deciding). */
  position?: Phase | null;
  /** Hosted: your saved orders for the open decision and whether you are ready. */
  saved?: string[] | null;
  ready?: boolean;
}
export interface Check { text: string; ok: boolean; reason: string | null }

/** Where decisions come from and go to. */
export interface PlayBackend {
  /** local: `brink serve`; hosted: the multiplayer API; tutorial: the lessons' sandbox (src/tutorial.ts). */
  readonly kind: "local" | "hosted" | "tutorial" | "practice" | "sandbox";
  /** Load a game (null: none to resume; local play then shows the start screen). */
  load(param: string | null): Promise<PlayState | null>;
  refresh(): Promise<PlayState>;
  validate(texts: string[]): Promise<Check[]>;
  /** Send the open decision. Local: commits it. Hosted: saves it, ready or not. */
  submit(p: Pending, texts: string[], ready: boolean): Promise<PlayState>;
  lastYear(): number;
  /** Milliseconds until the next refresh, or null for none. */
  pollDelay(st: PlayState, sinceCommit: number): number | null;
  // ---- hosted extras
  /** Player-aid sections under the decision (players, press). */
  sections?(root: HTMLElement, live: boolean): void;
  /** Who the phase is waiting for, in words. */
  waitingFor?(): string;
  deadline?(): Date | null;
  /** First phase index this browser hasn't seen, and a hook to record what has been seen. */
  unseenFrom?(): number | null;
  markSeen?(n: number): void;
  exit?(): void;
  snapshot?(): Promise<Record<string, unknown>>;
  controls?(root: HTMLElement): void;
  draftChanged?(p: Pending, texts: string[]): void;
  draftOrders?(): Order[];
}

const isAdjust = (p: Pending) => p.kind === "adjust" || (p.kind === "winter" && !!p.parts?.adjust);
const isVote = (p: Pending) => p.kind === "vote" || (p.kind === "winter" && !!p.parts?.vote);
const isUmbrellas = (p: Pending) => p.kind === "umbrellas" || (p.kind === "winter" && !!p.parts?.umbrellas);

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

// One hand-written line of character per power. It makes no countable claims: units, homes and umbrellas
// on the start screen are read from map.json `start`, so they can't go stale.
export const CHARACTER: Record<string, string> = {
  USA: "The Americas are its backyard, and both oceans are within reach.",
  CHN: "Starts with money and influence rather than armies; Asia is the first market.",
  EU: "Sits at the centre of the board, with Ukraine, Turkey and Africa next door.",
  RUS: "Spread across the largest landmass: strong at home, short of reach.",
  IND: "Faces the Indian Ocean, with Pakistan, Iran and the south in reach.",
  JPN: "An island broker whose strength is money and influence.",
  GLF: "Rich, beside the Strait of Hormuz, with friends to buy.",
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
  private state: PlayState | null = null;
  private pending: Pending | null = null;
  private busy = false;
  private reviewFrom: number | null = null; // first phase the player hasn't seen yet
  private overShown = false;
  private pollTimer = 0;
  private lastCommit = 0;
  private sig = "";

  // drafts for the open decision
  private orders = new Map<string, Opt>();
  private withdraw = new Set<string>();
  private retreats = new Map<string, string>();
  private builds: BuildChoice[] = [];
  private disbands = new Set<string>();
  private proposal: string | null = null;
  private vote: string | null = null;
  private umbrellas = new Set<string>();
  // hosted: the decision is saved as you go and resolves when everyone is ready
  private ready = false;
  private saveTimer = 0;
  private saveState: "saved" | "saving" | "unsaved" | "error" = "saved";
  private savedAt: Date | null = null;

  // selection
  private index = new Map<string, UnitIndex>();
  private sel: { key: string; mode: Mode; target?: string } | null = null;
  private retreatSel: string | null = null;
  private buildSite: string | null = null;

  private statusChip: HTMLElement;
  private fab: HTMLButtonElement;
  private toast: HTMLElement;
  readonly backend: PlayBackend;

  constructor(private host: PlayHost, backend?: PlayBackend) {
    this.backend = backend ?? new LocalBackend();
    const top = document.querySelector(".top-actions")!;
    this.statusChip = html("span", { class: "you-chip", id: "play-status", hidden: true });
    top.prepend(this.statusChip);
    const stage = document.getElementById("stage")!;
    this.fab = html("button", { class: "btn commit-btn fab", type: "button", hidden: true }, stage, "Commit orders");
    this.fab.addEventListener("click", () => this.commit());
    this.toast = html("div", { class: "play-toast", role: "status", "aria-live": "polite", hidden: true }, stage);
    host.overlay.showHolds = true;
    (window as unknown as { __brink: unknown }).__brink = this; // dev/testing hook (see web/README.md)
  }

  get hosted() {
    return this.backend.kind === "hosted";
  }

  // ================================================================== lifecycle
  async start(param: string | null) {
    try {
      const st = await this.backend.load(param);
      if (st) {
        this.reviewFrom = null;
        this.accept(st, true);
        this.focusHome();
        return;
      }
    } catch (e) {
      if (this.backend.kind !== "local") return this.showError(String(e));
      /* unknown game (server restarted): fall through to a new one */
    }
    if (!this.hosted) this.showStart();
  }

  private async create(power: string, seed: string, alliances: boolean, lastYear: string) {
    const local = this.backend as LocalBackend;
    this.setBusy(true, "Setting up the board…");
    try {
      const st = await local.create(power, seed, alliances, lastYear);
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

  /** The game's final year, for the turn record track. */
  lastYear(): number {
    const r = this.state?.record.result;
    if (r?.year && r.kind === "time") return r.year;
    return this.backend.lastYear();
  }

  /** Zoom to the player's home provinces. */
  focusHome() {
    const homes = this.host.map.powers[this.power ?? ""]?.homes ?? [];
    if (homes.length) this.host.view.zoomToProvinces(homes);
  }

  /** Take a new server state: rebuild the record + live phase and move the view. */
  private accept(st: PlayState, fresh = false) {
    const n = st.record.phases.length;
    const sig = `${n}|${st.pending?.id ?? "-"}|${st.status}|${st.ready ? 1 : 0}|${st.position?.name ?? ""}`;
    this.schedulePoll(st);
    if (!fresh && sig === this.sig) {
      this.paintHostedWaiting();
      return; // keep drafts, focus, and the board intact while other players' status changes
    }
    this.sig = sig;
    const before = fresh ? null : (this.state?.record.phases.length ?? 0);
    this.state = st;
    this.id = st.id;
    this.power = st.power;
    this.host.overlay.you = st.power;
    const newDecision = fresh || st.pending?.id !== this.pending?.id || st.pending?.kind !== this.pending?.kind || st.power !== this.pending?.power;
    this.pending = st.pending;
    this.ready = !!st.ready;
    if (newDecision) {
      this.resetDrafts(st.saved ?? null);
      this.saveState = "saved";
      this.savedAt = null;
    }
    const rec: PlayRecord = st.record;
    const livePos = st.pending ? st.pending.phase : st.position ?? null;
    const live = livePos ? livePhase(livePos) : null;
    let goTo = live ? n : Math.max(0, n - 1);
    if (before !== null && n > before) {
      this.reviewFrom = before;
      goTo = before; // step through what happened since the last decision
    } else if (fresh) {
      const unseen = this.backend.unseenFrom?.() ?? null;
      this.reviewFrom = unseen !== null && unseen < n ? unseen : null;
      if (this.reviewFrom !== null) goTo = this.reviewFrom;
    }
    this.host.setRecord({ ...rec, phases: [...rec.phases] }, live, goTo);
    this.updateChip();
    if (st.status === "error") this.showError(st.error ?? "the game stopped with an error");
    if (st.status === "over" && !this.overShown) {
      this.overShown = true;
      if (!fresh || this.reviewFrom === null) this.showGameOver();
    }
  }

  private schedulePoll(st: PlayState) {
    window.clearTimeout(this.pollTimer);
    const d = this.backend.pollDelay(st, Date.now() - this.lastCommit);
    if (d !== null) this.pollTimer = window.setTimeout(() => this.poll(), d);
  }

  async poll() {
    if (!this.id) return;
    try {
      this.accept(await this.backend.refresh());
    } catch (e) {
      if (this.hosted) {
        // a dropped connection shouldn't throw a modal at someone reading the board: try again later
        window.clearTimeout(this.pollTimer);
        this.pollTimer = window.setTimeout(() => this.poll(), 30000);
        return;
      }
      this.showError(String(e));
    }
  }

  /** Local: commit the decision. Hosted: save it and mark yourself ready. */
  async commit() {
    const p = this.pending;
    if (!p || this.busy || !this.id) return;
    const texts = this.texts();
    window.clearTimeout(this.saveTimer);
    this.setBusy(true, this.hosted ? "Saving your orders…" : p.kind === "orders" ? "Orders committed. The other powers are moving…" : "Committed…");
    try {
      const st = await this.backend.submit(p, texts, true);
      this.lastCommit = Date.now();
      try {
        localStorage.removeItem(this.draftKey());
      } catch {
        /* ignore */
      }
      this.saveState = "saved";
      this.savedAt = new Date();
      this.accept(st);
      if (this.hosted && this.pending?.id === p.id) this.flash("Ready. The phase resolves when every player is ready, or at the deadline.");
    } catch (e) {
      // 409: the decision moved on (another tab, or the deadline passed): resync
      await this.poll();
      if (!String(e).includes("409")) this.showError(String(e));
    } finally {
      this.setBusy(false);
    }
  }

  /** Hosted: take back "ready" and keep editing. */
  async unready() {
    const p = this.pending;
    if (!p || this.busy) return;
    this.setBusy(true, "Saving…");
    try {
      this.accept(await this.backend.submit(p, this.texts(), false));
      this.ready = false;
      this.host.redraw();
    } catch (e) {
      await this.poll();
      if (!String(e).includes("409")) this.showError(String(e));
    } finally {
      this.setBusy(false);
    }
  }

  /** Hosted: save drafts to the server as you go (not ready). */
  private scheduleSave() {
    if (!this.hosted || !this.pending) return;
    this.saveState = "unsaved";
    window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => this.save(), 1200);
  }

  private async save() {
    const p = this.pending;
    if (!p || this.busy) return;
    const wasReady = this.ready;
    this.saveState = "saving";
    this.paintSaveState();
    try {
      const st = await this.backend.submit(p, this.texts(), false);
      this.saveState = "saved";
      this.savedAt = new Date();
      this.ready = false;
      if (st.pending?.id !== p.id) this.accept(st);
      else {
        this.state = st;
        this.sig = `${st.record.phases.length}|${st.pending?.id ?? "-"}|${st.status}|0|${st.position?.name ?? ""}`;
        if (wasReady) {
          this.flash("Orders changed: you are no longer ready.");
          this.host.redraw();
        } else this.paintSaveState();
      }
    } catch (e) {
      this.saveState = "error";
      this.paintSaveState();
      if (String(e).includes("409")) await this.poll();
    }
  }

  /** Update the "saved" note in the foot without re-rendering the panel. */
  private paintSaveState() {
    const el = document.getElementById("save-state");
    if (el) el.textContent = this.saveNote();
  }

  private saveNote(): string {
    switch (this.saveState) {
      case "saving": return "Saving…";
      case "unsaved": return "Unsaved changes";
      case "error": return "Not saved: check your connection";
      default: return this.savedAt || this.state?.saved ? `Orders saved${this.savedAt ? ` ${this.savedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}` : "Nothing saved yet";
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
  private resetDrafts(serverSaved: string[] | null = null) {
    this.orders.clear();
    this.withdraw.clear();
    this.retreats.clear();
    this.builds = [];
    this.disbands.clear();
    this.proposal = null;
    this.vote = null;
    this.umbrellas = new Set(this.pending?.current ?? []);
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
    // restore an unsent draft (page reload), else what the server holds for you
    let local: string[] | null = null;
    try {
      const saved = localStorage.getItem(this.draftKey());
      if (saved) local = JSON.parse(saved) as string[];
    } catch {
      /* ignore */
    }
    const texts = this.backend.kind === "sandbox" ? serverSaved : local ?? serverSaved;
    if (texts) this.applyTexts(texts);
  }

  private draftKey() {
    return `brink.play.${this.id}.${this.pending?.id}${this.backend.kind === "sandbox" ? `.${this.power}` : ""}`;
  }

  /** Re-apply order texts to the drafts (restoring a saved draft). */
  private applyTexts(texts: string[]) {
    const p = this.pending;
    if (!p) return;
    let umbrellaSeen = false;
    for (const t of texts) {
      const T = t.toUpperCase().trim();
      if (p.kind === "winter") {
        // one list carries the whole Winter: sort it out by kind
        if (/^(VOTE )?(YES|NO|VETO)$/.test(T)) this.vote = T.replace("VOTE ", "");
        else if (T.includes("UMBRELLA")) {
          if (!umbrellaSeen) this.umbrellas.clear();
          umbrellaSeen = true;
          const who = T.split(/\s+/)[2];
          if (who && who !== "NONE") this.umbrellas.add(who);
        } else {
          const b = p.builds?.find((x) => x.text === T);
          if (b) this.builds.push(b);
          const d = p.disband?.find((x) => x.text === T);
          if (d) this.disbands.add(keyOf(d.unit));
        }
      } else if (p.kind === "orders") {
        if (T.includes(" WITHDRAW ")) this.withdraw.add(T.split(/\s+/)[2]);
        else {
          // Restore the submitted text, including orders absent from the legal-options list.
          const o = draftOrder(T);
          if (o.unit && this.index.has(keyOf(o.unit))) this.orders.set(keyOf(o.unit), o);
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

  /** Replace the drafts with these order texts (the tutorial's "Show me"). */
  setDraft(texts: string[]) {
    if (!this.pending || this.busy) return;
    try {
      localStorage.removeItem(this.draftKey()); // or resetDrafts would restore it
    } catch {
      /* ignore */
    }
    this.resetDrafts();
    this.applyTexts(texts);
    this.changed();
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
      case "winter": {
        const out: string[] = [];
        if (p.parts?.vote && this.vote) out.push(`VOTE ${this.vote}`);
        if (p.parts?.adjust) {
          if ((p.change ?? 0) > 0) out.push(...this.builds.map((b) => b.text));
          else if ((p.change ?? 0) < 0) out.push(...(p.disband ?? []).filter((d) => this.disbands.has(keyOf(d.unit))).map((d) => d.text));
        }
        if (p.parts?.umbrellas) {
          // the full list for next year; an empty list must say so (silence keeps the current ones)
          if (this.umbrellas.size) out.push(...[...this.umbrellas].map((b) => `${p.power} UMBRELLA ${b}`));
          else out.push(`${p.power} UMBRELLA NONE`);
        }
        return out;
      }
    }
    return [];
  }

  private changed() {
    if (this.pending) this.backend.draftChanged?.(this.pending, this.texts());
    try {
      localStorage.setItem(this.draftKey(), JSON.stringify(this.texts()));
    } catch {
      /* ignore */
    }
    this.host.redraw();
    this.scheduleSave();
  }

  /** Switch sandbox power or restore a copied decision without retaining the old power's draft. */
  replaceState(st: PlayState) {
    this.overShown = false;
    this.accept(st, true);
    document.getElementById("panel")?.scrollTo({ top: 0 });
  }

  private briefing(root: HTMLElement, p: Pending) {
    const b = p.status.briefing;
    if (!b || this.backend.kind === "tutorial") return;
    const card = aidSection(root, null, "Turn briefing", undefined, "turn-briefing");
    html("p", { class: "brief-goal" }, card, `${p.status.swing} of ${b.primacy} swing states toward victory`);
    const places = (ids: string[]) => ids.map((id) => this.host.map.provinces[id]?.name ?? id).join(", ");
    if (b.contested.length) html("p", { class: "help" }, card, `Contested: ${places(b.contested)}. These states currently give you no supply.`);
    html("p", { class: "help" }, card, b.threatened.length
      ? `In reach of other powers: ${places(b.threatened)}. They could move here; this is not a prediction.`
      : p.status.swing ? "No other power can currently move directly into your aligned swing states." : "Send civil units into swing states and hold them through Winter to gain alignment.");
    const previous = this.state?.record.phases.at(-1);
    if (previous) {
      const gained = Object.keys(p.phase.ownership).filter((id) => p.phase.ownership[id] === p.power && previous.ownership[id] !== p.power);
      const lost = Object.keys(previous.ownership).filter((id) => previous.ownership[id] === p.power && p.phase.ownership[id] !== p.power);
      if (gained.length || lost.length) html("p", { class: "help" }, card,
        `Since the last resolved phase: ${[gained.length ? `gained ${places(gained)}` : "", lost.length ? `lost ${places(lost)}` : ""].filter(Boolean).join("; ")}.`);
    }
    const c = b.crisis;
    const details = html("details", { class: "brief-crisis" }, card);
    html("summary", {}, details, c.applied ? `Crisis ${c.current} · Winter change applied` : `Crisis ${c.current} · ${c.projected} at Winter if nothing else changes`);
    html("p", { class: "help" }, details,
      `Year +${c.baseline}; closed chokepoints +${c.closed}; nuclear clash +${c.clash}; ${c.commons} successful Commons −${Math.floor(c.commons / c.per_point)}.`);
    if (!c.applied) {
      const drafted = [...this.orders.values()].filter((o) => o.kind === "commons").length;
      const more = c.per_point - c.commons % c.per_point;
      html("p", { class: "help" }, details, `${more} more successful Commons order${more === 1 ? "" : "s"} this year earn the next −1. ${drafted ? `You have drafted ${drafted}; these are not counted yet. ` : ""}Future orders and dislodgements can change this projection.`);
    }
    this.sandboxButton(card, p);
  }

  private sandboxButton(card: HTMLElement, p: Pending | null) {
    if (this.backend.snapshot && this.backend.kind !== "sandbox" && this.power) {
      const button = html("button", { class: "btn small", type: "button" }, card, "Try moves in sandbox");
      button.addEventListener("click", async () => {
        const opened = window.open("about:blank", "_blank");
        if (opened) opened.opener = null;
        button.disabled = true;
        try {
          const state = await this.backend.snapshot!();
          if (this.hosted && p && state.step !== p.id) throw new Error("The game advanced. Refresh the board before copying this position.");
          const url = storeSandbox(state, this.power!, p ? this.texts() : []);
          if (opened) opened.location.href = new URL(url, location.href).href;
          else html("a", { href: url, target: "_blank", class: "btn" }, card, "Open sandbox");
        } catch (e) {
          opened?.close();
          html("p", { class: "void", role: "alert" }, card, String(e));
        } finally { button.disabled = false; }
      });
    }
  }

  // ================================================================== board rendering
  /** The live phase with the drafted orders drawn as arrows. */
  displayPhase(): Phase {
    const i = this.host.liveIndex()!;
    const base = this.host.phase(i);
    const p = this.pending;
    if (!p) return base; // hosted: nothing for you to decide, just the live position
    const orders: Order[] = [];
    const add = (o: Partial<Order> & { text: string; kind: string }) => {
      const n = normaliseOrder({ power: p.power, unit: null, to: null, target: null, result: "ok", ...o } as Order);
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
    } else if (isAdjust(p)) {
      for (const b of this.builds) add({ text: b.text, kind: "build", unit: { type: b.type, at: b.at } });
      for (const d of p.disband ?? []) if (this.disbands.has(keyOf(d.unit))) add({ text: d.text, kind: "disband", unit: d.unit });
    }
    return { ...base, orders: [...(this.backend.draftOrders?.() ?? []).filter((o) => o.power !== p.power), ...orders] };
  }

  /** Highlights + the selection bar for the live phase. */
  paint() {
    // the tutorial's coach follows the drafts and the selection
    document.dispatchEvent(new CustomEvent("brink:draft", {
      detail: { texts: this.texts(), sel: this.sel ? { ...this.sel } : null, phase: this.pending?.phase.name ?? null },
    }));
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
    const mark = (pid: string, cls = "") => {
      const bp = view.board.provinces[provOf(pid)];
      if (!bp) return;
      svg("path", { d: bp.d, class: `reach red ${cls}` }, L);
    };
    let active = false;
    if (p.kind === "orders" && this.sel) {
      active = true;
      const ix = this.index.get(this.sel.key)!;
      const here = provOf(ix.choice.unit.at);
      const s = this.sel;
      if (this.backend.kind !== "tutorial") {
        mark(here, "origin");
        if (s.target) mark(s.target.split(" ")[1], "pick");
      } else if (s.mode === "move") {
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
        if (this.backend.kind === "tutorial") for (const d of c.to) mark(d);
        this.retreatBar(bar, c);
      }
    } else if (isAdjust(p) && (p.change ?? 0) > 0) {
      const sites = new Set((p.builds ?? []).map((b) => provOf(b.at)));
      for (const s of sites) mark(s, s === this.buildSite ? "origin" : "");
      active = !!this.buildSite;
      if (this.buildSite) this.buildBar(bar, this.buildSite);
    } else if (isAdjust(p) && (p.change ?? 0) < 0) {
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

  private barHead(bar: HTMLElement, name: string, hint: string, type?: UnitType) {
    bar.hidden = false;
    if (type && this.power) bar.append(counterSvg(type, this.power, 24));
    html("strong", {}, bar, name);
    html("span", { class: "hint" }, bar, hint);
  }

  private btn(parent: HTMLElement, label: string, on: () => void, opts: { key?: string; active?: boolean; title?: string } = {}) {
    const b = html("button", { class: `bar-btn${opts.active ? " active" : ""}`, type: "button", title: opts.title ?? "" }, parent);
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
      move: "click a province to move, its own to hold",
      "support-unit": "support: click the unit to support",
      "support-dest": "support: click where it goes (its own province: support hold)",
      "convoy-army": "convoy: click the Army to carry",
      "convoy-dest": "convoy: click the Army's destination",
      disrupt: "disrupt: click the target province",
    };
    this.barHead(bar, ix.choice.name, hints[s.mode], ix.choice.unit.type);
    const acts = html("span", { class: "bar-actions" }, bar);
    if (s.mode !== "move") {
      this.btn(acts, "Back", () => this.setMode("move"), { key: "Esc" });
    } else {
      if (ix.hold) this.btn(acts, "Hold", () => this.setOrder(ix.hold!), { key: "H" });
      this.btn(acts, "Support", () => this.setMode("support-unit"), { key: "S" });
      if (ix.choice.unit.type === "F") this.btn(acts, "Convoy", () => this.setMode("convoy-army"), { key: "C" });
      if ("CI".includes(ix.choice.unit.type)) this.btn(acts, "Commons", () => this.setOrder(draftOrder(`${s.key} COMMONS`)), { title: "Work on the global commons (lowers the Crisis)" });
      if (ix.choice.unit.type === "I") this.btn(acts, "Disrupt", () => this.setMode("disrupt"), { key: "D" });
      if (ix.choice.unit.type === "C") {
        const sel = html("select", { class: "bar-select", "aria-label": "Sanction a power" }, acts);
        html("option", { value: "" }, sel, "Sanction…");
        for (const p of POWER_ORDER.filter((p) => p !== this.power)) html("option", { value: p }, sel, `Sanction ${this.host.map.powers[p]?.name ?? p}`);
        sel.addEventListener("click", (ev) => ev.stopPropagation());
        sel.addEventListener("change", () => {
          if (sel.value) this.setOrder(draftOrder(`${s.key} SANCTION ${sel.value}`));
        });
      }
      if (ix.choice.unit.type === "F") {
        const cur = this.orders.get(s.key);
        const curAgainst = cur?.kind === "close" ? cur.text.split(/\s+/).slice(3) : null;
        const sel = html("select", { class: "bar-select", "aria-label": "Close the chokepoint" }, acts);
        html("option", { value: "" }, sel, curAgainst ? (curAgainst.length ? `Closed to ${curAgainst.join(" ")}: add…` : "Closed to all") : "Close…");
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
      if (this.orders.has(s.key)) this.btn(acts, "Clear", () => this.clearOrder(s.key), { key: "Del", title: "Remove this unit's order (it holds)" });
    }
    const x = html("button", { class: "x-btn", type: "button", "aria-label": "Cancel selection" }, bar);
    x.append(xIcon());
    x.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.cancel();
    });
  }

  private retreatBar(bar: HTMLElement, c: RetreatChoice) {
    this.barHead(bar, c.name, "dislodged: click where it retreats", c.unit.type);
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
      const label = `${UNIT_NAMES[b.type]}${b.at.includes("/") ? ` (${b.at.split("/")[1]})` : ""}${b.forward_base ? " (forward base)" : ""}`;
      const active = this.builds.some((x) => x.text === b.text);
      this.btn(acts, label, () => this.toggleBuild(b), { key: b.type, active });
    }
    const x = html("button", { class: "x-btn", type: "button", "aria-label": "Cancel" }, bar);
    x.append(xIcon());
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
        if (s.mode === "support-unit") return this.setMode("support-dest", key);
        if (s.mode === "convoy-army" && u.type === "A") return this.setMode("convoy-dest", key);
        if (s.key === key && s.mode === "move") return ix.hold ? this.setOrder(ix.hold) : undefined;
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
    if (isAdjust(p)) {
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
          return this.setOrder(draftOrder(`${s.key} - ${pid}`));
        }
        case "support-unit": {
          const cands = this.unitsAt(pid).map((u) => `${u.type} ${u.at}`);
          if (cands.length) return this.setMode("support-dest", cands[0]);
          return;
        }
        case "support-dest": {
          const t = s.target!;
          return this.setOrder(draftOrder(`${s.key} S ${t}${pid === provOf(t.split(" ")[1]) ? " H" : ` - ${pid}`}`));
        }
        case "convoy-army": {
          const cands = this.unitsAt(pid).filter((u) => u.type === "A").map((u) => `${u.type} ${u.at}`);
          if (cands.length) return this.setMode("convoy-dest", cands[0]);
          return;
        }
        case "convoy-dest": {
          return this.setOrder(draftOrder(`${s.key} C ${s.target} - ${pid}`));
        }
        case "disrupt": {
          return this.setOrder(draftOrder(`${s.key} DISRUPT ${pid}`));
        }
      }
      return;
    }
    if (p.kind === "retreats") {
      const c = (p.units as RetreatChoice[]).find((x) => keyOf(x.unit) === this.retreatSel);
      if (!c) return;
      const dest = c.to.find((d) => provOf(d) === pid) ?? pid;
      if (dest) {
        this.retreats.set(keyOf(c.unit), `${c.unit.type} ${c.unit.at} R ${dest}`);
        return this.nextRetreat();
      }
      return;
    }
    if (isAdjust(p)) {
      if ((p.change ?? 0) > 0) {
        if ((p.builds ?? []).some((b) => provOf(b.at) === pid)) {
          this.buildSite = this.buildSite === pid ? null : pid;
          return this.host.redraw();
        }
        this.buildSite = null;
        return this.host.redraw();
      }
      const mine = this.unitsAt(pid).filter((u) => u.power === p.power);
      if ((p.change ?? 0) < 0 && mine.length === 1) this.toggleDisband(`${mine[0].type} ${mine[0].at}`);
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
      if (ev.key === "Enter" && this.host.liveIndex() !== null) {
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
      if (k === "s") return this.setMode("support-unit"), true;
      if (k === "c" && ix.choice.unit.type === "F") return this.setMode("convoy-army"), true;
      if (k === "d" && ix.choice.unit.type === "I") return this.setMode("disrupt"), true;
      if (k === "h" && ix.hold) return this.setOrder(ix.hold), true;
      if (ev.key === "Backspace" || ev.key === "Delete") return this.clearOrder(this.sel.key), true;
    }
    if (p?.kind === "retreats" && this.retreatSel && k === "x") {
      const c = (p.units as RetreatChoice[]).find((x) => keyOf(x.unit) === this.retreatSel)!;
      this.retreats.set(this.retreatSel, `${c.unit.type} ${c.unit.at} D`);
      this.nextRetreat();
      return true;
    }
    if (p && isAdjust(p) && this.buildSite && "afci".includes(k) && k.length === 1) {
      const b = (p.builds ?? []).find((x) => provOf(x.at) === this.buildSite && x.type === k.toUpperCase());
      if (b) this.toggleBuild(b);
      return true;
    }
    if (p && isVote(p) && ["y", "n", "v"].includes(k)) {
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
    if (this.backend.controls) {
      const controls = html("div");
      this.backend.controls(controls);
      root.prepend(controls);
    }
    const live = this.host.liveIndex();
    const st = this.state;
    if (!st) return;
    const b = html("section", { class: "play-banner" });
    const head = root.querySelector(".phase-head");
    if (head) head.after(b);
    else root.prepend(b);
    const ph = this.host.phase(i);
    if (this.reviewFrom !== null && i >= this.reviewFrom) {
      const n = (live ?? this.host.total()) - this.reviewFrom;
      const k = i - this.reviewFrom + 1;
      html("p", { class: "banner-h" }, b, `Results ${k} of ${n} since your last ${this.hosted ? "visit" : "move"}`);
      html("p", {}, b, ph.kind === "movement" ? "These orders were resolved together. Arrows show each move; a crossed box marks one that failed."
        : ph.kind === "retreat" ? "Dislodged units retreated or were disbanded." : "Winter: alignment, the Crisis track, the Assembly and builds.");
    } else {
      html("p", { class: "banner-h" }, b, "Reviewing an earlier phase");
    }
    const row = html("div", { class: "btn-row" }, b);
    if (live !== null) {
      if (i + 1 < live) {
        const nx = html("button", { class: "btn", type: "button" }, row, "Next phase");
        nx.addEventListener("click", () => this.host.go(i + 1));
      }
      const label = this.pending ? (i + 1 < live ? "Skip to your move" : "Continue to your move") : (i + 1 < live ? "Skip to now" : "Continue to now");
      const go = html("button", { class: "btn commit-btn small", type: "button" }, row, label);
      html("kbd", {}, go, "Enter");
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
    const live = this.host.liveIndex();
    if (live !== null) this.backend.markSeen?.(live);
    const p = this.pending;
    if (!p) return this.waitingPanel(root);
    this.backend.controls?.(root);
    const map = this.host.map;
    const ph = p.phase;
    const head = html("section", { class: "phase-head" }, root);
    const h = html("h2", { class: "phase-title" }, head);
    h.append(document.createTextNode(`${seasonLabel(ph.season, ph.kind)} `));
    html("span", { class: "num" }, h, String(ph.year));
    const winterParts = [isVote(p) ? "vote" : "", isAdjust(p) && p.change ? ((p.change ?? 0) > 0 ? "build" : "disband") : "",
      isUmbrellas(p) ? "declare your umbrellas" : ""].filter(Boolean);
    const kindLabel: Record<string, string> = {
      orders: "Order your units", retreats: "Retreat or disband your dislodged units",
      propose: "You chair the Assembly: choose a proposal", vote: "Vote on the Assembly proposal",
      adjust: (p.change ?? 0) > 0 ? "Build new units" : "Disband units",
      umbrellas: "Declare your nuclear umbrellas for next year",
      winter: winterParts.length ? `Winter: ${listWords(winterParts)}` : "Winter",
    };
    const sub = html("p", { class: "phase-sub" }, head);
    sub.append(codeCounter(p.power, 18));
    const st = html("span", {}, sub);
    html("em", { class: "move-tag" }, st, this.ready ? "Ready" : "Your move");
    st.append(document.createTextNode(` ${map.powers[p.power]?.name}. ${kindLabel[p.kind]}.`));

    this.briefing(root, p);
    switch (p.kind) {
      case "orders": this.ordersCard(root, p); break;
      case "retreats": this.retreatsCard(root, p); break;
      case "adjust": this.adjustCard(root, p); break;
      case "propose": this.proposeCard(root, p); break;
      case "vote": this.voteCard(root, p); break;
      case "umbrellas": this.umbrellaCard(root, p); break;
      case "winter":
        if (isVote(p)) this.voteCard(root, p);
        if (isAdjust(p)) this.adjustCard(root, p);
        if (isUmbrellas(p)) this.umbrellaCard(root, p);
        break;
    }

    this.backend.sections?.(root, true);
    this.statusCard(root, p);
    // the usual state sections (Crisis, centres, security)
    this.host.stateCards(root, ph);

    const foot = html("div", { class: "aid-foot" }, root);
    const label = this.commitLabel();
    if (this.hosted) return this.hostedFoot(foot, label);
    const c = html("button", { class: "btn commit-btn commit", type: "button" }, foot, label);
    html("kbd", {}, c, "Enter");
    c.disabled = this.busy;
    c.addEventListener("click", () => this.commit());
    html("p", { class: "foot-note" }, foot, this.backend.kind === "sandbox"
      ? "Resolve all powers’ drafts now. You can undo or reset to the copied position."
      : p.kind === "orders"
      ? "Orders resolve together once every power has written theirs. Unordered units hold."
      : "Takes effect when every power has decided.");
    this.fab.textContent = label;
  }

  /** Hosted: orders save as you go; Ready resolves the phase early once every player is ready. */
  private hostedFoot(foot: HTMLElement, label: string) {
    foot.classList.add("hosted-foot");
    const line = html("p", { class: `ready-line${this.ready ? " is-ready" : ""}` }, foot);
    line.append(tickBox(this.ready));
    html("strong", {}, line, this.ready ? "Ready" : "Not ready");
    html("span", { class: "save-state", id: "save-state" }, line, this.saveNote());
    if (this.ready) {
      const c = html("button", { class: "btn big-ish commit", type: "button" }, foot, "Not ready: keep editing");
      c.disabled = this.busy;
      c.addEventListener("click", () => this.unready());
      this.fab.textContent = "Not ready";
    } else {
      const c = html("button", { class: "btn commit-btn commit", type: "button" }, foot, label);
      html("kbd", {}, c, "Enter");
      c.disabled = this.busy;
      c.addEventListener("click", () => this.commit());
      this.fab.textContent = label;
    }
    html("p", { class: "foot-note", id: "hosted-waiting-note" }, foot, this.hostedWaitingNote());
  }

  private hostedWaitingNote(): string {
    const dl = this.backend.deadline?.();
    const wait = this.backend.waitingFor?.() ?? "";
    return `The phase resolves when every player is ready, or at the deadline${dl ? ` (${deadlineWords(dl)})` : ""}. Saved orders count at the deadline; unordered units hold.${wait ? ` ${wait}` : ""}`;
  }

  private paintHostedWaiting() {
    const foot = document.getElementById("hosted-waiting-note");
    if (foot) foot.textContent = this.hostedWaitingNote();
    const waiting = document.getElementById("hosted-waiting-players");
    if (waiting) waiting.textContent = this.backend.waitingFor?.() || "Waiting for the other players.";
  }

  /** Hosted: nothing for you to decide right now (others are deciding, or you're out). */
  private waitingPanel(root: HTMLElement) {
    this.backend.controls?.(root);
    const i = this.host.liveIndex();
    if (i === null) return;
    const ph = this.host.phase(i);
    const head = html("section", { class: "phase-head" }, root);
    const h = html("h2", { class: "phase-title" }, head);
    h.append(document.createTextNode(`${seasonLabel(ph.season, ph.kind)} `));
    html("span", { class: "num" }, h, String(ph.year));
    const sub = html("p", { class: "phase-sub" }, head);
    if (this.power) sub.append(codeCounter(this.power, 18));
    html("span", { id: "hosted-waiting-players" }, sub, this.backend.waitingFor?.() || "Waiting for the other players.");
    const dl = this.backend.deadline?.();
    if (dl) html("p", { class: "phase-sub" }, head, `Deadline ${deadlineWords(dl)}.`);
    this.backend.sections?.(root, true);
    this.sandboxButton(root, null);
    this.host.stateCards(root, ph);
  }

  private commitLabel(): string {
    const p = this.pending!;
    if (this.backend.kind === "sandbox") return "Resolve sandbox";
    if (this.hosted) return "Ready";
    switch (p.kind) {
      case "orders": return "Commit orders";
      case "retreats": return "Commit retreats";
      case "adjust": return (p.change ?? 0) > 0 ? `Commit builds (${this.builds.length}/${p.change})` : "Commit disbands";
      case "propose": return this.proposal && this.proposal !== "PROPOSE NOTHING" ? "Propose" : "Propose nothing";
      case "vote": return this.vote ? `Vote ${this.vote}` : "Abstain";
      case "umbrellas": return "Declare umbrellas";
      case "winter": return "Commit";
    }
  }

  /** One row of the order list: counter, unit name, notation, Edit / Remove. */
  private draftRow(ul: HTMLElement, type: UnitType, name: string, text: string | null, empty: string, opts: {
    selected?: boolean; onEdit?: () => void; onRemove?: () => void; editLabel?: string;
  }) {
    const li = html("li", { class: `draft${opts.selected ? " selected" : ""}${text ? "" : " unset"}` }, ul);
    li.append(counterSvg(type, this.power!, 28));
    const body = html("div", { class: "draft-body" }, li);
    html("div", { class: "draft-name" }, body, name);
    if (text) html("div", { class: "notation" }, body, text);
    else html("div", { class: "draft-empty" }, body, empty);
    const acts = html("div", { class: "draft-acts" }, li);
    if (opts.onEdit) {
      const e = html("button", { class: "link-btn", type: "button", "aria-label": `${opts.editLabel ?? "Edit"} ${name}` }, acts, opts.editLabel ?? "Edit");
      e.addEventListener("click", (ev) => {
        ev.stopPropagation();
        e.blur();
        opts.onEdit!();
      });
    }
    if (opts.onRemove && text) {
      const x = html("button", { class: "link-btn", type: "button", "aria-label": `Remove the order for ${name}` }, acts, "Remove");
      x.addEventListener("click", (ev) => {
        ev.stopPropagation();
        opts.onRemove!();
      });
    }
    return li;
  }

  private ordersCard(root: HTMLElement, p: Pending) {
    const units = p.units as UnitChoice[];
    const n = units.filter((u) => this.orders.has(keyOf(u.unit))).length;
    const card = aidSection(root, RULE.orders, "Your orders", `${n} of ${units.length} written`, "decision");
    html("p", { class: "help" }, card, "Click a counter, then where it should go. With a unit selected: S support, C convoy, H hold, D disrupt, Esc to cancel. Orders are checked when the turn resolves.");
    const ul = html("ul", { class: "draft-list" }, card);
    for (const c of units) {
      const k = keyOf(c.unit);
      const o = this.orders.get(k);
      const li = this.draftRow(ul, c.unit.type, c.name.replace(/^\S+ /, ""), o?.text ?? null, "Holds (no order yet)", {
        selected: this.sel?.key === k,
        onEdit: () => this.select(this.sel?.key === k ? null : k),
        editLabel: o ? "Edit" : "Order",
        onRemove: () => this.clearOrder(k),
      });
      li.addEventListener("click", () => this.select(this.sel?.key === k ? null : k));
    }
    if (p.withdraw?.length) {
      const w = html("div", { class: "umbrella-actions" }, card);
      html("p", { class: "sub-h" }, w, `Your umbrellas (§${RULE.security})`);
      for (const b of p.withdraw) {
        const lab = html("label", { class: "check" }, w);
        const cb = html("input", { type: "checkbox" }, lab) as HTMLInputElement;
        cb.checked = this.withdraw.has(b);
        html("span", {}, lab, `Withdraw the umbrella over ${this.host.map.powers[b]?.name ?? b} (takes effect this turn)`);
        cb.addEventListener("change", () => {
          if (cb.checked) this.withdraw.add(b);
          else this.withdraw.delete(b);
          this.changed();
        });
      }
    }
  }

  private retreatsCard(root: HTMLElement, p: Pending) {
    const card = aidSection(root, RULE.resolution, "Dislodged units", undefined, "decision");
    html("p", { class: "help" }, card, "Click a dislodged counter, then a province to retreat there, or Disband (X). Units without an order are disbanded.");
    const ul = html("ul", { class: "draft-list" }, card);
    for (const c of p.units as RetreatChoice[]) {
      const k = keyOf(c.unit);
      const t = this.retreats.get(k) ?? null;
      const empty = "Disbands (no order yet)";
      const li = this.draftRow(ul, c.unit.type, c.name.replace(/^\S+ /, ""), t, empty, {
        selected: this.retreatSel === k,
        onEdit: () => { this.retreatSel = k; this.host.redraw(); },
        onRemove: () => { this.retreats.delete(k); this.retreatSel = k; this.changed(); },
      });
      li.addEventListener("click", () => {
        this.retreatSel = k;
        this.host.redraw();
      });
    }
  }

  private adjustCard(root: HTMLElement, p: Pending) {
    const change = p.change ?? 0;
    const alt = p.change_if_passed;
    if (change === 0) {
      const card = aidSection(root, RULE.winter, "Adjustments", undefined, "decision");
      html("p", { class: "help" }, card, `No builds or disbands this Winter${alt !== undefined && alt !== null
        ? `, unless the Assembly passes the Recognition: then you ${alt > 0 ? `could build ${alt}, but builds can't be written ahead` : `disband ${-alt}, chosen for you`}` : ""}.`);
      return;
    }
    if (change > 0) {
      const card = aidSection(root, RULE.winter, `Build up to ${change}`, `${this.builds.length} chosen`, "decision");
      this.altNote(card, p);
      html("p", { class: "help" }, card, "Click a highlighted province, then pick the unit type (A, F, C, I keys). Armies and Fleets build in your original homes; civil units in any owned home or aligned swing state; one forward-base Army a year where you have a civil unit.");
      const ul = html("ul", { class: "draft-list" }, card);
      for (const b of this.builds) {
        this.draftRow(ul, b.type, `${UNIT_NAMES[b.type]} in ${this.host.map.provinces[provOf(b.at)]?.name ?? b.at}${b.forward_base ? " (forward base)" : ""}`, b.text, "", {
          onRemove: () => this.toggleBuild(b),
        });
      }
      if (!this.builds.length) html("p", { class: "muted small" }, card, "No builds chosen yet. Unused builds are lost.");
    } else {
      const card = aidSection(root, RULE.winter, `Disband ${-change}`, `${this.disbands.size} chosen`, "decision");
      this.altNote(card, p);
      html("p", { class: "help" }, card, "You have more units than centres. Click counters to mark them for disbanding; any shortfall is chosen for you (furthest from home first).");
      const ul = html("ul", { class: "draft-list" }, card);
      for (const d of p.disband ?? []) {
        const k = keyOf(d.unit);
        const on = this.disbands.has(k);
        const li = this.draftRow(ul, d.unit.type, d.name.replace(/^\S+ /, ""), on ? d.text : null, "Keep", {
          selected: on,
          onEdit: () => this.toggleDisband(k),
          editLabel: on ? "Keep" : "Disband",
        });
        li.addEventListener("click", () => this.toggleDisband(k));
      }
    }
  }

  /** Hosted Winter: adjustments are written before the vote; a passed Recognition can change them. */
  private altNote(card: HTMLElement, p: Pending) {
    const alt = p.change_if_passed;
    if (alt === undefined || alt === null) return;
    html("p", { class: "help" }, card, `If the Assembly passes the Recognition, this becomes ${alt > 0 ? `${alt} build${alt === 1 ? "" : "s"}` : alt < 0 ? `${-alt} disband${alt === -1 ? "" : "s"}` : "no adjustment"}: extra builds are void and missing disbands are chosen for you.`);
  }

  private proposeCard(root: HTMLElement, p: Pending) {
    const card = aidSection(root, RULE.assembly, "Assembly proposal", "You chair", "decision");
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

  private voteCard(root: HTMLElement, p: Pending) {
    const card = aidSection(root, RULE.assembly, "Assembly vote", `Your weight ${p.weight}`, "decision");
    const chair = html("p", { class: "chair" }, card);
    if (p.chair) chair.append(codeCounter(p.chair, 18));
    html("span", {}, chair, `${this.host.map.powers[p.chair ?? ""]?.name ?? p.chair} proposes`);
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

  private umbrellaCard(root: HTMLElement, p: Pending) {
    const card = aidSection(root, RULE.security, "Nuclear umbrellas", undefined, "decision");
    html("p", { class: "help" }, card, "Military units of other powers can't enter the homes of a power under your umbrella. Declare who you protect next year.");
    if (!p.candidates?.length) html("p", { class: "muted small" }, card, "No power can be protected.");
    for (const c of p.candidates ?? []) {
      const lab = html("label", { class: "check" }, card);
      const cb = html("input", { type: "checkbox" }, lab) as HTMLInputElement;
      cb.checked = this.umbrellas.has(c);
      lab.append(codeCounter(c, 18));
      html("span", {}, lab, `${this.host.map.powers[c]?.name ?? c}${p.current?.includes(c) ? " (current)" : ""}`);
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
    const card = aidSection(root, RULE.winning, "Your position", map.powers[p.power]?.nuclear ? "Nuclear power" : "Not nuclear", "you-card");
    const tbl = html("dl", { class: "tally" }, card);
    for (const [k, v] of [["Centres", s.centres], ["Units", s.units], ["Swing states", s.swing], ["Vote weight", s.vote_weight]] as const) {
      const d = html("div", {}, tbl);
      html("dt", {}, d, k);
      html("dd", { class: "num" }, d, String(v));
    }
    const kv = (k: string, v: string) => {
      const r = html("div", { class: "aid-row" }, card);
      html("span", { class: "k" }, r, k);
      html("span", { class: "v" }, r, v);
    };
    const names = (xs: string[]) => xs.map((x) => map.powers[x]?.name ?? x).join(", ");
    if (!s.nuclear) kv("Umbrella", s.protected_by.length ? `Protected by ${names(s.protected_by)}` : "Unprotected: foreign Military can take your homes");
    else kv("Umbrellas", s.protecting.length ? `You protect ${names(s.protecting)}` : "You protect no one");
    kv("Veto", s.vetoes_left ? "1 left" : "Used");
    for (const e of s.effects) html("p", { class: "effect" }, card, e);
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
    const map = this.host.map;
    if (!st.power) {
      html("span", { class: "you-n" }, c, "Watching");
      const mg = html("button", { class: "link-btn", type: "button" }, c, "My games");
      mg.addEventListener("click", () => this.backend.exit?.());
      return;
    }
    const power = st.power;
    const last = st.pending?.phase ?? st.record.phases[st.record.phases.length - 1];
    const centres = last ? Object.entries(last.ownership).filter(([pid, o]) => o === power && map.provinces[pid]?.centre !== "none").length : 0;
    const who = html("span", { class: "you-power" }, c);
    who.append(codeCounter(power, 22));
    html("strong", {}, who, map.powers[power]?.name ?? power);
    html("span", { class: "you-n" }, c, `${centres} centres`);
    const prot = st.pending?.status.protected_by ?? [];
    if (prot.length) {
      const u = html("span", { class: "you-n umb", title: `Protected by ${prot.join(", ")}` }, c);
      u.append(umbrellaSvg(), document.createTextNode(prot.join(" ")));
    }
    c.title = `You play ${map.powers[power]?.name}`;
    const nb = html("button", { class: "link-btn", type: "button" }, c, this.hosted ? "My games" : "New game");
    nb.addEventListener("click", () => (this.backend.kind !== "local" ? this.backend.exit?.() : this.showStart()));
  }

  showStart() {
    const map = this.host.map;
    const m = modal("Brink: choose your power", true);
    m.classList.add("start");
    m.querySelector(".modal-head")!.remove();
    const lid = html("div", { class: "lid" }, m);
    // grid areas: cover / opts / go on the left (desktop); on phones the power list comes before Start
    const left = html("div", { class: "lid-cover" }, lid);
    const logo = html("h1", { class: "lid-logo", "aria-label": "Brink" }, left);
    html("span", { class: "logo-box" }, logo, "B");
    logo.append(document.createTextNode("RINK"));
    html("p", { class: "lid-line" }, left, "A game of great-power competition in the 2030s for seven players. Secret simultaneous orders, no dice. Power runs on two layers, military and civil; you win by aligning swing states, not by conquest, and a shared Crisis track can end the game with everyone losing.");
    html("p", { class: "lid-line small" }, left, "You play one power; six bots play the rest. Reach Primacy (9 aligned swing states) or lead when the clock runs out.");
    const close = html("button", { class: "x-btn lid-x", type: "button", "aria-label": "Close" }, lid);
    close.append(xIcon());
    close.addEventListener("click", closeModal);

    const right = html("div", { class: "lid-powers" }, lid);
    const rh = html("h2", { class: "aid-h" }, right);
    html("span", { class: "aid-t" }, rh, "Choose your power");
    const list = html("ul", { class: "power-list", role: "radiogroup", "aria-label": "Power" }, right);

    const optsBox = html("div", { class: "lid-opts" }, lid);
    const oh = html("h3", { class: "aid-h" }, optsBox);
    html("span", { class: "aid-t" }, oh, "Game options");
    const seedL = html("label", { class: "opt" }, optsBox);
    html("span", {}, seedL, "Seed");
    const seed = html("input", { class: "field", type: "number", placeholder: "Random", inputmode: "numeric" }, seedL) as HTMLInputElement;
    const lenL = html("label", { class: "opt" }, optsBox);
    html("span", {}, lenL, "Length");
    const len = html("select", { class: "field" }, lenL) as HTMLSelectElement;
    for (const [v, t] of [["", "Full game, to 2040"], ["2035", "Medium, to 2035"], ["2032", "Short, to 2032"]]) html("option", { value: v }, len, t);
    const allyL = html("label", { class: "opt check" }, optsBox);
    const ally = html("input", { type: "checkbox" }, allyL) as HTMLInputElement;
    html("span", {}, allyL, "Bots form alliances with each other");
    const resume = html("div", { class: "resume", hidden: true }, optsBox);
    const row = html("div", { class: "lid-go" }, lid);
    const startBtn = html("button", { class: "btn commit-btn big", type: "button" }, row, "Start game");

    let chosen = this.power ?? "USA";
    const rows: HTMLElement[] = [];
    const pick = (p: string) => {
      chosen = p;
      rows.forEach((r) => {
        const on = r.dataset.power === p;
        r.classList.toggle("active", on);
        r.setAttribute("aria-checked", String(on));
        r.tabIndex = on ? 0 : -1;
      });
      startBtn.textContent = `Start game as ${map.powers[p]?.name ?? p}`;
    };
    const pname = (x: string) => map.powers[x]?.name ?? x;
    const list2 = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
    for (const p of POWER_ORDER) {
      const P = map.powers[p];
      if (!P) continue;
      const li = html("li", {}, list);
      const card = html("button", { class: "power-row", type: "button", role: "radio", "data-power": p }, li);
      const us = html("span", { class: "pr-units" }, card);
      const start = map.start.units.filter((x) => x.power === p);
      for (const u of start) {
        const c = counterSvg(u.type, p, 30);
        const t = svg("title", {}, c);
        t.textContent = `${UNIT_NAMES[u.type]} in ${map.provinces[u.at]?.name ?? u.at}`;
        us.append(c);
      }
      const body = html("span", { class: "pr-body" }, card);
      const h = html("span", { class: "pr-head" }, body);
      html("span", { class: "pr-name" }, h, P.name);
      html("span", { class: "pr-code" }, h, p);
      html("span", { class: "pr-tag" }, h, P.nuclear ? "Nuclear" : "Not nuclear");
      html("span", { class: "pr-char" }, body, CHARACTER[p] ?? "");
      // facts from the map's start position
      const facts: string[] = [`Homes: ${list2(P.homes.map((x) => map.provinces[x]?.name ?? x))}.`];
      facts.push(`Starts with ${list2(start.map((u) => `${UNIT_NAMES[u.type]} in ${map.provinces[u.at]?.name ?? u.at}`))}.`);
      const over = map.start.umbrellas.filter(([a]) => a === p).map(([, b]) => pname(b));
      const under = map.start.umbrellas.filter(([, b]) => b === p).map(([a]) => pname(a));
      if (over.length) facts.push(`Umbrella over ${list2(over)}.`);
      if (under.length) facts.push(`Under the ${list2(under)} umbrella.`);
      html("span", { class: "pr-homes" }, body, facts.join(" "));
      card.addEventListener("click", () => pick(p));
      card.addEventListener("dblclick", () => go());
      card.addEventListener("keydown", (ev) => {
        const i = POWER_ORDER.indexOf(p as typeof POWER_ORDER[number]);
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
          ev.preventDefault();
          ev.stopPropagation();
          const n = POWER_ORDER[(i + (ev.key === "ArrowDown" ? 1 : POWER_ORDER.length - 1)) % POWER_ORDER.length];
          pick(n);
          rows.find((r) => r.dataset.power === n)?.focus();
        }
      });
      rows.push(card);
    }
    const go = () => this.create(chosen, seed.value, ally.checked, len.value);
    startBtn.addEventListener("click", go);
    pick(chosen);
    // ongoing games on this server
    (this.backend as LocalBackend).list().then((games) => {
      const open = games.filter((g) => g.status === "waiting" || g.status === "thinking" || g.status === "over");
      if (!open.length) return;
      resume.hidden = false;
      const rh2 = html("h3", { class: "aid-h" }, resume);
      html("span", { class: "aid-t" }, rh2, "Resume a game on this server");
      const ul = html("ul", { class: "resume-list" }, resume);
      for (const g of open.slice(0, 5)) {
        const li = html("li", {}, ul);
        const b = html("button", { class: "resume-row", type: "button" }, li);
        b.append(codeCounter(g.power, 20));
        html("span", {}, b, map.powers[g.power]?.name ?? g.power);
        html("span", { class: "muted" }, b, g.status === "over" ? "Finished" : `${g.season.charAt(0).toUpperCase()}${g.season.slice(1)} ${g.year}`);
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
      html("p", { class: "void" }, optsBox, "Can't reach the game server. Start it with `brink serve` and open the address it prints.");
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
    const you = st.power ?? "";
    const scores = r.scores ?? {};
    const ranked = POWER_ORDER.filter((p) => map.powers[p]).sort((a, b) => (scores[b] ?? 0) - (scores[a] ?? 0));
    const rank = 1 + ranked.filter((p) => (scores[p] ?? 0) > (scores[you] ?? 0)).length;
    const tied = ranked.filter((p) => p !== you && (scores[p] ?? 0) === (scores[you] ?? 0)).length;
    const headline = r.kind === "primacy" ? (r.winner === you ? "Primacy: you win" : `Primacy for ${map.powers[r.winner ?? ""]?.name ?? r.winner}`)
      : r.kind === "collapse" ? "Collapse: the Crisis reached 10 and everyone loses" : `Time: the game ends after Winter ${r.year ?? ""}`;
    html("p", { class: "lede strong" }, m, headline);
    html("p", { class: "muted" }, m, r.kind === "time" && you ? `Scores are shares of the squared centre counts. You finish ${tied ? "joint " : ""}${ordinal(rank)} of ${ranked.length}.` : "");
    const tbl = html("table", { class: "scores" }, m);
    const hr = html("tr", {}, html("thead", {}, tbl));
    for (const t of ["", "Power", "Centres", "Score"]) html("th", {}, hr, t);
    const tb = html("tbody", {}, tbl);
    for (const p of ranked) {
      const tr = html("tr", { class: p === you ? "you" : "" }, tb);
      const c = html("td", {}, tr);
      c.append(codeCounter(p, 20));
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
    const nw = html("button", { class: "btn commit-btn", type: "button" }, row, this.hosted ? "My games" : "New game");
    nw.addEventListener("click", () => {
      closeModal();
      if (this.backend.kind !== "local") this.backend.exit?.();
      else this.showStart();
    });
  }

  private showError(msg: string) {
    const m = modal("Something went wrong", true);
    html("pre", { class: "err" }, m, msg.slice(-1500));
    const row = html("div", { class: "btn-row end" }, m);
    const b = html("button", { class: "btn primary", type: "button" }, row, this.hosted ? "Back to my games" : "Start a new game");
    b.addEventListener("click", () => {
      closeModal();
      if (this.backend.kind !== "local") this.backend.exit?.();
      else this.showStart();
    });
  }
}

// ------------------------------------------------------------------ local backend (`brink serve`)
export class LocalBackend implements PlayBackend {
  readonly kind = "local";
  private id: string | null = null;

  async load(param: string | null): Promise<PlayState | null> {
    const id = param && !["", "1", "new", "true"].includes(param) ? param : null;
    if (!id) return null;
    const st = await api<PlayState>("GET", `/api/games/${id}`);
    this.id = st.id;
    return st;
  }

  async create(power: string, seed: string, alliances: boolean, lastYear: string): Promise<PlayState> {
    const st = await api<PlayState>("POST", "/api/games", {
      power, seed: seed === "" ? null : Number(seed), alliances, last_year: lastYear === "" ? null : Number(lastYear),
    });
    this.id = st.id;
    try {
      localStorage.setItem(`brink.play.${st.id}.last`, lastYear || "2040");
    } catch {
      /* ignore */
    }
    return st;
  }

  list() {
    return api<{ id: string; power: string; status: string; year: number; season: string }[]>("GET", "/api/games");
  }

  refresh(): Promise<PlayState> {
    return api<PlayState>("GET", `/api/games/${this.id}`);
  }

  async validate(texts: string[]): Promise<Check[]> {
    return (await api<{ results: Check[] }>("POST", `/api/games/${this.id}/validate`, { orders: texts })).results;
  }

  async snapshot(): Promise<Record<string, unknown>> {
    return (await api<{ state: Record<string, unknown> }>("GET", `/api/games/${this.id}/sandbox`)).state;
  }

  async submit(p: Pending, texts: string[]): Promise<PlayState> {
    const r = await api<{ state: PlayState; accepted: boolean }>("POST", `/api/games/${this.id}/orders`, {
      orders: texts, decision: p.id, submit: true,
    });
    return r.state;
  }

  /** The game's final year (chosen at the start; the server doesn't echo it). */
  lastYear(): number {
    try {
      const v = Number(localStorage.getItem(`brink.play.${this.id}.last`));
      if (v > 2000) return v;
    } catch {
      /* ignore */
    }
    return 2040;
  }

  pollDelay(st: PlayState): number | null {
    return st.status === "thinking" ? 700 : null;
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

export function listWords(xs: string[]): string {
  return xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** "in 13h 20m" / "in 4m" / "now". */
export function deadlineWords(d: Date): string {
  const ms = d.getTime() - Date.now();
  if (ms <= 0) return "now";
  const m = Math.ceil(ms / 60000);
  const h = Math.floor(m / 60);
  return h >= 48 ? `in ${Math.floor(h / 24)}d ${h % 24}h` : h ? `in ${h}h ${m % 60}m` : `in ${m}m`;
}

/** A printed tick box: empty, or ticked in ink. */
export function tickBox(on: boolean, px = 16): SVGSVGElement {
  const s = svg("svg", { viewBox: "0 0 16 16", width: px, height: px, class: `tickbox${on ? " on" : ""}`, "aria-hidden": "true" });
  svg("rect", { x: 1, y: 1, width: 14, height: 14 }, s);
  if (on) svg("path", { d: "M4 8.3l2.7 2.7L12 5.2" }, s);
  return s;
}

/** Decode notation for display only; legality belongs to turn resolution. */
function draftOrder(text: string): Opt {
  const o = normaliseOrder({ text, kind: "", power: "", unit: null, to: null, target: null, result: "ok" });
  return { text, kind: o.kind, unit: o.unit, to: o.to ?? null, target: o.target ?? null };
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

function xIcon(): SVGSVGElement {
  const s = svg("svg", { viewBox: "0 0 16 16", width: 14, height: 14, "aria-hidden": "true" });
  svg("path", { d: "M4 4l8 8M12 4l-8 8" }, s);
  return s;
}

function umbrellaSvg(): SVGSVGElement {
  const s = svg("svg", { viewBox: "0 0 16 16", width: 14, height: 14, class: "icon umbrella", "aria-hidden": "true" });
  svg("path", { d: "M8 2.2C4.6 2.2 2.2 4.6 1.8 7.6h12.4C13.8 4.6 11.4 2.2 8 2.2Zm0 5.4v5a1.4 1.4 0 0 1-2.8 0", fill: "none", stroke: "currentColor", "stroke-width": 1.3 }, s);
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
    const x = html("button", { class: "x-btn", type: "button", "aria-label": "Close" }, h);
    x.append(xIcon());
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

