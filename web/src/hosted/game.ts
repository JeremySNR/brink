// A hosted game on the board: the PlayController's backend for the hosted API, plus the player-aid
// sections only multiplayer has (players with ready ticks, press) and the deadline on the turn record.
import { codeCounter } from "../counter";
import { deadlineWords, listWords, tickBox } from "../play";
import type { Check, Pending, PlayBackend, PlayRecord, PlayState } from "../play";
import type { MapData, Phase } from "../types";
import { html } from "../util";
import { api } from "./api";
import type { GameView, Message, Seat } from "./api";

const FAST_POLL_MS = 5000;
const POLL_MS = 15000;
const FAST_WINDOW_MS = 120000;

export class HostedBackend implements PlayBackend {
  readonly kind = "hosted";
  view: GameView;
  private phases: Phase[] = [];
  private seatsEl: HTMLElement;
  private press: PressPanel | null = null;
  private clock = 0;

  constructor(private id: string, first: GameView, private map: MapData) {
    this.view = first;
    this.phases = [...first.phases];
    this.seatsEl = html("section", { class: "aid players" });
    const me = first.you.power;
    if (me && first.game.press === "full" && first.game.status === "active") {
      this.press = new PressPanel(id, me, map, () => this.view.seats);
      this.press.start();
    }
    this.clock = window.setInterval(() => this.paintDeadline(), 30000);
  }

  // ---------------------------------------------------------------- PlayBackend
  async load(): Promise<PlayState> {
    this.paintDeadline();
    this.paintSeats();
    return this.toState();
  }

  async refresh(): Promise<PlayState> {
    return this.take(await api<GameView>("GET", `games/${this.id}?since=${this.phases.length}`));
  }

  async validate(texts: string[]): Promise<Check[]> {
    return (await api<{ results: Check[] }>("POST", `games/${this.id}/validate`, { orders: texts })).results;
  }

  async snapshot(): Promise<Record<string, unknown>> {
    return (await api<{ state: Record<string, unknown> }>("GET", `games/${this.id}/sandbox`)).state;
  }

  async submit(p: Pending, texts: string[], ready: boolean): Promise<PlayState> {
    return this.take(await api<GameView>("POST", `games/${this.id}/submit`, {
      step: p.id, orders: texts, ready, since: this.phases.length,
    }));
  }

  lastYear(): number {
    return this.view.game.last_year;
  }

  pollDelay(st: PlayState, sinceCommit: number): number | null {
    if (st.status === "over" || st.status === "stopped") return null;
    return sinceCommit < FAST_WINDOW_MS ? FAST_POLL_MS : POLL_MS;
  }

  sections(root: HTMLElement) {
    const active = document.activeElement;
    const pressHadFocus = !!this.press && active instanceof HTMLElement && this.press.el.contains(active);
    root.append(this.seatsEl);
    if (this.press) {
      root.append(this.press.el);
      if (pressHadFocus) (active as HTMLElement).focus(); // moving a node drops focus: put it back
    }
  }

  waitingFor(): string {
    const v = this.view;
    if (v.game.status !== "active" || !v.pending) return "";
    const waiting = v.seats.filter((s) => s.human && s.involved && !s.ready && !s.you);
    if (!waiting.length) return v.seats.some((s) => s.you && s.involved && !s.ready) ? "Everyone else is ready." : "";
    const names = waiting.map((s) => `${this.map.powers[s.power]?.name ?? s.power} (${s.name})`);
    return `Waiting for ${listWords(names)}.`;
  }

  deadline(): Date | null {
    const d = this.view.game.deadline;
    return d && this.view.game.status === "active" ? new Date(d) : null;
  }

  unseenFrom(): number | null {
    try {
      const v = localStorage.getItem(`brink.seen.${this.id}`);
      return v === null ? null : Number(v);
    } catch {
      return null;
    }
  }

  markSeen(n: number) {
    try {
      localStorage.setItem(`brink.seen.${this.id}`, String(n));
    } catch {
      /* ignore */
    }
  }

  exit() {
    window.clearInterval(this.clock);
    this.press?.stop();
    location.hash = "#/";
  }

  // ---------------------------------------------------------------- state
  private take(v: GameView): PlayState {
    if (v.phases.length && v.phase_count === this.phases.length + v.phases.length) this.phases.push(...v.phases);
    else if (v.phase_count !== this.phases.length) {
      // out of step (shouldn't happen): the response carries what it carries; refetch everything next time
      if (v.phase_count === v.phases.length) this.phases = [...v.phases];
    }
    this.view = v;
    this.paintSeats();
    this.paintDeadline();
    return this.toState();
  }

  private toState(): PlayState {
    const v = this.view;
    const me = v.you.power;
    const record: PlayRecord = {
      map: "brink-v0", phases: this.phases, result: v.result,
      players: Object.fromEntries(v.seats.map((s) => [s.power, s.human ? s.name ?? "player" : "bot"])),
    };
    return {
      id: this.id, power: me, record, error: null,
      status: v.game.status === "finished" ? "over" : v.game.status === "active" ? "waiting" : "stopped",
      pending: me && v.decision ? toPending(v.decision, me) : null,
      position: v.pending?.position ?? null,
      saved: v.submission?.orders ?? null,
      ready: !!v.submission?.ready,
    };
  }

  // ---------------------------------------------------------------- printed parts
  private paintSeats() {
    const v = this.view;
    const el = this.seatsEl;
    el.replaceChildren();
    const humans = v.seats.filter((s) => s.human).length;
    const h = html("h3", { class: "aid-h" }, el);
    html("span", { class: "aid-t" }, h, "Players");
    html("span", { class: "aid-note" }, h, `${humans} of 7 · ${7 - humans} bots`);
    const ul = html("ul", { class: "seat-list" }, el);
    for (const s of v.seats) seatRow(ul, s, this.map, v.game.status === "active");
  }

  private paintDeadline() {
    const el = document.getElementById("trt-deadline");
    if (!el) return;
    const d = this.deadline();
    el.hidden = !d;
    if (!d) return;
    el.replaceChildren();
    html("span", { class: "dl-k" }, el, "Deadline");
    html("span", { class: "dl-v num" }, el, deadlineWords(d).replace(/^in /, ""));
    el.title = `Orders are due ${d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}`;
    el.classList.toggle("soon", d.getTime() - Date.now() < 3600_000);
  }
}

function seatRow(ul: HTMLElement, s: Seat, map: MapData, active: boolean) {
  const li = html("li", { class: `seat${s.you ? " you" : ""}${s.bot ? " bot" : ""}` }, ul);
  li.append(codeCounter(s.power, 20));
  html("span", { class: "seat-power" }, li, map.powers[s.power]?.name ?? s.power);
  html("span", { class: "seat-who" }, li, s.human ? `${s.name}${s.you ? " (you)" : ""}` : s.bot ? "Bot" : "Open");
  const st = html("span", { class: "seat-state" }, li);
  if (!active || !s.human) return;
  if (!s.involved) {
    html("span", { class: "muted" }, st, "No decision");
  } else {
    st.append(tickBox(!!s.ready, 14));
    html("span", {}, st, s.ready ? "Ready" : s.saved ? "Saved" : "Writing");
  }
  if (s.missed) html("span", { class: "missed", title: "Deadlines passed without orders" }, li, `missed ${s.missed}`);
}

/** The hosted API's decision for one power, in the shape the PlayController draws. */
export function toPending(d: GameView["decision"] & object, power: string): Pending | null {
  const o = d.options[power] as Record<string, unknown> | undefined;
  if (!o) return null;
  const base = { id: d.step, power, phase: d.position, status: o.status as Pending["status"] };
  if (d.kind === "orders") return { ...base, kind: "orders", units: o.units as Pending["units"], withdraw: o.withdraw as string[] };
  if (d.kind === "retreats") return { ...base, kind: "retreats", units: o.units as Pending["units"] };
  if (d.kind === "propose") return { ...base, kind: "propose", resolutions: o.resolutions as string[], chair: o.chair as string };
  const vote = o.vote as { proposal: string; chair: string; can_veto: boolean; weight: number } | null;
  const adj = o.adjust as { change: number; builds: Pending["builds"]; disband: Pending["disband"]; change_if_passed?: number } | null;
  const umb = o.umbrellas as { candidates: string[]; current: string[] } | null;
  return {
    ...base, kind: "winter", parts: { vote: !!vote, adjust: !!adj, umbrellas: !!umb },
    proposal: vote?.proposal, chair: vote?.chair ?? d.chair, can_veto: vote?.can_veto, weight: vote?.weight,
    change: adj?.change, change_if_passed: adj?.change_if_passed, builds: adj?.builds ?? [], disband: adj?.disband ?? [],
    candidates: umb?.candidates, current: umb?.current ?? [],
  };
}

// ------------------------------------------------------------------ press
/** Messages between powers, in threads by recipient set. Polls every 15 seconds. */
class PressPanel {
  el: HTMLElement;
  private messages: Message[] = [];
  private timer = 0;
  private openKey: string | null = null;
  private to = new Set<string>();
  private draft = "";
  private sending = false;
  private error = "";

  constructor(private gameId: string, private me: string, private map: MapData, private seats: () => Seat[]) {
    this.el = html("section", { class: "aid press" });
    this.render();
  }

  start() {
    this.poll();
  }

  stop() {
    window.clearTimeout(this.timer);
  }

  private async poll() {
    window.clearTimeout(this.timer);
    try {
      const after = this.messages.length ? this.messages[this.messages.length - 1].id : 0;
      const r = await api<{ messages: Message[] }>("GET", `games/${this.gameId}/messages?after=${after}`);
      const have = new Set(this.messages.map((m) => m.id));
      const fresh = r.messages.filter((m) => !have.has(m.id));
      if (fresh.length) {
        this.messages.push(...fresh);
        this.render();
      }
    } catch {
      /* try again next time */
    }
    this.timer = window.setTimeout(() => this.poll(), POLL_MS);
  }

  private others(): Seat[] {
    return this.seats().filter((s) => s.human && s.power !== this.me);
  }

  private key(recipients: string[]) {
    return [...recipients].sort().join(",");
  }

  private label(recipients: string[]) {
    const rest = recipients.filter((p) => p !== this.me);
    if (rest.length && rest.length === this.others().length && rest.length > 1) return "Everyone";
    return listWords(rest.map((p) => this.map.powers[p]?.name ?? p));
  }

  private seen(): Record<string, number> {
    try {
      return JSON.parse(localStorage.getItem(`brink.press.${this.gameId}.${this.me}`) || "{}");
    } catch {
      return {};
    }
  }

  private markSeen(key: string, id: number) {
    const s = this.seen();
    if ((s[key] ?? 0) >= id) return;
    s[key] = id;
    try {
      localStorage.setItem(`brink.press.${this.gameId}.${this.me}`, JSON.stringify(s));
    } catch {
      /* ignore */
    }
  }

  private render() {
    const focused = document.activeElement;
    const hadFocus = focused instanceof HTMLTextAreaElement && this.el.contains(focused);
    const caret = hadFocus ? (focused as HTMLTextAreaElement).selectionStart : 0;
    const el = this.el;
    el.replaceChildren();
    const threads = new Map<string, Message[]>();
    for (const m of this.messages) {
      const k = this.key(m.recipients);
      if (!threads.has(k)) threads.set(k, []);
      threads.get(k)!.push(m);
    }
    if (this.openKey !== null) {
      const ms = threads.get(this.openKey);
      if (ms?.length) this.markSeen(this.openKey, ms[ms.length - 1].id); // reading the thread now
    }
    const seen = this.seen();
    const unread = [...threads].reduce((n, [k, ms]) => n + ms.filter((m) => m.id > (seen[k] ?? 0) && m.sender_power !== this.me).length, 0);
    const h = html("h3", { class: "aid-h" }, el);
    html("span", { class: "aid-t" }, h, "Press");
    html("span", { class: "aid-note" }, h, unread ? `${unread} new` : threads.size ? `${threads.size} thread${threads.size === 1 ? "" : "s"}` : "Private messages");
    if (!this.others().length) {
      html("p", { class: "help" }, el, "Every other power is a bot: there is no one to negotiate with.");
      return;
    }

    if (this.openKey !== null) {
      const ms = threads.get(this.openKey) ?? [];
      const recips = this.openKey.split(",");
      const bar = html("div", { class: "thread-head" }, el);
      const back = html("button", { class: "link-btn", type: "button" }, bar, "All threads");
      back.addEventListener("click", () => {
        this.openKey = null;
        this.render();
      });
      const who = html("span", { class: "thread-who" }, bar);
      for (const p of recips.filter((x) => x !== this.me)) who.append(codeCounter(p, 16));
      html("strong", {}, who, this.label(recips));
      const list = html("ol", { class: "msg-list" }, el);
      for (const m of ms.slice(-60)) {
        const li = html("li", { class: `msg${m.sender_power === this.me ? " mine" : ""}` }, list);
        const meta = html("div", { class: "msg-meta" }, li);
        meta.append(codeCounter(m.sender_power, 14));
        html("span", {}, meta, m.sender_power === this.me ? "You" : this.map.powers[m.sender_power]?.name ?? m.sender_power);
        html("span", { class: "msg-when" }, meta, `${m.phase_name ?? ""} · ${new Date(m.created_at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}`);
        html("p", { class: "msg-body" }, li, m.body);
      }
      if (!ms.length) html("p", { class: "muted small" }, list, "No messages yet.");
      this.to = new Set(recips.filter((p) => p !== this.me));
      this.compose(el, false);
    } else {
      if (threads.size) {
        const ul = html("ul", { class: "thread-list" }, el);
        const sorted = [...threads].sort((a, b) => b[1][b[1].length - 1].id - a[1][a[1].length - 1].id);
        for (const [k, ms] of sorted) {
          const last = ms[ms.length - 1];
          const n = ms.filter((m) => m.id > (seen[k] ?? 0) && m.sender_power !== this.me).length;
          const li = html("li", {}, ul);
          const b = html("button", { class: `thread-row${n ? " unread" : ""}`, type: "button" }, li);
          const cs = html("span", { class: "thread-ctrs" }, b);
          for (const p of k.split(",").filter((x) => x !== this.me)) cs.append(codeCounter(p, 16));
          const body = html("span", { class: "thread-body" }, b);
          html("span", { class: "thread-name" }, body, this.label(k.split(",")));
          html("span", { class: "thread-last" }, body, `${last.sender_power === this.me ? "You: " : ""}${last.body}`);
          if (n) html("span", { class: "thread-n num" }, b, String(n));
          b.addEventListener("click", () => {
            this.openKey = k;
            this.render();
          });
        }
      } else {
        html("p", { class: "help" }, el, "Messages are private to the powers you send them to. Nothing said here binds anyone.");
      }
      this.compose(el, true);
    }
    if (hadFocus) {
      const ta = this.el.querySelector("textarea");
      if (ta) {
        ta.focus();
        ta.setSelectionRange(caret, caret);
      }
    }
  }

  private compose(el: HTMLElement, pick: boolean) {
    const form = html("form", { class: "compose" }, el);
    if (pick) {
      const row = html("div", { class: "to-row", role: "group", "aria-label": "Send to" }, form);
      html("span", { class: "to-k" }, row, "To");
      for (const s of this.others()) {
        const on = this.to.has(s.power);
        const b = html("button", { class: `to-btn${on ? " on" : ""}`, type: "button", "aria-pressed": String(on), title: `${this.map.powers[s.power]?.name} (${s.name})` }, row);
        b.append(codeCounter(s.power, 16));
        html("span", {}, b, s.power);
        b.addEventListener("click", () => {
          if (this.to.has(s.power)) this.to.delete(s.power);
          else this.to.add(s.power);
          this.render();
        });
      }
    }
    const ta = html("textarea", { class: "field msg-field", rows: 3, maxlength: 2000, placeholder: "Write to them… (Ctrl+Enter sends)", "aria-label": "Message" }, form) as HTMLTextAreaElement;
    ta.value = this.draft;
    ta.addEventListener("input", () => (this.draft = ta.value));
    ta.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        form.requestSubmit();
      }
    });
    const foot = html("div", { class: "compose-foot" }, form);
    if (this.error) html("span", { class: "void" }, foot, this.error);
    else html("span", { class: "muted small" }, foot, this.to.size ? `To ${listWords([...this.to].map((p) => this.map.powers[p]?.name ?? p))}` : "Pick who it's for");
    const send = html("button", { class: "btn primary small", type: "submit" }, foot, "Send");
    send.disabled = this.sending || !this.to.size;
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const body = this.draft.trim();
      if (!body || !this.to.size || this.sending) return;
      this.sending = true;
      this.error = "";
      try {
        const m = await api<Message>("POST", `games/${this.gameId}/messages`, { recipients: [...this.to], body });
        this.messages.push(m);
        this.draft = "";
        this.openKey = this.key(m.recipients);
      } catch (e) {
        this.error = (e as Error).message;
      } finally {
        this.sending = false;
        this.render();
      }
    });
  }
}
