"""Static SVG render of the board at the starting position (same visual language as the web viewer)."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

# Okabe-Ito derived, darkened so white glyphs stay legible. Keep in sync with web/src/theme.ts.
POWER_COLOURS = {
    "USA": "#1F6FB2",
    "CHN": "#C8501E",
    "EU": "#3E95D1",
    "RUS": "#566079",
    "IND": "#C98A08",
    "JPN": "#B45E91",
    "GLF": "#0F8A68",
}
PAPER = {
    "ocean": "#D9E5EC",
    "ocean_deep": "#C9D9E3",
    "sea_border": "#9DB4C4",
    "land": "#EFEAE0",
    "swing": "#E9DCC2",
    "impassable": "#CFCDC8",
    "border": "#FFFFFF",
    "coast": "#7F97A8",
    "label": "#2E3440",
    "sea_label": "#4F7591",
    "route": "#486A86",
    "choke": "#BDD2E0",
}


def tint(hex_colour: str, amount: float, base: str = "#FFFFFF") -> str:
    a = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    c = [round(x + (y - x) * amount) for x, y in zip(a, b)]
    return "#" + "".join(f"{v:02X}" for v in c)


def _token(kind: str, x: float, y: float, colour: str, r: float = 6.5) -> str:
    stroke = 'stroke="#FFFFFF" stroke-width="1.1"'
    if kind == "A":
        shape = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{colour}" {stroke}/>'
    elif kind == "F":
        pts = [(x, y - r * 1.15), (x + r * 1.1, y + r * 0.8), (x - r * 1.1, y + r * 0.8)]
        shape = f'<polygon points="{_pts(pts)}" fill="{colour}" {stroke} stroke-linejoin="round"/>'
    elif kind == "C":
        pts = [(x, y - r * 1.2), (x + r * 1.2, y), (x, y + r * 1.2), (x - r * 1.2, y)]
        shape = f'<polygon points="{_pts(pts)}" fill="{colour}" {stroke} stroke-linejoin="round"/>'
    else:  # I
        pts = [(x + r * 1.08 * math.cos(math.radians(a)), y + r * 1.08 * math.sin(math.radians(a)))
               for a in range(30, 390, 60)]
        shape = f'<polygon points="{_pts(pts)}" fill="{colour}" {stroke} stroke-linejoin="round"/>'
    glyph = (f'<text x="{x:.1f}" y="{y + (1.6 if kind == "F" else 2.3):.1f}" font-size="6.2" font-weight="700" '
             f'fill="#FFFFFF" text-anchor="middle">{kind}</text>')
    return f'<g filter="url(#tok)">{shape}</g>{glyph}'


def _pts(pts):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def render_preview(board: dict, m: dict, path: Path) -> None:
    W, H = board["width"], board["height"]
    P = m["provinces"]
    out: list[str] = []
    a = out.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -10 {W + 20} {H + 20}" '
      f'width="{W + 20:.0f}" height="{H + 20:.0f}" font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">')
    a("<defs>")
    a('<pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
      '<rect width="5" height="5" fill="#CFCDC8"/><line x1="0" y1="0" x2="0" y2="5" stroke="#B5B2AB" '
      'stroke-width="1.6"/></pattern>')
    a('<pattern id="swinghatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">'
      f'<rect width="4" height="4" fill="{PAPER["swing"]}"/><line x1="0" y1="0" x2="0" y2="4" stroke="#DCCBAA" '
      'stroke-width="0.8"/></pattern>')
    a('<radialGradient id="oceang" cx="50%" cy="45%" r="70%"><stop offset="0" stop-color="#E1EBF1"/>'
      f'<stop offset="1" stop-color="{PAPER["ocean_deep"]}"/></radialGradient>')
    a('<filter id="tok" x="-50%" y="-50%" width="200%" height="200%"><feDropShadow dx="0" dy="0.8" '
      'stdDeviation="0.8" flood-color="#000" flood-opacity="0.35"/></filter>')
    a('<filter id="coastglow" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="2.2"/>'
      '</filter>')
    a("</defs>")
    a(f'<rect x="-10" y="-10" width="{W + 20}" height="{H + 20}" fill="#F4F1EA"/>')
    a(f'<path d="{board["outline"]}" fill="url(#oceang)"/>')
    a(f'<path d="{board["graticule"]}" fill="none" stroke="#FFFFFF" stroke-opacity="0.45" stroke-width="0.5"/>')

    seas = [p for p, v in P.items() if v["kind"] == "sea"]
    lands = [p for p, v in P.items() if v["kind"] == "land"]
    for p in seas:
        if P[p]["chokepoint"]:
            a(f'<path d="{board["provinces"][p]["d"]}" fill="{PAPER["choke"]}" fill-opacity="0.85"/>')
    if board.get("lakes"):
        a(f'<path d="{board["lakes"]}" fill="#D3E1EA" stroke="{PAPER["coast"]}" stroke-width="0.5"/>')
    if board.get("decor"):
        a(f'<path d="{board["decor"]}" fill="#E6E2D9" stroke="{PAPER["coast"]}" stroke-width="0.4"/>')
    # coast emphasis: blurred dark halo under all land
    land_d = "".join(board["provinces"][p]["d"] for p in lands)
    a(f'<path d="{land_d}" fill="none" stroke="#6F8FA6" stroke-opacity="0.45" stroke-width="3.5" '
      f'filter="url(#coastglow)"/>')
    for p in seas:
        a(f'<path d="{board["provinces"][p]["d"]}" fill="none" stroke="{PAPER["sea_border"]}" '
          f'stroke-width="0.6" stroke-dasharray="2.5 2"/>')
    for p in lands:
        v = P[p]
        if v["impassable"]:
            fill = "url(#hatch)"
        elif v["centre"] == "home":
            fill = tint(POWER_COLOURS[v["home_of"]], 0.18)
        elif v["centre"] == "swing":
            fill = "url(#swinghatch)"
        else:
            fill = PAPER["land"]
        a(f'<path d="{board["provinces"][p]["d"]}" fill="{fill}" stroke="{PAPER["border"]}" stroke-width="0.9" '
          f'stroke-linejoin="round"/>')
    a(f'<path d="{land_d}" fill="none" stroke="{PAPER["coast"]}" stroke-width="0.55" stroke-linejoin="round"/>')

    # routes
    for rid, r in board["routes"].items():
        closed = bool(r.get("opens"))
        a(f'<path d="{r["d"]}" fill="none" stroke="{PAPER["route"]}" stroke-width="1.4" '
          f'stroke-dasharray="{"2 3" if closed else "6 3"}" stroke-linecap="round" '
          f'stroke-opacity="{0.35 if closed else 0.75}"/>')
        for e1, e2 in r.get("seam_points", []):
            for (x, y), dirn in ((e1, -1 if e1[0] < W / 2 else 1), (e2, -1 if e2[0] < W / 2 else 1)):
                a(_chevron(x, y, dirn, PAPER["route"]))
    # wrap marks
    for wm in board.get("wrap_marks", []):
        (lx, ly), (rx, ry) = wm["at"]
        a(_chevron(lx + 6, ly, -1, PAPER["sea_label"]))
        a(_chevron(rx - 6, ry, 1, PAPER["sea_label"]))

    # chokepoint icon + centres
    for p in seas:
        if P[p]["chokepoint"]:
            x, y = board["provinces"][p]["label"]
            a(f'<g transform="translate({x - 14:.1f} {y - 2.5:.1f})"><circle r="3.6" fill="#FFFFFF" '
              f'stroke="{PAPER["sea_label"]}" stroke-width="0.9"/><path d="M-1.8 -1.6 V1.6 M1.8 -1.6 V1.6" '
              f'stroke="{PAPER["sea_label"]}" stroke-width="1"/></g>')
    for p in lands:
        b = board["provinces"][p]
        if b["centre"] and P[p]["centre"] != "none":
            x, y = b["centre"]
            if P[p]["centre"] == "home":
                c = POWER_COLOURS[P[p]["home_of"]]
                a(f'<circle cx="{x}" cy="{y}" r="2.8" fill="{c}" stroke="#FFFFFF" stroke-width="1"/>')
            else:
                a(f'<circle cx="{x}" cy="{y}" r="2.6" fill="#FFFFFF" stroke="#8C7B5E" stroke-width="1"/>')

    # labels
    for p in seas:
        b = board["provinces"][p]
        size = 6.4 * min(1.25, b["label_size"])
        for (x, y) in [b["label"]] + b.get("labels_extra", []):
            a(f'<text x="{x}" y="{y + 2:.1f}" font-size="{size:.1f}" font-style="italic" letter-spacing="0.4" '
              f'fill="{PAPER["sea_label"]}" text-anchor="middle" paint-order="stroke" stroke="{PAPER["ocean"]}" '
              f'stroke-width="2" stroke-opacity="0.8">{escape(P[p]["name"].upper())}</text>')
    for p in lands:
        b = board["provinces"][p]
        x, y = b["label"]
        size = 7.2 * b["label_size"]
        col = PAPER["label"]
        a(f'<text x="{x}" y="{y + 2.4:.1f}" font-size="{size:.1f}" font-weight="600" fill="{col}" '
          f'text-anchor="middle" paint-order="stroke" stroke="#FFFFFF" stroke-width="2.2" stroke-opacity="0.85" '
          f'stroke-linejoin="round">{escape(P[p]["name"])}</text>')

    # starting units
    for u in m["start"]["units"]:
        b = board["provinces"][u["at"]]
        slot = "military" if u["type"] in ("A", "F") else "civil"
        x, y = b["slots"].get(slot) or b["slots"]["military"]
        a(_token(u["type"], x, y, POWER_COLOURS[u["power"]]))
    a("</svg>")
    path.write_text("\n".join(out))


def _chevron(x, y, dirn, colour):
    # small double chevron pointing off the board edge (dirn -1 = left, 1 = right)
    s = 3.2
    d = ""
    for k in (0, 1):
        ox = x + dirn * k * 3.4
        d += f"M{ox - dirn * s:.1f} {y - s:.1f}L{ox:.1f} {y:.1f}L{ox - dirn * s:.1f} {y + s:.1f}"
    return f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.1" stroke-linecap="round"/>'


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def svg_to_png(svg: Path, png: Path, w: float, h: float) -> Path | None:
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(int(w * 1.5)), "-o", str(png), str(svg)], check=True)
        return png
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=int(w * 1.5))
        return png
    except Exception:
        pass
    chrome = CHROME if Path(CHROME).exists() else (shutil.which("google-chrome") or shutil.which("chromium"))
    if not chrome:
        return None
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / "p.html"
        html.write_text(f'<html><body style="margin:0"><img src="{svg.resolve().as_uri()}" '
                        f'style="width:{w + 20:.0f}px;height:{h + 20:.0f}px;display:block"></body></html>')
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={td}/u",
                        "--force-device-scale-factor=1.5", "--allow-file-access-from-files",
                        f"--window-size={int(w + 20)},{int(h + 20)}", f"--screenshot={png.resolve()}",
                        html.as_uri()], check=True, capture_output=True, timeout=120)
    return png if png.exists() else None
