// A browser-owned practice game, or a copy of a game for trying every power's orders.
import type { Check, Pending, PlayBackend, PlayState } from "./play";
import { toPending } from "./hosted/game";
import type { GameView } from "./hosted/api";
import type { MapData, Phase, Order } from "./types";
import { html, normaliseOrder } from "./util";

type Decision = NonNullable<GameView["decision"]> & { powers: string[] };
export interface WorkshopView {
  state: Record<string, unknown>; decision: Decision | null; phases: Phase[];
  result: PlayState["record"]["result"];
}
interface Saved {
  view: WorkshopView; power: string; drafts: Record<string, string[]>;
  origin?: WorkshopView; source?: string;
}
const key = (id: string) => `brink.workshop.${id}`;

async function request<T>(action: string, body: unknown): Promise<T> {
  const r = await fetch(`/api/workshop/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
  return data as T;
}

export function storeSandbox(state: Record<string, unknown>, power: string, orders: string[]): string {
  const id = crypto.randomUUID();
  const view = { state, decision: null, phases: [], result: null };
  localStorage.setItem(key(id), JSON.stringify({ view, origin: view, power, drafts: { [power]: orders }, source: location.href }));
  return `./?sandbox=${id}`;
}

export class WorkshopBackend implements PlayBackend {
  readonly kind: "practice" | "sandbox";
  private id = "";
  private saved!: Saved;
  private history: Saved[] = [];
  private warning = "";
  private busy = false;
  onChange?: (state: PlayState) => void;
  constructor(mode: "practice" | "sandbox", private map: MapData) { this.kind = mode; }

  async load(param: string | null): Promise<PlayState> {
    this.id = param ?? "USA";
    if (this.kind === "practice" && this.map.powers[this.id]) {
      const power = this.id;
      this.id = crypto.randomUUID();
      const view = await request<WorkshopView>("start", { mode: this.kind, power });
      this.saved = { view, power, drafts: {} };
    } else {
      const raw = localStorage.getItem(key(this.id));
      if (!raw) throw new Error("This position is saved in the browser where you opened it. Start a practice game or copy your game again.");
      this.saved = JSON.parse(raw) as Saved;
      this.saved.view = await request<WorkshopView>("view", this.body());
    }
    const url = new URL(location.href);
    url.searchParams.set(this.kind, this.id);
    history.replaceState(null, "", url);
    this.persist();
    return this.toState();
  }
  private body() { return { mode: this.kind, power: this.saved.power, state: this.saved.view.state }; }
  async refresh() { return this.toState(); }
  async snapshot() { return structuredClone(this.saved.view.state); }
  async validate(orders: string[]): Promise<Check[]> {
    return (await request<{ results: Check[] }>("validate", { ...this.body(), orders })).results;
  }
  draftChanged(p: Pending, texts: string[]) {
    this.saved.drafts[p.power] = [...texts];
    this.persist();
  }
  draftOrders(): Order[] {
    if (this.kind !== "sandbox") return [];
    return Object.entries(this.saved.drafts).flatMap(([power, orders]) => orders.map((text) => normaliseOrder({ power, text, kind: "", unit: null, to: null, target: null, result: "ok" })));
  }
  async submit(p: Pending, orders: string[]): Promise<PlayState> {
    this.saved.drafts[p.power] = [...orders];
    this.busy = true;
    try {
      const view = await request<WorkshopView>("step", { ...this.body(), orders, decisions: this.saved.drafts });
      this.history.push(structuredClone(this.saved));
      if (this.history.length > 20) this.history.shift();
      this.saved.view = view;
      this.saved.drafts = {};
      this.persist();
      return this.toState();
    } finally { this.busy = false; }
  }
  lastYear() { return Number((this.saved?.view.state.rules as { last_year?: number })?.last_year ?? 2034); }
  pollDelay() { return null; }
  exit() { location.href = "./#/practice"; }
  waitingFor() { return this.kind === "sandbox" ? "Sandbox: choose a power with a decision to continue." : "Practice game complete."; }

  private toState(): PlayState {
    const { view: v } = this.saved;
    if (this.kind === "sandbox" && v.decision && !v.decision.options[this.saved.power]) {
      this.saved.power = Object.keys(v.decision.options)[0] ?? this.saved.power;
    }
    const power = this.saved.power;
    const d = v.decision?.options[power] ? toPending(v.decision, power) : null;
    return { id: this.id, power, status: v.result ? "over" : "waiting", error: null,
      record: { map: "brink-v0", phases: v.phases, result: v.result }, pending: d,
      position: v.decision?.position ?? null, saved: this.saved.drafts[power] ?? [] };
  }
  private persist() {
    try {
      localStorage.setItem(key(this.id), JSON.stringify(this.saved));
      if (this.kind === "practice") localStorage.setItem("brink.practice.last", this.id);
      this.warning = "";
    } catch { this.warning = "Browser storage is full or unavailable. Keep this tab open; this position cannot be saved."; }
  }
  private change() { this.persist(); this.onChange?.(this.toState()); }

  controls(root: HTMLElement) {
    if (!this.saved) return;
    const card = html("section", { class: "aid workshop-card" }, root);
    html("h3", { class: "aid-h" }, card, this.kind === "sandbox" ? "Sandbox · What if?" : "Guest practice");
    html("p", { class: "help" }, card, this.kind === "sandbox"
      ? "Try orders for any power. Unordered units hold; missing retreats disband. These are your assumptions, not other players’ secret orders."
      : "You against six bots, through 2034. Saved in this browser. No account or deadline needed.");
    if (this.warning) html("p", { class: "void", role: "status" }, card, this.warning);
    if (this.kind === "practice") {
      html("a", { class: "link-btn", href: "./?learn" }, card, "Open the tutorial");
      return;
    }
    const label = html("label", { class: "opt" }, card);
    html("span", {}, label, "Orders for");
    const select = html("select", { class: "field", "aria-label": "Sandbox power" }, label);
    const d = this.saved.view.decision;
    for (const p of Object.keys(this.map.powers)) {
      const n = this.saved.drafts[p]?.length ?? 0;
      html("option", { value: p, disabled: !d?.options[p] }, select,
        `${this.map.powers[p].name} · ${n} order${n === 1 ? "" : "s"}${d?.options[p] ? "" : " · no decision"}`);
    }
    select.value = this.saved.power;
    select.disabled = this.busy || !d;
    select.addEventListener("change", () => { this.saved.power = select.value; this.change(); });
    const row = html("div", { class: "btn-row" }, card);
    const undo = html("button", { class: "btn small", type: "button", disabled: this.busy || !this.history.length }, row, "Undo resolution");
    undo.addEventListener("click", () => { this.saved = this.history.pop()!; this.change(); });
    const reset = html("button", { class: "btn small", type: "button", disabled: this.busy }, row, "Reset to copied position");
    reset.addEventListener("click", async () => {
      if (this.busy) return;
      this.busy = true; reset.disabled = true;
      try {
        const view = await request<WorkshopView>("view", { ...this.body(), state: this.saved.origin!.state });
        this.history.push(structuredClone(this.saved));
        this.saved.view = view; this.saved.drafts = {}; this.change();
      } catch (e) { html("p", { class: "void", role: "alert" }, card, String(e)); }
      finally { this.busy = false; this.change(); }
    });
    if (this.saved.source) html("a", { class: "link-btn", href: this.saved.source }, card, "Return to game");
  }
}
