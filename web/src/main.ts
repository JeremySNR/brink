import "@fontsource-variable/inter";
import "./style.css";
import { BoardView } from "./board";
import { Overlay } from "./overlay";
import { Panel } from "./panel";
import { POWER_COLOURS, POWER_ORDER, UNIT_NAMES, powerColour } from "./theme";
import type { Board, GameRecord, MapData, Phase, Unit } from "./types";
import { html, loadJSON, normaliseRecord, seasonLabel, svg } from "./util";

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

// ------------------------------------------------------------------ theme
const THEME_KEY = "brink.theme";
function initTheme() {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(THEME_KEY);
  } catch {
    /* storage unavailable */
  }
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "light" || q === "dark") saved = q;
  const sys = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    const t = saved === "light" || saved === "dark" ? saved : sys.matches ? "dark" : "light";
    document.documentElement.dataset.theme = t;
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", String(t === "dark"));
      btn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
    }
  };
  sys.addEventListener("change", apply);
  apply();
  return (next: "light" | "dark") => {
    saved = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
    apply();
  };
}

async function main() {
  const setTheme = initTheme();
  const params = new URLSearchParams(location.search);
  const gameUrl = params.get("game") || "sample-game.json";
  let board: Board, map: MapData, rec: GameRecord;
  try {
    [board, map, rec] = await Promise.all([
      loadJSON<Board>("board.json"),
      loadJSON<MapData>("map.json"),
      loadJSON<GameRecord>(gameUrl),
    ]);
  } catch (e) {
    $("#map").innerHTML = `<div class="load-error"><h2>Couldn't load the board</h2><p>${String(e)}</p>
      <p>Run <code>.venv/bin/python -m brink.geo.build</code>, then <code>npm run dev</code>.</p></div>`;
    return;
  }
  rec = normaliseRecord(rec, map);
  $("#game-name").textContent = gameUrl === "sample-game.json" ? "Sample game" : gameUrl.split("/").slice(-2).join("/");

  const view = new BoardView($("#map"), board, map);
  const tip = $("#tooltip");
  let current = 0;
  let selected: Unit | null = null;

  const overlay = new Overlay(view, {
    onUnitClick(u) {
      selected = selected && selected.at === u.at && selected.type === u.type ? null : u;
      overlay.selected = selected;
      showReach();
      overlay.draw();
    },
    onUnitHover(u, ev) {
      if (!u || !ev) return hideTip();
      showTip(ev, unitTip(u));
    },
  });

  const panel = new Panel($("#panel-body"), map, {
    onOrderHover(i) {
      overlay.hoverOrder = i;
      view.svg.classList.toggle("order-focus", i !== null);
      for (const g of view.layer.orders.querySelectorAll<SVGGElement>(".order")) {
        g.classList.toggle("hot", Number(g.dataset.index) === i);
      }
    },
    onProvinceFocus(pid) {
      view.zoomTo(pid);
    },
  });

  // ---------------------------------------------------------------- selection / reach
  function reachOf(u: Unit): Set<string> {
    const A = map.adj;
    const out = new Set<string>();
    const P = map.provinces;
    if (u.type === "A") (A.army[u.at] ?? []).forEach((p) => out.add(p));
    else if (u.type === "F") (A.fleet[u.at] ?? []).forEach((p) => out.add(p));
    else if (u.type === "C") {
      (A.army[u.at] ?? []).forEach((p) => out.add(p));
      if (P[u.at]?.coastal) {
        for (const s of A.fleet[u.at] ?? []) {
          if (P[s].kind !== "sea" || P[s].ocean) continue; // short hops cross marginal seas only
          for (const q of A.fleet[s] ?? []) if (P[q].kind === "land" && q !== u.at) out.add(q);
        }
      }
      for (const r of map.trade_routes) {
        if (r.ends[0] === u.at) out.add(r.ends[1]);
        if (r.ends[1] === u.at) out.add(r.ends[0]);
      }
    } else {
      const one = A.any[u.at] ?? [];
      for (const a of one) {
        if (P[a].kind === "land") out.add(a);
        for (const b of A.any[a] ?? []) if (P[b].kind === "land" && b !== u.at) out.add(b);
      }
    }
    out.delete(u.at);
    // drop targets the entry rules forbid outright (umbrellas/alignment change by phase)
    const ph = rec.phases[current];
    const mil = u.type === "A" || u.type === "F";
    for (const p of [...out]) {
      const mp = P[p];
      if (!mp || mp.impassable) out.delete(p);
      else if (mp.opens && ph.year < mp.opens) out.delete(p);
      else if (mp.kind === "land" && mil && mp.centre === "swing" && !ph.ownership[p]) out.delete(p);
      else if (mp.kind === "land" && mp.centre === "home" && mp.home_of !== u.power) {
        if (!mil) out.delete(p);
        else if (map.powers[mp.home_of!]?.nuclear) out.delete(p);
        else if (ph.umbrellas.some(([a, b]) => b === mp.home_of && a !== u.power)) out.delete(p);
      }
    }
    return out;
  }

  function showReach() {
    const L = view.layer.reach;
    L.replaceChildren();
    view.svg.classList.toggle("has-selection", !!selected);
    const info = $("#selection");
    if (!selected) {
      info.hidden = true;
      return;
    }
    const reach = reachOf(selected);
    for (const pid of reach) {
      const p = board.provinces[pid];
      if (!p) continue;
      const el = svg("path", { d: p.d, class: "reach" }, L);
      el.style.setProperty("--c", powerColour(selected.power));
    }
    const here = board.provinces[selected.at];
    if (here) {
      const el = svg("path", { d: here.d, class: "reach origin" }, L);
      el.style.setProperty("--c", powerColour(selected.power));
    }
    info.hidden = false;
    info.replaceChildren();
    const sw = html("span", { class: "dot" }, info);
    sw.style.background = powerColour(selected.power);
    html("strong", {}, info, ` ${map.powers[selected.power]?.adjective ?? selected.power} ${UNIT_NAMES[selected.type]} · ${map.provinces[selected.at]?.name}`);
    html("span", { class: "muted" }, info, ` — ${reach.size} reachable ${selected.type === "I" ? "(2 steps)" : selected.type === "C" ? "(land, hops, routes)" : ""}`);
    const x = html("button", { class: "icon-btn small", type: "button", "aria-label": "Clear selection" }, info, "×");
    x.addEventListener("click", () => {
      selected = null;
      overlay.selected = null;
      showReach();
      overlay.draw();
    });
  }

  // ---------------------------------------------------------------- tooltip
  function showTip(ev: MouseEvent, content: HTMLElement) {
    tip.replaceChildren(content);
    tip.hidden = false;
    const host = $("#stage").getBoundingClientRect();
    const tw = tip.offsetWidth;
    const th = tip.offsetHeight;
    let x = ev.clientX - host.left + 14;
    let y = ev.clientY - host.top + 14;
    if (x + tw > host.width - 8) x = ev.clientX - host.left - tw - 14;
    if (y + th > host.height - 8) y = ev.clientY - host.top - th - 14;
    tip.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, y)}px)`;
  }
  function hideTip() {
    tip.hidden = true;
  }

  function provinceTip(pid: string): HTMLElement {
    const ph = rec.phases[current];
    const mp = map.provinces[pid];
    const d = html("div", {});
    const h = html("div", { class: "tt-title" }, d);
    html("span", {}, h, mp.name);
    html("span", { class: "tt-id" }, h, pid);
    const owner = ph.ownership[pid] ?? null;
    let type = "";
    if (mp.impassable) type = "Impassable";
    else if (mp.kind === "sea") type = mp.chokepoint ? "Chokepoint" : "Sea zone";
    else if (mp.centre === "home") type = `Home of ${map.powers[mp.home_of!]?.name}${owner && owner !== mp.home_of ? ` · held by ${owner}` : ""}`;
    else if (mp.centre === "swing") type = owner ? `Swing state · aligned to ${map.powers[owner]?.name}` : "Swing state · unaligned";
    else type = "Open land (no centre)";
    const t = html("div", { class: "tt-type" }, d);
    if (owner || mp.home_of) {
      const dt = html("span", { class: "dot" }, t);
      dt.style.background = powerColour(owner ?? mp.home_of);
    }
    t.append(document.createTextNode(type));
    if (mp.opens && ph.year < mp.opens) html("div", { class: "tt-note" }, d, `Opens in ${mp.opens}`);
    const closed = ph.closed?.[pid];
    if (closed) html("div", { class: "tt-note warn" }, d, `Closed${closed.length ? ` to ${closed.join(", ")}` : ""}`);
    if (!mp.impassable) {
      const slots = html("div", { class: "tt-slots" }, d);
      const layers: [string, string[]][] = mp.kind === "land"
        ? [["Military", ["A", "F"]], ["Civil", ["C", "I"]]]
        : [["Military", ["F"]]];
      for (const [name, types] of layers) {
        const u = ph.units.find((x) => x.at === pid && types.includes(x.type) && !x.dislodged);
        const row = html("div", { class: "tt-slot" }, slots);
        html("span", { class: "k" }, row, name);
        if (u) {
          const dt = html("span", { class: "dot" }, row);
          dt.style.background = powerColour(u.power);
          row.append(document.createTextNode(` ${map.powers[u.power]?.adjective ?? u.power} ${UNIT_NAMES[u.type]}`));
        } else html("span", { class: "muted" }, row, "empty");
      }
      const army = map.adj.army[pid]?.length ?? 0;
      const any = map.adj.any[pid] ?? [];
      const seas = any.filter((n) => map.provinces[n]?.kind === "sea").length;
      const lands = any.length - seas;
      html("div", { class: "tt-foot" }, d, `${any.length} neighbours · ${lands} land${army !== lands ? ` (${army} by army)` : ""} · ${seas} sea`);
    }
    return d;
  }

  function unitTip(u: Unit): HTMLElement {
    const d = html("div", {});
    const h = html("div", { class: "tt-title" }, d);
    const dt = html("span", { class: "dot" }, h);
    dt.style.background = powerColour(u.power);
    html("span", {}, h, ` ${map.powers[u.power]?.adjective ?? u.power} ${UNIT_NAMES[u.type]}`);
    html("div", { class: "tt-type" }, d, `in ${map.provinces[u.at]?.name}${u.dislodged ? " · dislodged" : ""}`);
    const o = rec.phases[current].orders.find((x) => x.unit?.at === u.at && x.unit?.type === u.type && x.power === u.power);
    if (o) html("code", { class: "tt-order" }, d, `${o.text}${o.result !== "ok" ? `  (${o.result})` : ""}`);
    html("div", { class: "tt-foot" }, d, "Click to show where it can reach");
    return d;
  }

  for (const [pid, el] of view.paths) {
    el.addEventListener("mousemove", (ev) => showTip(ev, provinceTip(pid)));
    el.addEventListener("mouseleave", hideTip);
  }
  view.svg.addEventListener("click", () => {
    if (selected) {
      selected = null;
      overlay.selected = null;
      showReach();
      overlay.draw();
    }
  });

  // ---------------------------------------------------------------- timeline
  const scrub = $("#scrubber") as HTMLInputElement;
  const ticks = $("#ticks");
  scrub.max = String(rec.phases.length - 1);
  rec.phases.forEach((ph, i) => {
    const b = html("button", {
      class: `tick ${ph.kind}`, type: "button", "aria-label": `${seasonLabel(ph.season, ph.kind)} ${ph.year}`, title: `${seasonLabel(ph.season, ph.kind)} ${ph.year}`,
    }, ticks);
    b.style.left = `${rec.phases.length === 1 ? 50 : (i / (rec.phases.length - 1)) * 100}%`;
    html("span", { class: "tick-label" }, b, ph.kind === "winter" ? `W${String(ph.year).slice(2)}` : ph.kind === "retreat" ? "R" : `${ph.season[0].toUpperCase()}${String(ph.year).slice(2)}`);
    b.addEventListener("click", () => go(i));
  });

  function go(i: number) {
    current = Math.max(0, Math.min(rec.phases.length - 1, i));
    const ph: Phase = rec.phases[current];
    scrub.value = String(current);
    scrub.setAttribute("aria-valuetext", `${seasonLabel(ph.season, ph.kind)} ${ph.year}`);
    $("#phase-label").textContent = `${seasonLabel(ph.season, ph.kind)} ${ph.year}`;
    $("#sheet-summary").textContent = `${seasonLabel(ph.season, ph.kind)} ${ph.year} · Crisis ${ph.crisis}`;
    ticks.querySelectorAll(".tick").forEach((t, k) => t.classList.toggle("active", k === current));
    ($("#prev") as HTMLButtonElement).disabled = current === 0;
    ($("#next") as HTMLButtonElement).disabled = current === rec.phases.length - 1;
    if (selected && !ph.units.some((u) => u.at === selected!.at && u.type === selected!.type)) {
      selected = null;
      overlay.selected = null;
    }
    view.paintPhase(ph);
    overlay.setPhase(ph);
    showReach();
    panel.render(ph, current, rec.phases.length);
    const url = new URL(location.href);
    url.hash = ph.name;
    history.replaceState(null, "", url);
  }

  let timer = 0;
  const playBtn = $("#play");
  function setPlaying(on: boolean) {
    window.clearInterval(timer);
    playBtn.setAttribute("aria-pressed", String(on));
    playBtn.setAttribute("aria-label", on ? "Pause replay" : "Play replay");
    playBtn.classList.toggle("playing", on);
    if (on) {
      if (current >= rec.phases.length - 1) go(0);
      timer = window.setInterval(() => {
        if (current >= rec.phases.length - 1) return setPlaying(false);
        go(current + 1);
      }, 1800);
    }
  }
  playBtn.addEventListener("click", () => setPlaying(playBtn.getAttribute("aria-pressed") !== "true"));
  $("#prev").addEventListener("click", () => { setPlaying(false); go(current - 1); });
  $("#next").addEventListener("click", () => { setPlaying(false); go(current + 1); });
  scrub.addEventListener("input", () => { setPlaying(false); go(Number(scrub.value)); });
  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target as HTMLElement)?.tagName;
    if (tag === "INPUT" && (ev.target as HTMLInputElement).type !== "range") return;
    if (ev.key === "ArrowLeft") { ev.preventDefault(); setPlaying(false); go(current - 1); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); setPlaying(false); go(current + 1); }
    else if (ev.key === "Home") { go(0); }
    else if (ev.key === "End") { go(rec.phases.length - 1); }
    else if (ev.key === "Escape" && selected) { selected = null; overlay.selected = null; showReach(); overlay.draw(); }
    else if ((ev.key === "+" || ev.key === "=") && !ev.metaKey && !ev.ctrlKey) view.zoomBy(1.6);
    else if (ev.key === "-" && !ev.metaKey && !ev.ctrlKey) view.zoomBy(1 / 1.6);
    else if (ev.key === "0") view.fit();
  });

  // ---------------------------------------------------------------- controls
  $("#zoom-in").addEventListener("click", () => view.zoomBy(1.6));
  $("#zoom-out").addEventListener("click", () => view.zoomBy(1 / 1.6));
  $("#zoom-reset").addEventListener("click", () => view.fit());
  $("#theme-toggle").addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    view.paintPhase(rec.phases[current]);
  });
  const legend = $("#legend");
  $("#legend-toggle").addEventListener("click", () => {
    const open = legend.classList.toggle("open");
    $("#legend-toggle").setAttribute("aria-expanded", String(open));
  });
  buildLegend($("#legend-body"));
  const sheet = $("#panel");
  $("#sheet-handle").addEventListener("click", () => {
    const open = sheet.classList.toggle("open");
    $("#sheet-handle").setAttribute("aria-expanded", String(open));
  });
  new MutationObserver(() => view.paintPhase(rec.phases[current])).observe(document.documentElement, {
    attributes: true, attributeFilter: ["data-theme"],
  });

  const start = rec.phases.findIndex((p) => p.name === location.hash.slice(1));
  view.fit(false);
  go(start >= 0 ? start : 0);
  const focus = params.get("focus")?.toUpperCase();
  if (focus && board.provinces[focus]) setTimeout(() => view.zoomTo(focus), 50);
  requestAnimationFrame(() => setTimeout(() => document.body.classList.add("ready"), 200));
}

function buildLegend(root: HTMLElement) {
  const sec = (title: string) => {
    const s = html("div", { class: "legend-sec" }, root);
    html("h4", {}, s, title);
    return html("ul", {}, s);
  };
  const icon = (draw: (g: SVGGElement) => void, w = 26, h = 18) => {
    const s = svg("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true" });
    const g = svg("g", {}, s);
    draw(g);
    return s;
  };
  const item = (ul: HTMLElement, ic: SVGSVGElement, label: string) => {
    const li = html("li", {}, ul);
    li.append(ic);
    html("span", {}, li, label);
  };
  const col = POWER_COLOURS.USA;
  const units = sec("Units");
  const tok = (t: string) => (g: SVGGElement) => {
    const r = 6.2;
    const cx = 13, cy = 9;
    if (t === "A") svg("circle", { cx, cy, r, fill: col, class: "lg-token" }, g);
    else {
      const pts = t === "F" ? [[0, -r * 1.18], [r * 1.12, r * 0.78], [-r * 1.12, r * 0.78]]
        : t === "C" ? [[0, -r * 1.22], [r * 1.22, 0], [0, r * 1.22], [-r * 1.22, 0]]
        : [0, 60, 120, 180, 240, 300].map((a) => [r * 1.1 * Math.cos(((a + 30) * Math.PI) / 180), r * 1.1 * Math.sin(((a + 30) * Math.PI) / 180)]);
      svg("polygon", { points: pts.map(([x, y]) => `${cx + x},${cy + y}`).join(" "), fill: col, class: "lg-token" }, g);
    }
    const tx = svg("text", { x: cx, y: cy + 2.3, class: "lg-glyph" }, g);
    tx.textContent = t;
  };
  item(units, icon(tok("A")), "Army · military");
  item(units, icon(tok("F")), "Fleet · military");
  item(units, icon(tok("C")), "Capital · civil");
  item(units, icon(tok("I")), "Influence · civil");
  const prov = sec("Provinces");
  const swatch = (cls: string, fill?: string) => (g: SVGGElement) => {
    const r = svg("rect", { x: 2, y: 3, width: 22, height: 12, rx: 3, class: `lg-sw ${cls}` }, g);
    if (fill) r.style.fill = fill;
  };
  item(prov, icon(swatch("home", POWER_COLOURS.EU)), "Home centre (owner colour)");
  item(prov, icon(swatch("aligned", "#9DCAEA")), "Swing state, aligned");
  item(prov, icon(swatch("swing")), "Swing state, unaligned");
  item(prov, icon(swatch("filler")), "Open land");
  item(prov, icon(swatch("impassable")), "Impassable");
  item(prov, icon(swatch("chokepoint")), "Chokepoint");
  item(prov, icon(swatch("marginal")), "Marginal sea (Capital hops)");
  item(prov, icon(swatch("ocean")), "Ocean (trade routes only)");
  const marks = sec("Markers");
  item(marks, icon((g) => svg("circle", { cx: 13, cy: 9, r: 3.2, fill: col, class: "lg-centre home" }, g)), "Home supply centre");
  item(marks, icon((g) => svg("circle", { cx: 13, cy: 9, r: 3.4, stroke: col, class: "lg-centre aligned" }, g)), "Swing centre (ring = aligned)");
  item(marks, icon((g) => {
    svg("path", { d: "M2 9H24", class: "lg-route" }, g);
  }), "Trade route");
  item(marks, icon((g) => svg("path", { d: "M2 9H24", class: "lg-route closed" }, g)), "Route not yet open");
  item(marks, icon((g) => svg("path", { d: "M4 4L9 9L4 14M10 4L15 9L10 14", class: "lg-chev" }, g)), "Continues across the edge");
  const ord = sec("Orders");
  const line = (cls: string, extra?: (g: SVGGElement) => void) => (g: SVGGElement) => {
    svg("path", { d: "M3 9H21", class: `lg-o ${cls}` }, g);
    extra?.(g);
  };
  const head = (g: SVGGElement) => svg("path", { d: "M18 5L23 9L18 13Z", class: "lg-head" }, g);
  item(ord, icon(line("move", head)), "Move");
  item(ord, icon(line("support", (g) => svg("path", { d: "M18 5L23 9L18 13", class: "lg-head-open" }, g))), "Support");
  item(ord, icon(line("convoy")), "Convoy");
  item(ord, icon((g) => svg("path", { d: "M3 9L6 6L9 12L12 6L15 12L18 6L21 9", class: "lg-o zig" }, g)), "Disrupt");
  item(ord, icon((g) => {
    svg("path", { d: "M3 9H15", class: "lg-o sanction" }, g);
    svg("circle", { cx: 19.5, cy: 9, r: 4, class: "lg-blocked" }, g);
    svg("path", { d: "M16.7 11.8L22.3 6.2", class: "lg-blocked" }, g);
  }), "Sanction");
  item(ord, icon(line("move failed", (g) => {
    head(g);
    svg("path", { d: "M10 5.5L15 12.5M15 5.5L10 12.5", class: "lg-x" }, g);
  })), "Failed / bounced");
  item(ord, icon((g) => {
    svg("circle", { cx: 13, cy: 9, r: 5, class: "lg-leaf" }, g);
    svg("path", { d: "M10.6 11.4C10.3 8.4 12.3 6.8 15 6.9C15.1 9.6 13.5 11.6 10.6 11.4ZM10.6 11.4L13.4 8.5", class: "lg-leaf-glyph" }, g);
  }), "Commons");
  void POWER_ORDER;
}

main();
