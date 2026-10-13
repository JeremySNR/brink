import "@fontsource/archivo/400.css";
import "@fontsource/archivo/500.css";
import "@fontsource/archivo/600.css";
import "@fontsource/archivo/700.css";
import "@fontsource/archivo/800.css";
import "@fontsource/barlow-condensed/500.css";
import "@fontsource/barlow-condensed/600.css";
import "@fontsource/barlow-condensed/700.css";
import "@fontsource/barlow-condensed/500-italic.css";
import "@fontsource/barlow-condensed/600-italic.css";
import "./style.css";
import { BoardView } from "./board";
import { codeCounter, counterSvg } from "./counter";
import { Overlay } from "./overlay";
import { Panel } from "./panel";
import { PlayController } from "./play";
import { BUFF, POWER_COLOURS, RED, UNIT_NAMES, arrowInk, homeFill, swingFill } from "./theme";
import type { Board, GameRecord, MapData, Phase, Unit } from "./types";
import { html, loadJSON, normaliseRecord, seasonLabel, svg } from "./util";

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

async function main() {
  const params = new URLSearchParams(location.search);
  const playMode = params.has("play");
  const gameUrl = params.get("game") || "sample-game.json";
  let board: Board, map: MapData, rec: GameRecord;
  try {
    [board, map, rec] = await Promise.all([
      loadJSON<Board>("board.json"),
      loadJSON<MapData>("map.json"),
      playMode ? Promise.resolve({ map: "brink-v0", phases: [], result: null } as GameRecord) : loadJSON<GameRecord>(gameUrl),
    ]);
  } catch (e) {
    $("#map").innerHTML = `<div class="load-error"><h2>Couldn't load the board</h2><p>${String(e)}</p>
      <p>Run <code>.venv/bin/python -m brink.geo.build</code>, then <code>npm run dev</code>.</p></div>`;
    return;
  }
  rec = normaliseRecord(rec, map);
  $("#game-name").textContent = playMode ? "Play against six bots" : gameUrl === "sample-game.json" ? "Sample game" : `Replay: ${gameUrl.split("/").pop()!.replace(/\.json$/, "")}`;
  document.body.classList.toggle("play-mode", playMode);

  const view = new BoardView($("#map"), board, map);
  const tip = $("#tooltip");
  let current = 0;
  let selected: Unit | null = null;
  // play mode: the controller owns the live (pending decision) phase, appended as the last phase
  let play: PlayController | null = null;
  let liveIndex: number | null = null;
  const isLive = () => play !== null && liveIndex !== null && current === liveIndex;

  const overlay = new Overlay(view, {
    onUnitClick(u) {
      if (isLive()) return play!.onUnitClick(u);
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
      svg("path", { d: p.d, class: "reach ink" }, L);
    }
    const here = board.provinces[selected.at];
    if (here) svg("path", { d: here.d, class: "reach ink origin" }, L);
    info.hidden = false;
    info.replaceChildren();
    info.append(counterSvg(selected.type, selected.power, 24));
    html("strong", {}, info, `${map.powers[selected.power]?.adjective ?? selected.power} ${UNIT_NAMES[selected.type]} in ${map.provinces[selected.at]?.name}`);
    html("span", { class: "hint" }, info, `${reach.size} provinces in reach${selected.type === "I" ? " (2 steps)" : selected.type === "C" ? " (land, hops, routes)" : ""}`);
    const x = html("button", { class: "x-btn", type: "button", "aria-label": "Clear selection" }, info);
    x.append(xIcon());
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
    else if (mp.centre === "home") type = `Home of ${map.powers[mp.home_of!]?.name}${owner && owner !== mp.home_of ? `, held by ${map.powers[owner]?.name ?? owner}` : ""}`;
    else if (mp.centre === "swing") type = owner ? `Swing state aligned to ${map.powers[owner]?.name}` : "Swing, unaligned";
    else type = "Open land (no centre)";
    const t = html("div", { class: "tt-type" }, d);
    if (owner || mp.home_of) t.append(codeCounter(owner ?? mp.home_of!, 16));
    t.append(document.createTextNode(type));
    if (mp.opens && ph.year < mp.opens) html("div", { class: "tt-note" }, d, `Opens in ${mp.opens}`);
    const occupier = mp.centre === "swing" && owner
      ? ph.units.find((x) => x.at.split("/")[0] === pid && x.power !== owner && !x.dislodged)?.power
      : undefined;
    if (occupier) html("div", { class: "tt-note warn" }, d,
      `Contested by ${map.powers[occupier]?.name ?? occupier}: pays ${map.powers[owner!]?.name ?? owner} no supply this Winter`);
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
          row.append(counterSvg(u.type, u.power, 18));
          html("span", {}, row, `${map.powers[u.power]?.adjective ?? u.power} ${UNIT_NAMES[u.type]}`);
        } else html("span", { class: "muted" }, row, "Empty");
      }
      const army = map.adj.army[pid]?.length ?? 0;
      const any = map.adj.any[pid] ?? [];
      const seas = any.filter((n) => map.provinces[n]?.kind === "sea").length;
      const lands = any.length - seas;
      html("div", { class: "tt-foot" }, d, `${any.length} neighbours: ${lands} land${army !== lands ? ` (${army} by army)` : ""}, ${seas} sea`);
    }
    return d;
  }

  function unitTip(u: Unit): HTMLElement {
    const d = html("div", {});
    const h = html("div", { class: "tt-title" }, d);
    h.append(counterSvg(u.type, u.power, 20));
    html("span", {}, h, `${map.powers[u.power]?.adjective ?? u.power} ${UNIT_NAMES[u.type]}`);
    html("div", { class: "tt-type" }, d, `in ${map.provinces[u.at]?.name}${u.dislodged ? ", dislodged" : ""}`);
    const o = rec.phases[current].orders.find((x) => x.unit?.at === u.at && x.unit?.type === u.type && x.power === u.power);
    if (o) html("div", { class: "tt-order notation" }, d, `${o.text}${o.result !== "ok" ? `  (${o.result})` : ""}`);
    const foot = !isLive() ? "Click to show where it can reach" : u.power === play!.power ? "Click to give it an order" : "";
    if (foot) html("div", { class: "tt-foot" }, d, foot);
    return d;
  }

  for (const [pid, el] of view.paths) {
    el.addEventListener("mousemove", (ev) => showTip(ev, provinceTip(pid)));
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("click", (ev) => {
      if (!isLive()) return;
      ev.stopPropagation();
      play!.onProvinceClick(pid);
    });
  }
  view.svg.addEventListener("click", () => {
    if (isLive()) return play!.cancel();
    if (selected) {
      selected = null;
      overlay.selected = null;
      showReach();
      overlay.draw();
    }
  });

  // ---------------------------------------------------------------- turn record track
  // Boxes for each year, split into Spring and Fall halves with Winter at the year's end. The track is the
  // replay timeline: clicking a half jumps to its first phase; ←/→ step every phase (retreats, winter).
  const trt = $("#trt-years");
  type Cell = { el: HTMLButtonElement; idx: number[]; key: string };
  let cells: Cell[] = [];
  let endYear = 2040;
  const cellKey = (ph: Phase) => `${ph.year}-${ph.kind === "winter" ? "w" : ph.season === "fall" ? "f" : "s"}`;
  function buildTrack() {
    trt.replaceChildren();
    cells = [];
    const first = rec.phases[0]?.year ?? map.start.year;
    const lastPh = rec.phases[rec.phases.length - 1];
    const resultYear = (rec.result as { year?: number } | null)?.year;
    const end = Math.max(play?.lastYear() ?? (resultYear && rec.result?.kind === "time" ? resultYear : endYear), lastPh?.year ?? first);
    const byKey = new Map<string, number[]>();
    rec.phases.forEach((ph, i) => {
      const k = cellKey(ph);
      if (!byKey.has(k)) byKey.set(k, []);
      byKey.get(k)!.push(i);
    });
    const finalYear = rec.result ? (resultYear ?? lastPh?.year) : null;
    for (let y = first; y <= Math.max(end, first); y++) {
      const li = html("li", { class: `year${y === end ? " end" : ""}${finalYear === y ? " final" : ""}` }, trt);
      const lab = html("span", { class: "yl" }, li);
      html("span", { class: "num" }, lab, String(y));
      if (y === end) html("span", { class: "end-mark" }, lab, "End");
      const halves = html("div", { class: "halves" }, li);
      for (const [h, name, short] of [["s", "Spring", "Spr"], ["f", "Fall", "Fall"], ["w", "Winter", "W"]] as const) {
        const key = `${y}-${h}`;
        const idx = byKey.get(key) ?? [];
        const b = html("button", {
          class: `half h-${h}${idx.length ? "" : " empty"}`, type: "button",
          "aria-label": `${name} ${y}`, title: `${name} ${y}`,
        }, halves) as HTMLButtonElement;
        html("span", { class: "hl" }, b, short);
        b.disabled = !idx.length;
        b.addEventListener("click", () => { setPlaying(false); if (idx.length) go(idx[0]); });
        cells.push({ el: b, idx, key });
      }
    }
  }

  function paintTrack() {
    const curKey = rec.phases[current] ? cellKey(rec.phases[current]) : "";
    const liveKey = liveIndex !== null ? cellKey(rec.phases[liveIndex]) : null;
    for (const c of cells) {
      const isCur = c.key === curKey;
      c.el.classList.toggle("past", c.idx.length > 0 && c.idx[c.idx.length - 1] < current);
      c.el.classList.toggle("current", isCur);
      c.el.classList.toggle("live", c.key === liveKey);
      if (isCur) c.el.setAttribute("aria-current", "step");
      else c.el.removeAttribute("aria-current");
      c.el.querySelector(".turn-ctr")?.remove();
      c.el.querySelector(".now-flag")?.remove();
      if (c.key === liveKey && !isCur) html("span", { class: "now-flag" }, c.el, "Now");
      if (isCur) {
        const ph = rec.phases[current];
        const live = current === liveIndex;
        const label = live ? "Now" : ph.kind === "winter" ? "W" : ph.kind === "retreat" ? "Ret" : ph.season === "fall" ? "Fall" : "Spr";
        html("span", { class: `turn-ctr${live ? " you" : ""}`, "aria-hidden": "true" }, c.el, label);
        // keep the counter in view on narrow screens (the track scrolls sideways)
        const box = trt.getBoundingClientRect();
        const r = c.el.getBoundingClientRect();
        if (r.left < box.left || r.right > box.right) trt.scrollLeft += r.left - box.left - box.width / 2 + r.width / 2;
      }
    }
  }
  buildTrack();
  {
    const y0 = rec.phases[0]?.year ?? map.start.year;
    $("#cartouche-years").textContent = `The world, ${y0} to ${Math.max(2040, rec.phases[rec.phases.length - 1]?.year ?? y0)}`;
  }

  function go(i: number) {
    current = Math.max(0, Math.min(rec.phases.length - 1, i));
    const ph: Phase = rec.phases[current];
    $("#phase-label").textContent = `${seasonLabel(ph.season, ph.kind)} ${ph.year}`;
    $("#sheet-summary").textContent = `${seasonLabel(ph.season, ph.kind)} ${ph.year}, Crisis ${ph.crisis}`;
    paintTrack();
    ($("#prev") as HTMLButtonElement).disabled = current === 0;
    ($("#next") as HTMLButtonElement).disabled = current === rec.phases.length - 1;
    if (selected && !ph.units.some((u) => u.at === selected!.at && u.type === selected!.type)) {
      selected = null;
      overlay.selected = null;
    }
    view.paintPhase(ph);
    if (isLive()) {
      selected = null;
      overlay.selected = null;
      overlay.setPhase(play!.displayPhase());
      showReach();
      play!.renderPanel($("#panel-body"));
      play!.paint();
      return;
    }
    play?.leaveLive();
    overlay.setPhase(ph);
    showReach();
    panel.render(ph, current, rec.phases.length, play ? { you: play.power ?? undefined } : {});
    if (play) {
      play.renderBanner($("#panel-body"), current);
      return;
    }
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
  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target as HTMLElement)?.tagName;
    if (tag === "INPUT" && (ev.target as HTMLInputElement).type !== "checkbox") return;
    if (tag === "SELECT" || tag === "TEXTAREA") return;
    if (play && play.onKey(ev, isLive())) return;
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
  const legend = $("#legend");
  const setLegend = (open: boolean) => {
    legend.classList.toggle("open", open);
    $("#legend-toggle").setAttribute("aria-expanded", String(open));
  };
  // the key starts open where the sheet has room for it
  // (play mode zooms to the player's homes, where an open key would cover their own counters)
  setLegend(!playMode && window.innerWidth >= 1100 && window.innerHeight >= 760);
  $("#legend-toggle").addEventListener("click", () => setLegend(!legend.classList.contains("open")));
  buildLegend($("#legend-body"));
  const sheet = $("#panel");
  $("#sheet-handle").addEventListener("click", () => {
    const open = sheet.classList.toggle("open");
    $("#sheet-handle").setAttribute("aria-expanded", String(open));
  });

  const start = rec.phases.findIndex((p) => p.name === location.hash.slice(1));
  view.fit(false);
  go(start >= 0 ? start : 0);
  if (playMode) {
    play = new PlayController({
      view, map, overlay,
      setRecord(next, live, goTo) {
        setPlaying(false);
        rec = next.phases.length ? normaliseRecord(next, map) : next;
        liveIndex = null;
        if (live) {
          rec.phases.push(live);
          liveIndex = rec.phases.length - 1;
        }
        buildTrack();
        go(goTo ?? rec.phases.length - 1);
      },
      go: (i) => go(i),
      redraw: () => go(current),
      current: () => current,
      total: () => rec.phases.length,
      liveIndex: () => liveIndex,
      phase: (i) => rec.phases[i],
      stateCards: (root, ph) => panel.render(ph, current, rec.phases.length, { root, header: false, orders: false, you: play?.power ?? undefined }),
    });
    play.start(params.get("play"));
  }
  const focus = params.get("focus")?.toUpperCase();
  if (focus && board.provinces[focus]) setTimeout(() => view.zoomTo(focus), 50);
  requestAnimationFrame(() => setTimeout(() => document.body.classList.add("ready"), 200));
}

function xIcon(): SVGSVGElement {
  const s = svg("svg", { viewBox: "0 0 16 16", width: 14, height: 14, "aria-hidden": "true" });
  svg("path", { d: "M4 4l8 8M12 4l-8 8" }, s);
  return s;
}

/** Terrain and unit key, printed in the map's corner. */
function buildLegend(root: HTMLElement) {
  const sec = (title: string) => {
    const s = html("div", { class: "legend-sec" }, root);
    html("h4", {}, s, title);
    return html("ul", {}, s);
  };
  const icon = (draw: (g: SVGGElement) => void, w = 26, h = 16) => {
    const s = svg("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true" });
    draw(svg("g", {}, s));
    return s;
  };
  const item = (ul: HTMLElement, ic: Element, label: string) => {
    const li = html("li", {}, ul);
    li.append(ic);
    html("span", {}, li, label);
  };
  const units = sec("Counters");
  item(units, counterSvg("A", "USA", 20), "Army (A)");
  item(units, counterSvg("F", "USA", 20), "Fleet (F)");
  item(units, counterSvg("C", "USA", 20), "Capital (C)");
  item(units, counterSvg("I", "USA", 20), "Influence (I)");
  const prov = sec("Land");
  const sw = (fill: string, cls = "") => (g: SVGGElement) => {
    svg("rect", { x: 1.5, y: 1.5, width: 23, height: 13, class: `lg-sw ${cls}`, fill }, g);
  };
  item(prov, icon(sw(homeFill("EU"))), "Home");
  item(prov, icon((g) => {
    sw(swingFill("IND"))(g);
    svg("rect", { x: 7.6, y: 4.6, width: 11, height: 7, class: "lg-ctl", fill: POWER_COLOURS.IND }, g);
    const t = svg("text", { x: 13.1, y: 10.2, class: "lg-ctl-t" }, g);
    t.textContent = "IND";
  }), "Swing, aligned");
  item(prov, icon((g) => {
    sw(swingFill("IND"))(g);
    svg("rect", { x: 7.6, y: 4.6, width: 11, height: 7, class: "lg-ctl", fill: POWER_COLOURS.IND }, g);
    svg("rect", { x: 13.1, y: 4.6, width: 5.5, height: 7, fill: POWER_COLOURS.CHN }, g);
    svg("rect", { x: 7.6, y: 4.6, width: 11, height: 7, class: "lg-ctl lg-contested", fill: "none" }, g);
  }), "Contested: no supply");
  item(prov, icon((g) => {
    sw(BUFF)(g);
    svg("circle", { cx: 13, cy: 8, r: 3, class: "lg-ring" }, g);
  }), "Swing, unaligned");
  item(prov, icon(sw("", "filler")), "Open land");
  item(prov, icon(sw("url(#hatch-impassable)", "imp")), "Impassable");
  const seaSec = sec("Sea");
  item(seaSec, icon(sw("", "marginal")), "Sea");
  item(seaSec, icon(sw("", "ocean")), "Ocean");
  item(seaSec, icon((g) => {
    sw("", "marginal")(g);
    const c = svg("g", { class: "choke-icon", transform: "translate(13 8)" }, g);
    svg("rect", { x: -3.4, y: -3.4, width: 6.8, height: 6.8, transform: "rotate(45)" }, c);
    svg("path", { d: "M-1.5 -1.9V1.9M1.5 -1.9V1.9", class: "gate" }, c);
  }), "Chokepoint");
  const marks = seaSec;
  item(marks, icon((g) => { svg("path", { d: "M2 8H24", class: "lg-route" }, g); }), "Trade route");
  item(marks, icon((g) => { svg("path", { d: "M2 8H24", class: "lg-route off" }, g); }), "Route shut");
  item(marks, icon((g) => svg("path", { d: "M5 4L9 8L5 12M11 4L15 8L11 12", class: "lg-chev" }, g)), "Wraps around");
  const ord = sec("Orders");
  const arrow = (col: string, extra?: (g: SVGGElement) => void, dash?: string) => (g: SVGGElement) => {
    svg("path", { d: "M3 8H18", class: "lg-o-case", "stroke-dasharray": dash }, g);
    svg("path", { d: "M3 8H18", class: "lg-o", stroke: col, "stroke-dasharray": dash }, g);
    if (!extra) svg("path", { d: "M17 4L24 8L17 12Z", class: "lg-head", fill: col }, g);
    extra?.(g);
  };
  item(ord, icon(arrow(RED)), "Your order");
  item(ord, icon(arrow(arrowInk("USA"))), "Other move");
  item(ord, icon(arrow(arrowInk("CHN"), (g) => svg("path", { d: "M17 4.5L23 8L17 11.5", class: "lg-head-open", stroke: arrowInk("CHN") }, g), "3 2")), "Support");
  item(ord, icon((g) => svg("path", { d: "M3 8L6 5L9 11L12 5L15 11L18 5L21 8", class: "lg-zig", stroke: arrowInk("GLF") }, g)), "Disrupt");
  item(ord, icon((g) => {
    arrow(arrowInk("EU"))(g);
    g.setAttribute("opacity", "0.45");
    const x = svg("g", { transform: "translate(11 8)" }, g.parentNode as Element);
    svg("rect", { x: -3.6, y: -3.6, width: 7.2, height: 7.2, class: "lg-fail" }, x);
    svg("path", { d: "M-2 -2L2 2M2 -2L-2 2", class: "lg-fail-x" }, x);
  }), "Failed");
  item(ord, icon((g) => {
    svg("circle", { cx: 13, cy: 8, r: 5, class: "lg-leaf" }, g);
    svg("path", { d: "M10.6 10.4C10.3 7.4 12.3 5.8 15 5.9C15.1 8.6 13.5 10.6 10.6 10.4ZM10.6 10.4L13.4 7.5", class: "lg-leaf-glyph" }, g);
  }), "Commons");
}

main();
