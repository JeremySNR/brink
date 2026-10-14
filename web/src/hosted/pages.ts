// Hosted multiplayer pages, around the board: landing, sign-in, my games and the lobby, new game, a
// forming game's seats and invite, invite links, profile. Hash routes:
//
//   #/            landing (signed out) or My games + Open games (signed in)
//   #/signin      email magic link        #/me        display name, turn emails, sign out
//   #/new         new game                #/join/CODE  invite landing
//   #/practice    you against six bots, one click (the tutorial ends here)
//   #/g/<id>      forming: seats + invite; active or finished: the board (handed to main.ts)
//
// Learn to play is the board itself (`?learn`, src/tutorial.ts), linked from here.
//
// Built from the same printed parts as the board's start screen: the box lid, aid sections, die-cut counters.
import { codeCounter, counterSvg } from "../counter";
import { CHARACTER, deadlineWords, listWords } from "../play";
import { POWER_ORDER, UNIT_NAMES } from "../theme";
import type { MapData } from "../types";
import { html, svg } from "../util";
import { ApiError, api } from "./api";
import type { GameSummary, GameView, Me, MyGame, Notification } from "./api";
import { configured, devAvailable, devSignIn, initAuth, onAuthChange, sendMagicLink, session, signOut, takeUrlError } from "./auth";

const SAMPLE_REPLAY = "?game=games/sim-1000.json";
const LEARN = "?learn";
const FORMING_POLL_MS = 10000;
const HOME_POLL_MS = 30000;

type OpenBoard = (id: string, view: GameView) => void;

let map: MapData;
let root: HTMLElement;
let openBoard: OpenBoard;
let me: Me | null = null;
let timer = 0;
let boardOpen = false;

export async function startHosted(m: MapData, onBoard: OpenBoard) {
  map = m;
  openBoard = onBoard;
  root = document.getElementById("pages")!;
  await initAuth();
  onAuthChange(() => {
    me = null;
    route();
  });
  window.addEventListener("hashchange", () => {
    if (boardOpen) return location.reload(); // the board owns the page: leave it with a fresh load
    route();
  });
  route();
}

// ------------------------------------------------------------------ routing
async function route() {
  window.clearTimeout(timer);
  const h = location.hash.replace(/^#\/?/, "");
  const [head, arg] = h.split("/");
  try {
    if (session() && !me) me = await api<Me>("GET", "me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      await signOut();
      return;
    }
  }
  if (head === "signin") return signinPage();
  if (head === "join" && arg) return joinPage(arg.toUpperCase());
  if (head === "g" && arg) return gamePage(arg);
  if (!session()) {
    if (head === "me" || head === "new" || head === "practice") return needSignIn(`#/${head}`);
    return landingPage();
  }
  if (head === "me") return mePage();
  if (head === "new") return newGamePage();
  if (head === "practice") return practicePage();
  return homePage();
}

function go(hash: string) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function needSignIn(next: string) {
  try {
    localStorage.setItem("brink.next", next);
  } catch {
    /* ignore */
  }
  go("#/signin");
}

// ------------------------------------------------------------------ page frame
function page(title: string, cls = ""): HTMLElement {
  document.title = title ? `${title} · Brink` : "Brink";
  document.body.classList.add("pages-mode");
  root.hidden = false;
  root.replaceChildren();
  const top = html("header", { class: "topbar page-top" }, root);
  const brand = html("a", { class: "brand", href: "#/" }, top);
  const logo = html("span", { class: "logotype", "aria-label": "Brink" }, brand);
  html("span", { class: "logo-box" }, logo, "B");
  logo.append(document.createTextNode("RINK"));
  if (title) html("span", { class: "game-name" }, brand, title);
  const nav = html("nav", { class: "top-actions page-nav", "aria-label": "Account" }, top);
  html("a", { class: "nav-link", href: LEARN }, nav, "Learn to play");
  if (session()) {
    if (me?.unread) {
      const n = html("a", { class: "nav-link", href: "#/" }, nav, "Notices ");
      html("span", { class: "num nav-n" }, n, String(me.unread));
    }
    html("a", { class: "nav-link", href: "#/me" }, nav, me?.display_name ?? "Account");
  } else if (configured() && !location.hash.startsWith("#/signin")) {
    html("a", { class: "nav-link", href: "#/signin" }, nav, "Sign in");
  }
  const main = html("main", { class: `sheet-page ${cls}`.trim() }, root);
  root.scrollTop = 0;
  return main;
}

function lid(parent: HTMLElement, cls = ""): { cover: HTMLElement; right: HTMLElement; lid: HTMLElement } {
  const l = html("div", { class: `lid page-lid ${cls}`.trim() }, parent);
  const cover = html("div", { class: "lid-cover" }, l);
  const right = html("div", { class: "lid-powers lid-right" }, l);
  return { cover, right, lid: l };
}

function logo(parent: HTMLElement, small = false) {
  const h = html("h1", { class: `lid-logo${small ? " small" : ""}`, "aria-label": "Brink" }, parent);
  html("span", { class: "logo-box" }, h, "B");
  h.append(document.createTextNode("RINK"));
}

function heading(parent: HTMLElement, title: string, note?: string, tag: "h2" | "h3" = "h2") {
  const h = html(tag, { class: "aid-h page-h" }, parent);
  html("span", { class: "aid-t" }, h, title);
  if (note) html("span", { class: "aid-note" }, h, note);
  return h;
}

function errorLine(parent: HTMLElement, e: unknown) {
  return html("p", { class: "void page-err", role: "alert" }, parent, e instanceof Error ? e.message : String(e));
}

function phaseWords(name: string | null): string {
  if (!name || name.length < 6) return name ?? "";
  const season = { S: "Spring", F: "Fall", W: "Winter" }[name[0]] ?? name[0];
  return `${season} ${name.slice(1, 5)}${name.endsWith("R") ? " retreats" : ""}`;
}

function settingsWords(g: GameSummary): string {
  return [g.visibility === "private" ? "Private" : "Public", `${g.deadline_hours}h deadlines`,
    g.press === "full" ? "full press" : "no press", `to ${g.last_year}`].join(" · ");
}

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 129600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function inviteUrl(code: string) {
  return `${location.origin}${location.pathname}#/join/${code}`;
}

// ------------------------------------------------------------------ landing
function landingPage() {
  const main = page("");
  const { cover, right } = lid(main, "landing");
  logo(cover);
  html("p", { class: "lid-line" }, cover, "A game of great-power competition in the 2030s for seven players. Secret simultaneous orders, no dice. Power runs on two layers, military and civil; you win by aligning swing states, not by conquest, and a shared Crisis track can end the game with everyone losing.");
  html("p", { class: "lid-line small" }, cover, "Play it with friends, one power each. Bots take the empty seats. Orders are due by a deadline, so a game fits around your week.");
  const go1 = html("div", { class: "lid-actions" }, cover);
  if (configured()) {
    const b = html("a", { class: "btn commit-btn big", href: "#/signin" }, go1, "Sign in to play");
    b.setAttribute("role", "button");
  } else {
    html("p", { class: "help" }, go1, "Sign-in isn't configured on this server. You can still learn the game, watch one, or play against six bots locally with brink serve.");
  }
  learnButton(go1);
  html("a", { class: "btn wide", href: SAMPLE_REPLAY }, go1, "Watch a sample game");

  heading(right, "How a game runs");
  const steps = html("ol", { class: "how" }, right);
  const step = (t: string, d: string) => {
    const li = html("li", {}, steps);
    html("strong", {}, li, t);
    html("span", {}, li, d);
  };
  step("Gather a table", "Start a game and send the invite link. Each friend takes a power; bots play the seats nobody takes.");
  step("Write orders by the deadline", "Every 12 to 72 hours, chosen when you start. Mark yourself ready and the phase resolves as soon as everyone is.");
  step("Negotiate", "Send private press to any player. Agreements aren't binding; the orders are.");
  step("Read what happened", "Each phase replays on the board: every move, support and failure, then the Winter's alignment, Crisis and Assembly.");
  heading(right, "The powers", "Seven seats");
  const ul = html("ul", { class: "power-list compact" }, right);
  for (const p of POWER_ORDER) {
    const P = map.powers[p];
    if (!P) continue;
    const li = html("li", { class: "power-mini" }, ul);
    const us = html("span", { class: "pr-units" }, li);
    for (const u of map.start.units.filter((x) => x.power === p)) us.append(counterSvg(u.type, p, 24));
    const b = html("span", { class: "pr-body" }, li);
    const hd = html("span", { class: "pr-head" }, b);
    html("span", { class: "pr-name" }, hd, P.name);
    html("span", { class: "pr-tag" }, hd, P.nuclear ? "Nuclear" : "Not nuclear");
    html("span", { class: "pr-char" }, b, CHARACTER[p] ?? "");
  }
}

// ------------------------------------------------------------------ sign in
function signinPage() {
  if (session()) return go("#/");
  const main = page("Sign in");
  const { cover, right } = lid(main, "narrow");
  logo(cover, true);
  html("p", { class: "lid-line" }, cover, "Sign in with your email address. We send you a link; open it on this device and you're in. No password.");
  heading(right, "Sign in");
  const err = takeUrlError();
  if (err) html("p", { class: "void page-err", role: "alert" }, right, `That sign-in link didn't work: ${err}. Send yourself a new one.`);
  if (!configured()) {
    html("p", { class: "help" }, right, "Sign-in isn't configured on this server.");
    return;
  }
  const form = html("form", { class: "form-card" }, right);
  const lab = html("label", { class: "opt" }, form);
  html("span", {}, lab, "Email");
  const email = html("input", { class: "field", type: "email", required: true, autocomplete: "email", placeholder: "you@example.com" }, lab) as HTMLInputElement;
  const send = html("button", { class: "btn commit-btn", type: "submit" }, form, "Email me a sign-in link");
  const out = html("div", { class: "form-out", "aria-live": "polite" }, form);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    out.replaceChildren();
    send.disabled = true;
    let next = "#/";
    try {
      next = localStorage.getItem("brink.next") || "#/";
    } catch {
      /* ignore */
    }
    try {
      await sendMagicLink(email.value.trim(), next);
      form.replaceChildren();
      html("p", { class: "lede strong" }, form, "Check your email for a sign-in link.");
      html("p", { class: "help" }, form, `We sent it to ${email.value.trim()}. Open it in this browser. It can take a minute; look in spam if it doesn't arrive.`);
      const again = html("button", { class: "link-btn", type: "button" }, form, "Use a different address");
      again.addEventListener("click", () => signinPage());
    } catch (e) {
      errorLine(out, e);
      send.disabled = false;
    }
  });
  if (devAvailable()) {
    heading(right, "Dev sign-in", "local only", "h3");
    const f2 = html("form", { class: "form-card" }, right);
    const l2 = html("label", { class: "opt" }, f2);
    html("span", {}, l2, "Name");
    const name = html("input", { class: "field", required: true, placeholder: "ada" }, l2) as HTMLInputElement;
    html("button", { class: "btn primary", type: "submit" }, f2, "Sign in as this player");
    f2.addEventListener("submit", (ev) => {
      ev.preventDefault();
      devSignIn(name.value);
      let next = "#/";
      try {
        next = localStorage.getItem("brink.next") || "#/";
        localStorage.removeItem("brink.next");
      } catch {
        /* ignore */
      }
      go(next);
    });
  }
  email.focus();
}

// ------------------------------------------------------------------ profile
function mePage() {
  const main = page("Your account");
  const { cover, right } = lid(main, "narrow");
  logo(cover, true);
  html("p", { class: "lid-line" }, cover, "Your display name is what the other players see next to your power.");
  heading(right, "Your account");
  const form = html("form", { class: "form-card" }, right);
  const lab = html("label", { class: "opt" }, form);
  html("span", {}, lab, "Name");
  const name = html("input", { class: "field", required: true, minlength: 2, maxlength: 32, value: me?.display_name ?? "" }, lab) as HTMLInputElement;
  const row = html("div", { class: "btn-row" }, form);
  html("button", { class: "btn primary", type: "submit" }, row, "Save name");
  const out = html("div", { class: "form-out", "aria-live": "polite" }, form);
  if (me?.email) html("p", { class: "help" }, right, `Signed in as ${me.email}.`);
  // turn emails: "your orders are due" when a phase opens, and one reminder before the deadline
  const mail = html("div", { class: "form-card" }, right);
  const ml = html("label", { class: "opt check" }, mail);
  const mcb = html("input", { type: "checkbox" }, ml) as HTMLInputElement;
  mcb.checked = me?.email_notifications ?? true;
  html("span", {}, ml, "Email me when my orders are due");
  html("p", { class: "help" }, mail, "One email when a phase needs your decision, and one more a few hours before the deadline if you haven't marked Ready. Only for games with other people in them: a game against bots waits for you.");
  const mout = html("div", { class: "form-out", "aria-live": "polite" }, mail);
  mcb.addEventListener("change", async () => {
    mout.replaceChildren();
    try {
      me = await api<Me>("PUT", "me", { email_notifications: mcb.checked });
      html("p", { class: "help" }, mout, mcb.checked ? "Turn emails are on." : "Turn emails are off.");
    } catch (e) {
      mcb.checked = !mcb.checked;
      errorLine(mout, e);
    }
  });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    out.replaceChildren();
    try {
      me = await api<Me>("PUT", "me", { display_name: name.value });
      mePage(); // re-render, top bar included
      html("p", { class: "help" }, document.querySelector(".form-out") ?? document.body, "Saved.");
    } catch (e) {
      errorLine(out, e);
    }
  });
  const so = html("button", { class: "btn", type: "button" }, right, "Sign out");
  so.addEventListener("click", async () => {
    await signOut();
    go("#/");
  });
}

// ------------------------------------------------------------------ my games + lobby
async function homePage() {
  const main = page("");
  const { cover, right } = lid(main, "home");
  logo(cover, true);
  html("p", { class: "lid-line" }, cover, `Signed in as ${me?.display_name ?? ""}.`);
  const acts = html("div", { class: "lid-actions" }, cover);
  html("a", { class: "btn commit-btn big", href: "#/new", role: "button" }, acts, "New game");
  const code = html("form", { class: "code-form" }, acts);
  const cl = html("label", { class: "opt" }, code);
  html("span", {}, cl, "Invite");
  const ci = html("input", { class: "field", placeholder: "Code, e.g. K7Q2MX9P", maxlength: 8, autocomplete: "off", "aria-label": "Invite code" }, cl) as HTMLInputElement;
  html("button", { class: "btn", type: "submit" }, code, "Go");
  code.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (ci.value.trim()) go(`#/join/${ci.value.trim().toUpperCase()}`);
  });
  const notes = html("div", { class: "notes" }, cover);

  const mineEl = html("section", { class: "page-sec" }, right);
  const lobbyEl = html("section", { class: "page-sec" }, right);
  // say nothing for a fast answer; a slow one (a cold server) gets a quiet line
  const slow = window.setTimeout(() => html("p", { class: "muted" }, mineEl, "Checking for your games…"), 900);

  const load = async () => {
    try {
      const [mine, lobby, nots] = await Promise.all([
        api<MyGame[]>("GET", "games/mine"), api<GameSummary[]>("GET", "lobby"),
        api<{ unread: number; items: Notification[] }>("GET", "notifications"),
      ]);
      window.clearTimeout(slow);
      renderMine(mineEl, mine);
      renderLobby(lobbyEl, lobby.filter((g) => !mine.some((m) => m.id === g.id)));
      renderNotes(notes, nots);
    } catch (e) {
      window.clearTimeout(slow);
      mineEl.replaceChildren();
      errorLine(mineEl, e);
    }
    timer = window.setTimeout(load, HOME_POLL_MS);
  };
  load();
}

function renderMine(el: HTMLElement, games: MyGame[]) {
  el.replaceChildren();
  const turn = games.filter((g) => g.my_turn).length;
  heading(el, "Your games", turn ? `${turn} waiting for you` : games.length ? `${games.length}` : undefined);
  if (!games.length) return welcome(el);
  const ul = html("ul", { class: "game-list" }, el);
  for (const g of games) {
    const li = html("li", {}, ul);
    const a = html("a", { class: `game-row${g.my_turn ? " turn" : ""}`, href: `#/g/${g.id}` }, li);
    a.append(codeCounter(g.power, 28));
    const body = html("span", { class: "gr-body" }, a);
    html("span", { class: "gr-name" }, body, g.name);
    let sub = "";
    if (g.status === "forming") sub = `Forming · ${g.players ?? 1} of 7 players${g.creator ? " · you start it" : ""}`;
    else if (g.status === "finished") sub = resultWords(g);
    else sub = `${phaseWords(g.phase_name)} · ${map.powers[g.power]?.name ?? g.power}`;
    html("span", { class: "gr-sub" }, body, sub);
    const st = html("span", { class: "gr-state" }, a);
    if (g.status === "active") {
      if (g.my_turn) html("em", { class: "move-tag" }, st, "Your move");
      else html("span", { class: "gr-tag" }, st, g.involved ? "Ready" : "Waiting");
      if (g.deadline) html("span", { class: "gr-dl num" }, st, deadlineWords(new Date(g.deadline)).replace(/^in /, ""));
    } else if (g.status === "forming") html("span", { class: "gr-tag" }, st, "Forming");
    else html("span", { class: "gr-tag" }, st, "Finished");
  }
}

/** Signed in with no games yet: where to start. */
function welcome(el: HTMLElement) {
  el.replaceChildren();
  heading(el, "Welcome to Brink");
  const w = html("div", { class: "welcome" }, el);
  const steps = html("ol", { class: "welcome-steps" }, w);
  const step = (n: string, title: string, text: string, href: string, label: string, cls: string) => {
    const li = html("li", {}, steps);
    html("span", { class: "ws-n num" }, li, n);
    const b = html("div", { class: "ws-b" }, li);
    html("strong", {}, b, title);
    html("span", {}, b, text);
    html("a", { class: `btn ${cls}`, href, role: "button" }, li, label);
  };
  step("1", "Learn to play", "Ten minutes on the real board: your first move, winning a contest with support, and the rules that matter.", LEARN, "Start the tutorial", "primary");
  step("2", "Practise against bots", "You against six bots in a short game. Each phase resolves the moment you commit.", "#/practice", "Practice game", "");
  step("3", "Play with friends", "Start a game and send the invite link. Bots take any seat nobody claims.", "#/new", "New game", "");
}

function learnButton(parent: HTMLElement) {
  const a = html("a", { class: "btn primary wide learn-btn", href: LEARN, role: "button" }, parent);
  html("span", {}, a, "Learn to play");
  html("span", { class: "learn-min" }, a, "10 min");
}

function resultWords(g: MyGame): string {
  const r = g.result;
  if (!r) return "Finished";
  if (r.kind === "primacy") return r.winner === g.power ? "Primacy: you won" : `Primacy for ${map.powers[r.winner ?? ""]?.name ?? r.winner}`;
  if (r.kind === "collapse") return "Collapse: everyone lost";
  const scores = r.scores ?? {};
  const rank = 1 + Object.values(scores).filter((v) => v > (scores[g.power] ?? 0)).length;
  return `Time: you finished ${rank}${rank === 1 ? "st" : rank === 2 ? "nd" : rank === 3 ? "rd" : "th"}`;
}

function renderLobby(el: HTMLElement, games: GameSummary[]) {
  el.replaceChildren();
  heading(el, "Open games", games.length ? `${games.length} forming` : undefined);
  if (!games.length) {
    html("p", { class: "help" }, el, "No public games are forming right now.");
    return;
  }
  const ul = html("ul", { class: "game-list" }, el);
  for (const g of games) {
    const li = html("li", {}, ul);
    const a = html("a", { class: "game-row", href: `#/g/${g.id}` }, li);
    const seats = html("span", { class: "gr-seats", "aria-label": `${g.players} of 7 seats taken` }, a);
    for (const p of POWER_ORDER) {
      const open = g.open_seats?.includes(p);
      const c = codeCounter(p, 16);
      if (open) c.classList.add("seat-open");
      seats.append(c);
    }
    const body = html("span", { class: "gr-body" }, a);
    html("span", { class: "gr-name" }, body, g.name);
    html("span", { class: "gr-sub" }, body, `${g.created_by_name ?? ""} · ${settingsWords(g)}`);
    const st = html("span", { class: "gr-state" }, a);
    html("span", { class: "gr-tag" }, st, `${g.players} of 7`);
    html("span", { class: "btn small gr-join" }, st, "Join");
  }
}

function renderNotes(el: HTMLElement, n: { unread: number; items: Notification[] }) {
  el.replaceChildren();
  if (!n.items.length) return;
  const h = heading(el, "Notices", n.unread ? `${n.unread} new` : undefined, "h3");
  if (n.unread) {
    const all = html("button", { class: "link-btn", type: "button" }, h, "Mark read");
    all.addEventListener("click", async () => {
      renderNotes(el, await api("POST", "notifications/read", {}));
      if (me) me.unread = 0;
    });
  }
  const ul = html("ul", { class: "note-list" }, el);
  const kinds: Record<string, string> = { turn: "Your move", joined: "Joined", started: "Started", message: "Press", finished: "Finished", cancelled: "Cancelled" };
  for (const it of n.items.slice(0, 12)) {
    const li = html("li", { class: it.read_at ? "" : "unread" }, ul);
    const a = html("a", { href: it.game_id ? `#/g/${it.game_id}` : "#/" }, li);
    html("span", { class: "note-k" }, a, kinds[it.kind] ?? it.kind);
    html("span", { class: "note-b" }, a, it.body);
    html("span", { class: "note-t" }, a, ago(it.created_at));
    a.addEventListener("click", () => {
      if (!it.read_at) api("POST", "notifications/read", { ids: [it.id] }).catch(() => undefined);
    });
  }
}

// ------------------------------------------------------------------ new game
function newGamePage() {
  const main = page("New game");
  const { cover, right } = lid(main, "new");
  html("h1", { class: "page-title" }, cover, "New game");
  html("p", { class: "lid-line small" }, cover, "You take a seat now; friends take theirs from the invite link. When you start, bots play every empty seat.");
  const form = html("form", { class: "lid-form", id: "new-game" }, cover);
  const field = (label: string, el: HTMLElement) => {
    const l = html("label", { class: "opt" }, form);
    html("span", {}, l, label);
    l.append(el);
    return el;
  };
  const name = field("Name", html("input", { class: "field", required: true, maxlength: 60, value: `${me?.display_name ?? "Our"}'s game` })) as HTMLInputElement;
  const sel = (label: string, opts: [string, string][], value: string) => {
    const s = html("select", { class: "field" }) as HTMLSelectElement;
    for (const [v, t] of opts) html("option", { value: v }, s, t);
    s.value = value;
    return field(label, s) as HTMLSelectElement;
  };
  const vis = sel("Lobby", [["public", "Public: listed in Open games"], ["private", "Private: invite link only"]], "public");
  const dl = sel("Deadline", [["12", "12 hours a phase"], ["24", "24 hours a phase"], ["48", "48 hours a phase"], ["72", "72 hours a phase"]], "24");
  const press = sel("Press", [["full", "Full press: private messages"], ["none", "No press: orders only"]], "full");
  const len = sel("Length", [["2040", "Full game, to 2040"], ["2034", "Short game, to 2034"]], "2040");
  const out = html("div", { class: "form-out", "aria-live": "polite" }, cover);
  const goRow = html("div", { class: "lid-actions" }, cover);
  const create = html("button", { class: "btn commit-btn big", type: "submit", form: "new-game" }, goRow, "Create game");

  heading(right, "Your seat");
  let chosen: string | null = null;
  const list = html("ul", { class: "power-list", role: "radiogroup", "aria-label": "Your power" }, right);
  const rows: HTMLElement[] = [];
  const pick = (p: string | null) => {
    chosen = p;
    for (const r of rows) {
      const on = (r.dataset.power || null) === p;
      r.classList.toggle("active", on);
      r.setAttribute("aria-checked", String(on));
      r.tabIndex = on ? 0 : -1;
    }
    create.textContent = p ? `Create game as ${map.powers[p]?.name ?? p}` : "Create game, random seat";
  };
  const rnd = html("button", { class: "power-row random", type: "button", role: "radio", "data-power": "" }, html("li", {}, list));
  const ru = html("span", { class: "pr-units" }, rnd);
  for (const p of POWER_ORDER) ru.append(codeCounter(p, 14));
  const rb = html("span", { class: "pr-body" }, rnd);
  html("span", { class: "pr-name" }, html("span", { class: "pr-head" }, rb), "Random");
  html("span", { class: "pr-char" }, rb, "Take whichever seat the draw gives you.");
  rnd.addEventListener("click", () => pick(null));
  rows.push(rnd);
  for (const p of POWER_ORDER) {
    const P = map.powers[p];
    if (!P) continue;
    const card = html("button", { class: "power-row", type: "button", role: "radio", "data-power": p }, html("li", {}, list));
    const us = html("span", { class: "pr-units" }, card);
    for (const u of map.start.units.filter((x) => x.power === p)) {
      const c = counterSvg(u.type, p, 30);
      svg("title", {}, c).textContent = `${UNIT_NAMES[u.type]} in ${map.provinces[u.at]?.name ?? u.at}`;
      us.append(c);
    }
    const body = html("span", { class: "pr-body" }, card);
    const h = html("span", { class: "pr-head" }, body);
    html("span", { class: "pr-name" }, h, P.name);
    html("span", { class: "pr-code" }, h, p);
    html("span", { class: "pr-tag" }, h, P.nuclear ? "Nuclear" : "Not nuclear");
    html("span", { class: "pr-char" }, body, CHARACTER[p] ?? "");
    card.addEventListener("click", () => pick(p));
    rows.push(card);
  }
  pick(null);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    out.replaceChildren();
    create.disabled = true;
    try {
      const v = await api<GameView>("POST", "games", {
        name: name.value, visibility: vis.value, deadline_hours: Number(dl.value), press: press.value,
        last_year: Number(len.value), power: chosen,
      });
      go(`#/g/${v.game.id}`);
    } catch (e) {
      errorLine(out, e);
      create.disabled = false;
    }
  });
}

// ------------------------------------------------------------------ practice
function practicePage() {
  const main = page("Practice game");
  const { cover, right } = lid(main, "new");
  html("h1", { class: "page-title" }, cover, "Practice game");
  html("p", { class: "lid-line small" }, cover, "You against six bots, in a short game to 2034. It's private and there's no waiting: each phase resolves the moment you commit, so you can play it through in one sitting or come back to it.");
  const out = html("div", { class: "form-out", "aria-live": "polite" }, cover);
  const acts = html("div", { class: "lid-actions" }, cover);
  const go1 = html("button", { class: "btn commit-btn big", type: "button" }, acts, "");
  html("a", { class: "link-btn", href: LEARN }, acts, "Take the tutorial first");

  heading(right, "Your power");
  let chosen = "USA";
  const list = html("ul", { class: "power-list", role: "radiogroup", "aria-label": "Your power" }, right);
  const rows: HTMLElement[] = [];
  const pick = (p: string) => {
    chosen = p;
    for (const r of rows) {
      const on = r.dataset.power === p;
      r.classList.toggle("active", on);
      r.setAttribute("aria-checked", String(on));
      r.tabIndex = on ? 0 : -1;
    }
    go1.textContent = `Start as ${map.powers[p]?.name ?? p}`;
  };
  for (const p of POWER_ORDER) {
    const P = map.powers[p];
    if (!P) continue;
    const card = html("button", { class: "power-row", type: "button", role: "radio", "data-power": p }, html("li", {}, list));
    const us = html("span", { class: "pr-units" }, card);
    for (const u of map.start.units.filter((x) => x.power === p)) us.append(counterSvg(u.type, p, 30));
    const body = html("span", { class: "pr-body" }, card);
    const h = html("span", { class: "pr-head" }, body);
    html("span", { class: "pr-name" }, h, P.name);
    if (p === "USA") html("span", { class: "pr-tag" }, h, "The tutorial's power");
    html("span", { class: "pr-char" }, body, CHARACTER[p] ?? "");
    card.addEventListener("click", () => pick(p));
    rows.push(card);
  }
  pick(chosen);
  go1.addEventListener("click", async () => {
    out.replaceChildren();
    go1.disabled = true;
    go1.textContent = "Setting up the board…";
    try {
      const v = await api<GameView>("POST", "games", {
        name: `${me?.display_name ?? "My"} practice game`, visibility: "private", deadline_hours: 72, press: "none",
        last_year: 2034, power: chosen,
      });
      await api("POST", `games/${v.game.id}/start`);
      go(`#/g/${v.game.id}`);
    } catch (e) {
      errorLine(out, e);
      go1.disabled = false;
      pick(chosen);
    }
  });
}

// ------------------------------------------------------------------ a game
async function gamePage(id: string) {
  let v: GameView;
  try {
    v = await api<GameView>("GET", `games/${id}`);
  } catch (e) {
    const main = page("Game");
    const { cover, right } = lid(main, "narrow");
    logo(cover, true);
    heading(right, "Can't open this game");
    errorLine(right, e);
    html("a", { class: "btn", href: "#/" }, right, "Back to your games");
    return;
  }
  if (v.game.status === "active" || v.game.status === "finished") {
    boardOpen = true;
    root.hidden = true;
    document.body.classList.remove("pages-mode");
    document.title = `${v.game.name} · Brink`;
    return openBoard(id, v);
  }
  formingPage(v);
}

function formingPage(v: GameView) {
  const g = v.game;
  const main = page(g.name);
  const { cover, right } = lid(main, "forming");
  html("h1", { class: "page-title" }, cover, g.name);
  html("p", { class: "lid-line small" }, cover, `${settingsWords(g)}. Created by ${g.created_by_name ?? "someone"}.`);
  if (g.status === "cancelled") {
    html("p", { class: "lede strong" }, cover, "This game was cancelled.");
    html("a", { class: "btn", href: "#/" }, cover, "Back to your games");
  }
  const mine = v.you.power;
  if (g.invite_code && g.status === "forming") {
    const inv = html("div", { class: "invite" }, cover);
    heading(inv, "Invite", g.visibility === "private" ? "Only people with the link can join" : undefined, "h3");
    const row = html("div", { class: "copy-row" }, inv);
    const url = html("input", { class: "field", readonly: true, value: inviteUrl(g.invite_code), "aria-label": "Invite link" }, row) as HTMLInputElement;
    const copy = html("button", { class: "btn", type: "button" }, row, "Copy link");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(url.value);
        copy.textContent = "Copied";
      } catch {
        url.select();
        copy.textContent = "Press Ctrl+C";
      }
      window.setTimeout(() => (copy.textContent = "Copy link"), 2000);
    });
    html("p", { class: "help" }, inv, `Code ${g.invite_code}`);
  }
  const out = html("div", { class: "form-out", "aria-live": "polite" }, cover);
  const acts = html("div", { class: "lid-actions" }, cover);
  const act = async (fn: () => Promise<unknown>) => {
    out.replaceChildren();
    try {
      await fn();
    } catch (e) {
      errorLine(out, e);
    }
  };
  if (g.status === "forming") {
    const humans = v.seats.filter((s) => s.human).length;
    if (v.you.creator) {
      const s = html("button", { class: "btn commit-btn big", type: "button" }, acts, humans >= 7 ? "Start game" : `Start game: ${7 - humans} bot${7 - humans === 1 ? "" : "s"}`);
      s.addEventListener("click", () => act(async () => {
        s.disabled = true;
        s.textContent = "Setting up the board…";
        await api("POST", `games/${g.id}/start`);
        location.reload();
      }).finally(() => (s.disabled = false)));
      html("p", { class: "help" }, acts, "Start when everyone who's coming has a seat. Empty seats are played by bots for the whole game.");
      const c = html("button", { class: "link-btn", type: "button" }, acts, "Cancel this game");
      c.addEventListener("click", () => act(async () => {
        if (!confirm("Cancel this game for everyone?")) return;
        await api("POST", `games/${g.id}/cancel`);
        go("#/");
      }));
    } else if (mine) {
      html("p", { class: "lede" }, acts, `You're in as ${map.powers[mine]?.name}. ${g.created_by_name ?? "The creator"} starts the game.`);
      const l = html("button", { class: "btn", type: "button" }, acts, "Leave this game");
      l.addEventListener("click", () => act(async () => {
        await api("POST", `games/${g.id}/leave`);
        formingPage(await api<GameView>("GET", `games/${g.id}`));
      }));
    } else if (session()) {
      const j = html("button", { class: "btn commit-btn big", type: "button" }, acts, "Join, random seat");
      j.addEventListener("click", () => act(async () => {
        formingPage(await api<GameView>("POST", `games/${g.id}/join`, { power: null, code: g.invite_code }));
      }));
    } else {
      const j = html("button", { class: "btn commit-btn big", type: "button" }, acts, "Sign in to join");
      j.addEventListener("click", () => needSignIn(`#/g/${g.id}`));
    }
  }

  heading(right, "Seats", `${v.seats.filter((s) => s.human).length} of 7 taken`);
  const ul = html("ul", { class: "power-list seats" }, right);
  for (const s of v.seats) {
    const li = html("li", { class: `seat-row${s.you ? " you" : ""}` }, ul);
    const us = html("span", { class: "pr-units" }, li);
    for (const u of map.start.units.filter((x) => x.power === s.power)) us.append(counterSvg(u.type, s.power, 24));
    const body = html("span", { class: "pr-body" }, li);
    const h = html("span", { class: "pr-head" }, body);
    html("span", { class: "pr-name" }, h, map.powers[s.power]?.name ?? s.power);
    html("span", { class: "pr-code" }, h, s.power);
    html("span", { class: `seat-holder${s.human ? "" : " open"}` }, body, s.human ? `${s.name}${s.you ? " (you)" : ""}` : "Open: a bot if nobody takes it");
    if (!s.human && g.status === "forming" && session()) {
      const take = html("button", { class: "btn small", type: "button" }, li, mine ? "Move here" : "Take seat");
      take.addEventListener("click", () => act(async () => {
        formingPage(await api<GameView>("POST", `games/${g.id}/join`, { power: s.power, code: g.invite_code }));
      }));
    }
  }
  if (g.status === "forming") {
    timer = window.setTimeout(async () => {
      try {
        const nv = await api<GameView>("GET", `games/${g.id}`);
        if (nv.game.status !== "forming") return location.reload();
        if (location.hash === `#/g/${g.id}` && !document.querySelector(".form-out .void")) formingPage(nv);
      } catch {
        /* keep the page as it is */
      }
    }, FORMING_POLL_MS);
  }
}

// ------------------------------------------------------------------ invite landing
async function joinPage(code: string) {
  const main = page("Invitation");
  const { cover, right } = lid(main, "narrow");
  logo(cover, true);
  let g: GameSummary;
  try {
    g = await api<GameSummary>("GET", `invite/${code}`);
  } catch (e) {
    heading(right, "Invitation");
    errorLine(right, e);
    html("a", { class: "btn", href: "#/" }, right, "Go to Brink");
    return;
  }
  html("p", { class: "lid-line" }, cover, `${g.created_by_name ?? "A friend"} invites you to play Brink: seven powers, secret simultaneous orders, and a Crisis track that can end the game with everyone losing.`);
  heading(right, g.name, `${g.players} of 7 seats taken`);
  html("p", { class: "help" }, right, `${settingsWords(g)}.`);
  if (g.status !== "forming") {
    html("p", { class: "lede" }, right, g.status === "cancelled" ? "This game was cancelled." : "This game has already started.");
    html("a", { class: "btn", href: `#/g/${g.id}` }, right, "Watch it");
    return;
  }
  if (!session()) {
    const b = html("button", { class: "btn commit-btn big", type: "button" }, right, "Sign in to join");
    b.addEventListener("click", () => needSignIn(`#/join/${code}`));
    return;
  }
  const out = html("div", { class: "form-out", "aria-live": "polite" }, right);
  const join = async (power: string | null) => {
    out.replaceChildren();
    try {
      await api<GameView>("POST", `games/${g.id}/join`, { power, code });
      go(`#/g/${g.id}`);
    } catch (e) {
      errorLine(out, e);
    }
  };
  const open = g.open_seats ?? [];
  const r = html("button", { class: "btn commit-btn big", type: "button" }, right, "Join, random seat");
  r.addEventListener("click", () => join(null));
  if (open.length) {
    html("p", { class: "help" }, right, `Or pick a power: ${listWords(open.map((p) => map.powers[p]?.name ?? p))} ${open.length === 1 ? "is" : "are"} open.`);
    const row = html("div", { class: "pick-row" }, right);
    for (const p of open) {
      const b = html("button", { class: "btn", type: "button" }, row);
      b.append(codeCounter(p, 18), document.createTextNode(` ${map.powers[p]?.name ?? p}`));
      b.addEventListener("click", () => join(p));
    }
  }
}
