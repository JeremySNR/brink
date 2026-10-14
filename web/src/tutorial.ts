// Learn to play (`?learn`): lessons from public/tutorial.json, played on the real board with the real order
// entry, every resolution by the real engine (src/brink/tutorial.py, POST api/tutorial/*). Two parts:
//
//   TutorialBackend  the PlayController's backend: a lesson's position, its scripted rivals, one learner
//   Coach            the programmed-instruction card over the map: what to read, what to do, what happened
//
// The coach follows the game through two DOM events: `brink:phase` (main.ts: which phase the board shows)
// and `brink:draft` (play.ts: the drafted orders and the selection). Lessons are checked in Python
// (tests/test_tutorial.py plays every solution), so the text here can't promise what the engine won't do.
import type { BoardView } from "./board";
import { counterSvg } from "./counter";
import { crisisTrack } from "./panel";
import type { Check, Pending, PlayBackend, PlayController, PlayState } from "./play";
import { toPending } from "./hosted/game";
import type { GameView } from "./hosted/api";
import { UNIT_NAMES } from "./theme";
import type { Phase, UnitType } from "./types";
import { html, svg } from "./util";

// ------------------------------------------------------------------ lesson file
interface GuideLine { until?: string; text: string }
interface Step { say?: string[]; task: string; guide?: GuideLine[]; solution?: string[]; mark?: string[] }
interface Result { check: string; success: string; miss: string }
interface Card {
  title: string; body: string[]; focus?: string[]; mark?: string[]; fit?: boolean;
  units?: UnitType[]; crisis?: number; point?: string;
}
export interface Chapter {
  id: string; kind: "cards" | "play"; title: string; minutes: number;
  setup: Record<string, unknown>; script?: Record<string, Record<string, string[]>>;
  end?: string; focus?: string[];
  steps?: Record<string, Step>; results?: Record<string, Result>; done?: string;
  cards?: Card[];
}
export interface Lessons { power: string; chapters: Chapter[] }

type Decision = NonNullable<GameView["decision"]>;
interface SandboxView { state: unknown; decision: (Decision & { phase: string; powers: string[] }) | null; phases: Phase[]; result: unknown }

const PROGRESS_KEY = "brink.tutorial.done";

async function sandbox<T>(action: string, body: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`api/tutorial/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error((data as { error?: string } | null)?.error ?? `HTTP ${r.status}`);
  return data as T;
}

// ------------------------------------------------------------------ backend
export class TutorialBackend implements PlayBackend {
  readonly kind = "tutorial";
  chapter: Chapter;
  view: SandboxView | null = null;
  private run = 0;

  constructor(readonly lessons: Lessons, index: number, private onSections: (root: HTMLElement) => void) {
    this.chapter = lessons.chapters[index];
  }

  /** The current chapter's position, fresh. */
  async load(): Promise<PlayState> {
    this.run = Date.now();
    this.view = await sandbox<SandboxView>("start", { setup: this.chapter.setup });
    return this.toState();
  }

  async refresh(): Promise<PlayState> {
    return this.toState();
  }

  async validate(texts: string[]): Promise<Check[]> {
    if (!this.view) return [];
    return (await sandbox<{ results: Check[] }>("validate", { state: this.view.state, orders: texts })).results;
  }

  async submit(_p: Pending, texts: string[]): Promise<PlayState> {
    this.view = await sandbox<SandboxView>("step", { state: this.view!.state, orders: texts, script: this.chapter.script ?? {} });
    return this.toState();
  }

  lastYear(): number {
    const end = this.chapter.end ? Number(this.chapter.end.slice(1, 5)) : 0;
    const start = Number((this.chapter.setup.year as number | undefined) ?? 2031);
    return Math.max(start, end - (this.chapter.end?.startsWith("S") ? 1 : 0));
  }

  pollDelay(): number | null {
    return null;
  }

  /** The lesson list under the board's state, when there's no decision to make. */
  sections(root: HTMLElement) {
    if (!this.toState().pending) this.onSections(root);
  }

  waitingFor(): string {
    return this.finished() ? "Lesson complete." : "Read the instruction card on the map.";
  }

  exit() {
    location.href = "./";
  }

  /** The lesson is over once play reaches its last phase (cards chapters never take orders). */
  finished(): boolean {
    const d = this.view?.decision;
    return this.chapter.kind === "play" && (!d || d.phase === this.chapter.end);
  }

  private toState(): PlayState {
    const v = this.view!;
    const power = this.lessons.power;
    const live = this.chapter.kind === "play" && v.decision && !this.finished();
    return {
      id: `tutorial-${this.chapter.id}-${this.run}`, power, status: "waiting", error: null,
      record: { map: "brink-v0", phases: v.phases, result: null, players: {} },
      pending: live ? toPending(v.decision!, power) : null,
      position: v.decision?.position ?? v.phases[v.phases.length - 1] ?? null,
    };
  }
}

// ------------------------------------------------------------------ coach
interface Draft { texts: string[]; sel: { key: string; mode: string; target?: string } | null; phase: string | null }
interface Shown { index: number; live: number | null; phase: Phase }

export class Coach {
  private el: HTMLElement;
  private card = 0;
  private draft: Draft = { texts: [], sel: null, phase: null };
  private shown: Shown | null = null;
  private pointed: Element | null = null;
  private collapsed = false;
  private index: number;
  private framed = ""; // the view last zoomed to: re-centre when the phase changes, not on every click

  constructor(
    private lessons: Lessons,
    private backend: TutorialBackend,
    private play: PlayController,
    private view: BoardView,
    private signedIn: () => boolean,
  ) {
    this.index = lessons.chapters.indexOf(backend.chapter);
    this.el = html("section", { class: "coach", "aria-live": "polite", "aria-label": "Instruction card" }, document.getElementById("stage"));
    document.addEventListener("brink:phase", (e) => {
      this.shown = (e as CustomEvent<Shown>).detail;
      this.render();
    });
    document.addEventListener("brink:draft", (e) => {
      const d = (e as CustomEvent<Draft>).detail;
      const changed = d.texts.join("|") !== this.draft.texts.join("|") || JSON.stringify(d.sel) !== JSON.stringify(this.draft.sel);
      this.draft = d;
      if (changed) this.renderGuide();
    });
  }

  get chapter() {
    return this.backend.chapter;
  }

  /** Start (or restart) chapter i. */
  async open(i: number) {
    this.index = Math.max(0, Math.min(this.lessons.chapters.length - 1, i));
    this.backend.chapter = this.lessons.chapters[this.index];
    this.card = 0;
    this.shown = null;
    this.draft = { texts: [], sel: null, phase: null };
    try {
      const url = new URL(location.href);
      url.searchParams.set("learn", this.chapter.id);
      history.replaceState(null, "", url);
    } catch {
      /* ignore */
    }
    this.el.replaceChildren();
    html("p", { class: "coach-loading" }, this.el, "Setting up the board…");
    await this.play.start(null);
    this.framed = "";
    this.render();
  }

  // ---------------------------------------------------------------- rendering
  /** Zoom once the card is laid out, beside it (or fit the whole sheet). */
  private zoom(to: "fit" | string[] | undefined) {
    if (!to || (Array.isArray(to) && !to.length)) return;
    // after layout settles: the board refits itself on resize (phones lay out late) and would undo it
    requestAnimationFrame(() => window.setTimeout(() => {
      this.inset();
      if (to === "fit") this.view.fit();
      else this.view.zoomToProvinces(to);
    }, 150));
  }

  /** Tell the board what the card covers, so zooms land beside it. */
  private inset() {
    const stage = document.getElementById("stage")!.getBoundingClientRect();
    const r = this.el.getBoundingClientRect();
    const phone = window.innerWidth <= 860;
    this.view.inset = this.collapsed ? { left: 0, bottom: 0 }
      : phone ? { left: 0, bottom: Math.max(0, stage.bottom - r.top) } : { left: Math.max(0, r.right - stage.left), bottom: 0 };
  }

  private render() {
    const c = this.chapter;
    const el = this.el;
    el.replaceChildren();
    el.classList.toggle("collapsed", this.collapsed);
    const live = this.shown?.live ?? null;
    const idx = this.shown?.index ?? null;
    const done = c.kind === "play" ? this.backend.finished() && (live === null || idx === live)
      : this.card >= (c.cards?.length ?? 1) - 1;
    if (done) markDone(c.id);
    this.head(el);
    if (this.collapsed) return this.marks([]);
    const body = html("div", { class: "coach-body" }, el);
    if (c.kind === "cards") return this.renderCard(body);
    const frame = `${c.id}:${this.shown?.phase.name ?? ""}`;
    if (frame !== this.framed) {
      this.framed = frame;
      this.zoom(c.focus);
    }
    if (done) return this.renderDone(body);
    if (idx !== null && live !== null && idx < live) return this.renderResult(body, this.shown!.phase);
    const d = this.backend.view?.decision;
    const step = d ? c.steps?.[d.phase] : undefined;
    if (!step) {
      html("p", {}, body, "Step through what happened with Next phase, then carry on.");
      return this.marks([]);
    }
    this.renderStep(body, step);
  }

  private head(el: HTMLElement) {
    const n = this.lessons.chapters.length;
    const top = html("header", { class: "coach-top" }, el);
    html("span", { class: "coach-kicker" }, top, "Programmed instruction");
    const pips = html("ol", { class: "coach-pips", "aria-label": `Lesson ${this.index + 1} of ${n}` }, top);
    const done = progress();
    this.lessons.chapters.forEach((ch, i) => {
      const li = html("li", { class: `${i === this.index ? "on" : ""}${done.has(ch.id) ? " done" : ""}` }, pips);
      const b = html("button", { type: "button", title: `${i + 1}. ${ch.title}`, "aria-label": `Lesson ${i + 1}: ${ch.title}` }, li, String(i + 1));
      b.addEventListener("click", () => this.open(i));
    });
    const tog = html("button", { class: "coach-toggle", type: "button", "aria-expanded": String(!this.collapsed) }, top,
      this.collapsed ? "Show" : "Hide");
    tog.addEventListener("click", () => {
      this.collapsed = !this.collapsed;
      this.render();
    });
    const t = html("h2", { class: "coach-title" }, el);
    html("span", { class: "coach-case num" }, t, `${this.index + 1}.`);
    t.append(document.createTextNode(` ${this.chapter.title}`));
  }

  private renderCard(body: HTMLElement) {
    const cards = this.chapter.cards ?? [];
    const k = Math.min(this.card, cards.length - 1);
    const card = cards[k];
    html("h3", { class: "coach-h" }, body, card.title);
    for (const p of card.body) html("p", {}, body, p);
    if (card.units) {
      const row = html("ul", { class: "coach-units" }, body);
      for (const u of card.units) {
        const li = html("li", {}, row);
        li.append(counterSvg(u, this.lessons.power, 34));
        html("span", { class: "cu-name" }, li, UNIT_NAMES[u]);
        html("span", { class: "cu-layer" }, li, u === "A" || u === "F" ? "Military" : "Civil");
      }
    }
    if (card.crisis !== undefined) {
      const box = html("div", { class: "coach-crisis" }, body);
      crisisTrack(box, card.crisis);
    }
    this.marks(card.mark ?? [], card.point);
    this.zoom(card.fit ? "fit" : card.focus);

    const nav = html("div", { class: "coach-nav" }, body);
    html("span", { class: "coach-count num" }, nav, `${k + 1} / ${cards.length}`);
    if (k > 0) {
      const b = html("button", { class: "btn", type: "button" }, nav, "Back");
      b.addEventListener("click", () => { this.card = k - 1; this.render(); });
    }
    if (k < cards.length - 1) {
      const b = html("button", { class: "btn primary", type: "button" }, nav, "Next");
      b.addEventListener("click", () => { this.card = k + 1; this.render(); });
      b.focus({ preventScroll: true });
    } else this.nextButtons(body, nav);
  }

  private renderStep(body: HTMLElement, step: Step) {
    for (const p of step.say ?? []) html("p", {}, body, p);
    const task = html("div", { class: "coach-task" }, body);
    html("span", { class: "coach-task-k" }, task, "Your move");
    html("p", { class: "coach-task-t" }, task, step.task);
    html("p", { class: "coach-guide", id: "coach-guide" }, task);
    this.renderGuide();
    const row = html("div", { class: "coach-nav" }, body);
    if (step.solution?.length) {
      const show = html("button", { class: "link-btn", type: "button" }, row, "Show me");
      show.addEventListener("click", () => this.play.setDraft(step.solution!));
    }
    this.marks(step.mark ?? []);
  }

  /** The one line that says what to click next, from the drafts and the selection. */
  private renderGuide() {
    const el = document.getElementById("coach-guide");
    const d = this.backend.view?.decision;
    const step = d ? this.chapter.steps?.[d.phase] : undefined;
    if (!el || !step) return;
    const line = (step.guide ?? []).find((g) => !g.until || !this.met(g.until)) ?? null;
    el.replaceChildren();
    if (!line) return;
    const arrow = html("span", { class: "coach-arrow", "aria-hidden": "true" }, el);
    arrow.textContent = "▸";
    el.append(document.createTextNode(` ${line.text}`));
  }

  private met(cond: string): boolean {
    const [k, ...rest] = cond.split(":");
    const v = rest.join(":");
    const texts = this.draft.texts.map((t) => t.toUpperCase());
    if (k === "sel") return this.draft.sel?.key === v || texts.some((t) => t.startsWith(`${v} `));
    if (k === "order") return texts.includes(v.toUpperCase());
    if (k === "prefix") return texts.some((t) => t.startsWith(v.toUpperCase()));
    if (k === "mode") {
      const order = ["move", "support-unit", "support-dest"];
      const want = order.indexOf(v);
      const now = this.draft.sel ? order.indexOf(this.draft.sel.mode) : -1;
      return now >= want || texts.some((t) => / S /.test(t));
    }
    return false;
  }

  private renderResult(body: HTMLElement, ph: Phase) {
    const r = this.chapter.results?.[ph.name];
    html("p", { class: "coach-kick" }, body, "What happened");
    if (!r) {
      html("p", {}, body, ph.kind === "winter"
        ? "Winter: swing states aligned, the Crisis track moved, and everyone built. The panel lists it all."
        : "These orders resolved together. Arrows show each move; a crossed box marks one that failed.");
    } else {
      const ok = holds(r.check, ph, this.position());
      const stamp = html("p", { class: `coach-stamp ${ok ? "pass" : "miss"}` }, body, ok ? "Well done" : "Not this time");
      stamp.setAttribute("aria-label", ok ? "Success" : "Missed");
      html("p", {}, body, ok ? r.success : r.miss);
      if (!ok) {
        const row = html("div", { class: "coach-nav" }, body);
        const again = html("button", { class: "btn primary", type: "button" }, row, "Replay this lesson");
        again.addEventListener("click", () => this.open(this.index));
      }
    }
    html("p", { class: "coach-hint" }, body, "Use the button in the panel to continue.");
    this.marks([]);
  }

  private renderDone(body: HTMLElement) {
    html("p", { class: "coach-stamp pass" }, body, "Lesson complete");
    if (this.chapter.done) html("p", {}, body, this.chapter.done);
    const nav = html("div", { class: "coach-nav" }, body);
    const again = html("button", { class: "link-btn", type: "button" }, nav, "Replay");
    again.addEventListener("click", () => this.open(this.index));
    this.nextButtons(body, nav);
    this.marks([]);
  }

  private nextButtons(body: HTMLElement, nav: HTMLElement) {
    const next = this.lessons.chapters[this.index + 1];
    if (next) {
      const b = html("button", { class: "btn commit-btn coach-go", type: "button" }, nav, `Next: ${next.title}`);
      b.addEventListener("click", () => this.open(this.index + 1));
      b.focus({ preventScroll: true });
      return;
    }
    // the end of the course: into a real game
    const end = html("div", { class: "coach-end" }, body);
    html("p", { class: "coach-h" }, end, "You know enough to play.");
    html("p", {}, end, "The rest you'll pick up at the table. A practice game puts you against six bots with no deadline pressure: each phase resolves as soon as you commit.");
    const acts = html("div", { class: "coach-end-acts" }, end);
    const go = html("a", { class: "btn commit-btn coach-go", href: "./#/practice", role: "button" }, acts,
      this.signedIn() ? "Start a practice game" : "Sign in and play a practice game");
    go.focus({ preventScroll: true });
    html("a", { class: "btn", href: "./?game=games/sim-1000.json" }, acts, "Watch a whole game");
    html("a", { class: "link-btn", href: "./" }, acts, "Back to Brink");
  }

  // ---------------------------------------------------------------- board marks
  private marks(pids: string[], point?: string) {
    const L = this.view.layer.coach;
    L.replaceChildren();
    for (const pid of pids) {
      const bp = this.view.board.provinces[pid];
      if (bp) svg("path", { d: bp.d, class: "coach-mark" }, L);
    }
    this.pointed?.classList.remove("coach-point");
    this.pointed = point ? document.querySelector(point) : null;
    this.pointed?.classList.add("coach-point");
  }

  private position(): Phase {
    const v = this.backend.view!;
    return (v.decision?.position as Phase | undefined) ?? v.phases[v.phases.length - 1];
  }

  /** The aid column's lesson list (cards chapters, and under a finished lesson). */
  syllabus(root: HTMLElement) {
    const sec = html("section", { class: "aid syllabus" }, root);
    const h = html("h3", { class: "aid-h" }, sec);
    html("span", { class: "aid-t" }, h, "Learn to play");
    const total = this.lessons.chapters.reduce((a, c) => a + c.minutes, 0);
    html("span", { class: "aid-note" }, h, `About ${total} minutes`);
    const ol = html("ol", { class: "syl-list" }, sec);
    const done = progress();
    this.lessons.chapters.forEach((c, i) => {
      const li = html("li", { class: `${i === this.index ? "on" : ""}${done.has(c.id) ? " done" : ""}` }, ol);
      const b = html("button", { type: "button" }, li);
      html("span", { class: "syl-n num" }, b, String(i + 1));
      html("span", { class: "syl-t" }, b, c.title);
      html("span", { class: "syl-m" }, b, done.has(c.id) ? "Done" : `${c.minutes} min`);
      b.addEventListener("click", () => this.open(i));
    });
    html("p", { class: "help" }, sec, "Every move here is resolved by the real rules. Replay any lesson as often as you like.");
  }
}

// ------------------------------------------------------------------ checks (tests/test_tutorial.py `holds`)
export function holds(cond: string, phase: Phase, position: Phase): boolean {
  const [kind, ...rest] = cond.split(":");
  const arg = rest.join(":");
  if (kind === "order") {
    const at = arg.lastIndexOf("=");
    const text = arg.slice(0, at);
    const res = arg.slice(at + 1);
    return phase.orders.some((o) => o.text === text && o.result === res);
  }
  if (kind === "owner") {
    const [pid, power] = arg.split("=");
    return (position.ownership[pid] ?? "") === power;
  }
  if (kind === "unit") {
    const [power, type, at] = arg.split(/\s+/);
    return position.units.some((u) => u.power === power && u.type === type && u.at === at);
  }
  return false;
}

function progress(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(PROGRESS_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function markDone(id: string) {
  try {
    const s = progress();
    s.add(id);
    localStorage.setItem(PROGRESS_KEY, JSON.stringify([...s]));
  } catch {
    /* ignore */
  }
}

/** The chapter to open for `?learn=<id>`, else the first one not done. */
export function startIndex(lessons: Lessons, param: string | null): number {
  const i = lessons.chapters.findIndex((c) => c.id === param);
  if (i >= 0) return i;
  const done = progress();
  const j = lessons.chapters.findIndex((c) => !done.has(c.id));
  return j >= 0 ? j : 0;
}
