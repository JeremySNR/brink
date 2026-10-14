"""Brink's emails, printed in the Wargame Box system (DESIGN.md): a paper sheet framed in ink, the red-box
logotype, the turn record track, a player-aid card with the player's die-cut counter, the Crisis track.

Email-safe: tables for layout, inline styles, explicit backgrounds on every cell (dark-mode clients that
invert only what has no colour leave the sheet alone), no images, no scripts. Barlow Condensed and Archivo
load from Google Fonts where the client allows it; every size and width is chosen so the design survives
Arial Narrow / Helvetica / Arial.

Rendering is pure: `turn_email(ctx)` takes the context dict that brink.hosted.email builds from the game
(see `TurnContext` there for the fields) and returns (subject, html, text). `auth_email(kind)` renders the
Supabase Auth templates (Go-template placeholders, pasted into the Supabase dashboard) and
`unsubscribe_page(...)` the confirmation page the stop link lands on.
"""
from __future__ import annotations

from html import escape

# ---------------------------------------------------------------------------------------------- tokens
PAPER = "#f2eee2"
PAPER_2 = "#e8e2d1"
PAPER_HI = "#fbf9f3"
INK = "#1d1d1b"
INK_2 = "#47433b"
INK_3 = "#635d52"
RULE_SOFT = "#a49e90"
EDGE_INK = "#000000"
RED = "#b3261e"
RED_DEEP = "#6e1510"
RED_TINT = "#ebc9c3"
RED_TINT_2 = "#f3e0dc"
BUFF = "#e4dcc2"
PAST = "#ddd6c2"          # flat stand-in for the hatched past half where gradients are stripped

POWER_STOCK = {"USA": "#8FAFC6", "CHN": "#D9776A", "EU": "#E6CB5A", "RUS": "#B0A794", "IND": "#E7A35A",
               "JPN": "#F4F1E8", "GLF": "#84A56C"}
NO_POWER = "#C9C4B6"

COND = "'Barlow Condensed','Arial Narrow','Helvetica Neue Condensed','Roboto Condensed',Arial,sans-serif"
BODY = "Archivo,'Helvetica Neue',Helvetica,Arial,sans-serif"
FONTS_URL = ("https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800"
             "&family=Barlow+Condensed:wght@500;600;700&display=swap")

# rulebook section numbers printed in red on the aid heads (DESIGN.md, "The player-aid column")
SECTION = {"orders": 5, "retreats": 6, "propose": 11, "winter": 12, "resolution": 6, "crisis": 10, "centres": 12}


def mix(hex_colour: str, amount: float, base: str = "#FFFFFF") -> str:
    a = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def stock(power: str | None) -> str:
    return POWER_STOCK.get(power or "", NO_POWER)


def edge(power: str | None) -> str:
    """The counter's die-cut edge (web/src/theme.ts edgeColour): the stock mixed halfway to ink."""
    if power == "JPN":
        return "#3a3833"
    return mix(stock(power), 0.5, INK)


def e(s) -> str:
    return escape(str(s), quote=True)


# ---------------------------------------------------------------------------------------------- pieces
def _font(family: str, size: float, weight: int = 400, spacing: str = "0", extra: str = "") -> str:
    return (f"font-family:{family};font-size:{size}px;font-weight:{weight};letter-spacing:{spacing};"
            f"mso-line-height-rule:exactly;{extra}")


def logotype(size: int = 20) -> str:
    """BRINK with the B reversed out of a red box (Archivo 800, 0.18em)."""
    box = size + 8
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">'
        f'<tr><td width="{box}" height="{box}" bgcolor="{RED}" align="center" valign="middle" '
        f'style="width:{box}px;height:{box}px;background:{RED};color:{PAPER_HI};'
        f'{_font(BODY, size, 800, "0", f"line-height:{box}px;")}">B</td>'
        f'<td valign="middle" style="padding:0 0 0 {round(size * 0.18)}px;color:{INK};'
        f'{_font(BODY, size, 800, "0.18em", f"line-height:{box}px;")}">RINK</td></tr></table>')


def counter(power: str, size: int = 46, depth: int = 4, bg: str = PAPER_HI) -> str:
    """A die-cut counter: the face in the power's stock, an ink stroke, the code printed on it, and the
    counter's thickness as a solid offset edge at the bottom-right (a second square, not a shadow)."""
    code_size = round(size * 0.36)
    ed = edge(power)
    sp = "font-size:0;line-height:0;"
    face = (f'<td width="{size}" height="{size}" bgcolor="{stock(power)}" align="center" valign="middle" '
            f'style="width:{size - 2}px;height:{size - 2}px;border:1px solid {INK};background:{stock(power)};'
            f'color:{INK};{_font(COND, code_size, 700, "0.04em", f"line-height:{size - 2}px;")}">{e(power)}</td>')
    right = (f'<td width="{depth}" valign="top" bgcolor="{bg}" style="width:{depth}px;background:{bg};{sp}">'
             f'<div style="height:{depth}px;background:{bg};{sp}">&nbsp;</div>'
             f'<div style="height:{size - depth}px;background:{ed};{sp}">&nbsp;</div></td>')
    bottom = (f'<tr><td height="{depth}" bgcolor="{bg}" style="height:{depth}px;background:{bg};{sp}">'
              f'<div style="height:{depth}px;margin-left:{depth}px;background:{ed};{sp}">&nbsp;</div></td>'
              f'<td width="{depth}" height="{depth}" bgcolor="{ed}" style="width:{depth}px;height:{depth}px;'
              f'background:{ed};{sp}">&nbsp;</td></tr>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">'
            f'<tr>{face}{right}</tr>{bottom}</table>')


def turn_track(track: dict) -> str:
    """The turn record track: joined year boxes, Spring and Fall halves split by a dashed rule, a narrow
    Winter cell behind a solid rule; past halves hatched, the live half bright with the game-turn counter
    (red: it is the player's move) and a red underline."""
    years = track["years"]
    width = f"{100 / len(years):.2f}%"
    cells = []
    for i, y in enumerate(years):
        halves = []
        for h in y["halves"]:
            is_w = h["season"] == "W"
            now = h["state"] == "now"
            past = h["state"] == "past"
            bg = PAPER_HI if now else (PAST if past else PAPER)
            hatch = ("background-image:repeating-linear-gradient(135deg,rgba(29,29,27,0.2) 0,"
                     "rgba(29,29,27,0.2) 1px,rgba(0,0,0,0) 1px,rgba(0,0,0,0) 5px);") if past else ""
            border_l = (f"border-left:1.5px solid {INK};" if is_w else
                        (f"border-left:1px dashed {RULE_SOFT};" if h["season"] == "F" else ""))
            under = f"border-bottom:3px solid {RED};" if now else f"border-bottom:3px solid {bg};"
            w_now = any(x["state"] == "now" and x["season"] == "W" for x in y["halves"])
            w = ("34%" if now else "12%") if is_w else ("33%" if w_now else "44%")
            label = {"S": "SPR", "F": "FALL", "W": "W"}[h["season"]]
            if now:
                inner = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
                         f'style="border-collapse:collapse"><tr><td bgcolor="{RED}" align="center" '
                         f'style="background:{RED};color:{PAPER_HI};padding:{"2px 2px 1px" if is_w else "2px 4px 1px"};border-right:2px solid {RED_DEEP};'
                         f'border-bottom:2px solid {RED_DEEP};{_font(COND, 10 if is_w else 11, 700, "0.04em", "line-height:13px;")}">'
                         f'NOW</td></tr></table>')
            else:
                inner = (f'<span style="color:{INK_3};{_font(COND, 9.5, 600, "0.06em", "line-height:12px;")}">'
                         f'{label}</span>')
            halves.append(
                f'<td width="{w}" bgcolor="{bg}" valign="bottom" align="{"center" if now else "left"}" '
                f'style="width:{w};height:24px;padding:3px 3px 2px;background-color:{bg};{hatch}{border_l}{under}">'
                f'{inner}</td>')
        end = (f'<td align="right" style="padding:3px 5px 0 0;color:{INK};'
               f'{_font(COND, 10, 700, "0.06em", "line-height:12px;")}">END</td>') if y["end"] else ""
        hide = ' class="hide-s"' if i == 4 and not any(h["state"] == "now" for h in y["halves"]) else ""
        cells.append(
            f'<td{hide} width="{width}" valign="top" bgcolor="{PAPER}" style="width:{width};padding:0;background:{PAPER};'
            f'border:1.5px solid {INK};{"border-right-width:4px;" if y["end"] else ""}">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse"><tr><td style="padding:3px 0 0 5px;color:{INK};'
            f'{_font(COND, 12, 700, "0.04em", "line-height:13px;")}">{y["year"]}</td>{end}</tr></table>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;table-layout:fixed"><tr>{"".join(halves)}</tr></table></td>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;table-layout:fixed"><tr>{"".join(cells)}</tr></table>')


def crisis_track(level: int) -> str:
    """Eleven joined boxes, 0 to 10: 6-7 and 8-9 in the red tints, 10 solid red, heavier rules before each
    threshold; the marker is a die-cut counter in the current box (red from 6, ink again on 10)."""
    cells = []
    for i in range(11):
        bg = RED if i == 10 else RED_TINT if i >= 8 else RED_TINT_2 if i >= 6 else PAPER_HI
        fg = PAPER_HI if i == 10 else INK
        left = "3px" if i in (6, 8, 10) else "1.5px"
        if i == level:
            face, lip = (RED, RED_DEEP) if 6 <= i < 10 else (INK, EDGE_INK)
            inner = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
                     f'style="border-collapse:collapse"><tr><td bgcolor="{face}" align="center" '
                     f'style="background:{face};color:{PAPER_HI};width:20px;padding:2px 0 1px;'
                     f'border-right:2px solid {lip};border-bottom:2px solid {lip};'
                     f'{_font(COND, 14, 700, "0", "line-height:16px;")}">{i}</td></tr></table>')
        else:
            inner = f'{i}'
        cells.append(
            f'<td width="9.09%" height="34" bgcolor="{bg}" align="center" valign="middle" '
            f'style="width:9.09%;height:34px;padding:0;background:{bg};color:{fg};border:1.5px solid {INK};'
            f'border-left-width:{left};{_font(COND, 14, 600, "0", "line-height:16px;")}">{inner}</td>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;table-layout:fixed"><tr>{"".join(cells)}</tr></table>')


def crisis_key(level: int) -> str:
    items = [(6, "Recession", "builds &minus;1"), (8, "State failure", ""), (10, "Collapse", "all lose")]
    out = []
    for n, name, note in items:
        colour = RED if level >= n else INK_2
        out.append(f'<span style="color:{colour};white-space:nowrap"><b style="{_font(COND, 13, 700)}">{n}</b>'
                   f'&nbsp;{name}{f" ({note})" if note else ""}</span>')
    return " &nbsp;&middot;&nbsp; ".join(out)


def button(label: str, href: str) -> str:
    """The Commit button: spot red, a red-deep frame with a 4px bottom lip, condensed caps."""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:separate"><tr><td bgcolor="{RED}" align="center" height="52" '
        f'style="background:{RED};border:2px solid {RED_DEEP};border-bottom-width:4px;">'
        f'<a href="{e(href)}" target="_blank" style="display:block;padding:15px 16px 13px;color:{PAPER_HI};'
        f'text-decoration:none;text-transform:uppercase;{_font(COND, 20, 700, "0.1em", "line-height:22px;")}">'
        f'{e(label)}</a></td></tr></table>')


def section_head(number: int | None, title: str, aside: str = "") -> str:
    num = (f'<span style="color:{RED}">{number}</span>&nbsp;&nbsp;' if number is not None else "")
    right = (f'<td align="right" valign="bottom" style="color:{INK_2};{_font(BODY, 12.5, 400, "0", "line-height:16px;")}">'
             f'{aside}</td>') if aside else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse"><tr><td valign="bottom" style="color:{INK};text-transform:uppercase;'
            f'{_font(COND, 19, 700, "0.04em", "line-height:22px;")}">{num}{e(title)}</td>{right}</tr></table>')


def row(key: str, value_html: str, first: bool = False) -> str:
    """A player-aid row: a 96px condensed-caps key and the value, dotted soft rules between rows."""
    rule = "" if first else f"border-top:1px dotted {RULE_SOFT};"
    return (f'<tr><td width="96" valign="top" class="k" style="width:96px;padding:9px 10px 9px 0;{rule}color:{INK_2};'
            f'text-transform:uppercase;{_font(COND, 13, 600, "0.06em", "line-height:19px;")}">{e(key)}</td>'
            f'<td valign="top" style="padding:9px 0;{rule}color:{INK};{_font(BODY, 14, 400, "0", "line-height:20px;")}">'
            f'{value_html}</td></tr>')


def _section(inner: str, bg: str = PAPER, rule: str = f"2px solid {INK}", pad: str = "14px 22px 16px") -> str:
    return (f'<tr><td bgcolor="{bg}" class="sec" style="padding:{pad};background:{bg};border-bottom:{rule};">'
            f'{inner}</td></tr>')


def _rows(inner: str) -> str:
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;margin-top:6px">{inner}</table>')


HEAD_STYLE = f"""
  :root {{ color-scheme: light only; supported-color-schemes: light only; }}
  body {{ margin:0; padding:0; background:{PAPER}; }}
  a {{ color:{INK}; }}
  @media (max-width: 480px) {{
    .sheet-pad {{ padding: 12px 8px 24px !important; }}
    .sec {{ padding-left: 16px !important; padding-right: 16px !important; }}
    .k {{ width: 78px !important; }}
    .display {{ font-size: 28px !important; line-height: 29px !important; }}
    .hide-s {{ display: none !important; }}
  }}
"""


def _document(title: str, preheader: str, body_rows: str, footer: str) -> str:
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;'
           f'color:{PAPER};opacity:0">{e(preheader)}{"&#8199;&#65279;&#847; " * 30}</div>') if preheader else ""
    return f"""<!doctype html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{e(title)}</title>
<link href="{FONTS_URL}" rel="stylesheet">
<style>{HEAD_STYLE}</style>
</head>
<body bgcolor="{PAPER}" style="margin:0;padding:0;background:{PAPER};">
{pre}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="background:{PAPER};border-collapse:collapse">
<tr><td align="center" class="sheet-pad" style="padding:24px 12px 32px;background:{PAPER}">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" style="width:100%;max-width:600px;border-collapse:collapse;background:{PAPER};border:2px solid {INK};">
{body_rows}
</table>
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;border-collapse:collapse">
<tr><td style="padding:16px 2px 0;color:{INK_3};{_font(BODY, 12, 400, "0", "line-height:18px;")}">{footer}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def _top_bar(right_html: str) -> str:
    return (f'<tr><td bgcolor="{PAPER}" style="padding:14px 22px 13px;background:{PAPER};border-bottom:3px solid {INK};">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse"><tr><td valign="middle">{logotype(19)}</td>'
            f'<td align="right" valign="middle" style="color:{INK_2};text-transform:uppercase;'
            f'{_font(COND, 15, 600, "0.06em", "line-height:18px;")}">{right_html}</td></tr></table></td></tr>')


def _link(href: str, label: str, colour: str = INK_2) -> str:
    return (f'<a href="{e(href)}" target="_blank" style="color:{colour};text-decoration:underline;'
            f'text-underline-offset:2px">{e(label)}</a>')


# ---------------------------------------------------------------------------------------------- turn emails
def subject(ctx: dict) -> str:
    what = {"orders": "orders", "retreats": "retreats", "propose": "Assembly proposal",
            "winter": "decisions"}[ctx["decision"]]
    if ctx["kind"] == "reminder":
        return f"{ctx['game_name']}: {ctx['left']} left for your {ctx['phase_label']} {what}"
    return f"{ctx['game_name']}: {ctx['phase_label']} {what} due by {ctx['deadline_short']}"


def headline(ctx: dict) -> tuple[str, str]:
    """(display line, second line), both set in condensed caps."""
    if ctx["kind"] == "reminder":
        return ctx["phase_title"], f"{ctx['left']} left"
    second = {"orders": "Your orders are due", "retreats": "Your retreats are due",
              "propose": "Your proposal is due", "winter": "Your decisions are due"}[ctx["decision"]]
    return ctx["phase_title"], second


def turn_email(ctx: dict) -> tuple[str, str, str]:
    return subject(ctx), turn_html(ctx), turn_text(ctx)


def turn_html(ctx: dict) -> str:
    display, second = headline(ctx)
    power = ctx["power"]
    tag = "Not ready yet" if ctx["kind"] == "reminder" else "Your move"

    # phase head: display title, then the second line with the red "your move" tag
    head = (
        f'<tr><td bgcolor="{PAPER}" class="sec" style="padding:20px 22px 16px;background:{PAPER};'
        f'border-bottom:2px solid {INK};">'
        f'<div style="color:{RED};text-transform:uppercase;{_font(COND, 15, 700, "0.06em", "line-height:18px;")}">'
        f'{e(tag)}</div>'
        f'<h1 class="display" style="margin:4px 0 0;color:{INK};text-transform:uppercase;'
        f'{_font(COND, 36, 700, "0.02em", "line-height:36px;")}">{e(display)}:<br>{e(second)}</h1>'
        f'<p style="margin:10px 0 0;color:{INK_2};{_font(BODY, 14, 400, "0", "line-height:20px;")}">'
        f'{e(ctx["lede"])}</p></td></tr>')

    # the decision card: counter + power + game, what's needed, the deadline box, the commit button
    who = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">'
           f'<tr><td valign="middle" style="padding:0 14px 0 0">{counter(power, 46, 4, PAPER_HI)}</td>'
           f'<td valign="middle"><div style="color:{RED};text-transform:uppercase;'
           f'{_font(COND, 19, 700, "0.05em", "line-height:21px;")}">{e(ctx["power_name"])}</div>'
           f'<div style="color:{INK_2};{_font(BODY, 13, 400, "0", "line-height:18px;")}">'
           f'{e(ctx["seat_line"])}</div></td></tr></table>')
    deadline_box = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}" '
        f'style="border-collapse:collapse;border:2px solid {INK};background:{PAPER}"><tr>'
        f'<td valign="middle" style="padding:5px 11px 4px;border-right:1.5px solid {INK};color:{INK};white-space:nowrap;'
        f'{_font(COND, 20, 700, "0.02em", "line-height:22px;")}">{e(ctx["deadline_rel_short"])}</td>'
        f'<td valign="middle" style="padding:5px 11px 4px;color:{INK};'
        f'{_font(BODY, 13, 400, "0", "line-height:17px;")}">{e(ctx["deadline_abs"])}</td></tr></table>')
    need = e(ctx["need"])
    if ctx.get("need_note"):
        need += (f'<div style="margin-top:3px;color:{INK_2};{_font(BODY, 12.5, 400, "0", "line-height:18px;")}">'
                 f'{e(ctx["need_note"])}</div>')
    card_rows = (row("Power", who, first=True) + row("Needed", need) + row("Deadline", deadline_box))
    card = (section_head(SECTION[ctx["decision"]], ctx["card_title"]) + _rows(card_rows)
            + f'<div style="height:14px;line-height:14px;font-size:0">&nbsp;</div>'
            + button(ctx["cta"], ctx["game_url"])
            + f'<p style="margin:10px 0 0;color:{INK_2};{_font(BODY, 12.5, 400, "0", "line-height:18px;")}">'
              f'{e(ctx["rule_note"])}</p>')

    # what happened, then the Crisis track and the centres
    happened = "".join(
        f'<p style="margin:{"8px" if i else "8px"} 0 0;color:{INK};{_font(BODY, 14, 400, "0", "line-height:21px;")}">'
        f'{e(s)}</p>' for i, s in enumerate(ctx["happened"]))
    happened_sec = (section_head(SECTION["resolution"] if ctx["happened_winter"] is False else SECTION["winter"],
                                 "What happened", e(ctx["happened_label"])) + happened) if ctx["happened"] else ""
    crisis = (section_head(SECTION["crisis"], "Crisis track", f'{ctx["crisis"]} of 10')
              + f'<div style="height:10px;line-height:10px;font-size:0">&nbsp;</div>' + crisis_track(ctx["crisis"])
              + f'<p style="margin:8px 0 0;color:{INK_2};{_font(BODY, 12.5, 400, "0", "line-height:18px;")}">'
                f'{crisis_key(ctx["crisis"])}</p>')
    centres = (section_head(SECTION["centres"], "Your position") + _rows(
        row("Centres", f'<b style="{_font(COND, 17, 700, "0", "line-height:19px;")}">{ctx["centres"]}</b>'
                       f'&nbsp; {e(ctx["centres_note"])}', first=True)
        + row("Units", f'<b style="{_font(COND, 17, 700, "0", "line-height:19px;")}">{ctx["units"]}</b>'
                       f'&nbsp; {e(ctx["units_note"])}')))

    track = (
        f'<tr><td bgcolor="{BUFF}" class="sec" style="padding:10px 22px 12px;background:{BUFF};border-bottom:2px solid {INK};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">'
        f'<tr><td style="padding:0 0 6px;color:{INK};text-transform:uppercase;'
        f'{_font(COND, 13, 700, "0.08em", "line-height:15px;")}">Turn record</td>'
        f'<td align="right" style="padding:0 0 6px;color:{INK_2};text-transform:uppercase;'
        f'{_font(COND, 13, 600, "0.06em", "line-height:15px;")}">{e(ctx["track"]["caption"])}</td></tr></table>'
        f'{turn_track(ctx["track"])}</td></tr>')

    rows = (_top_bar(e(ctx["game_name"])) + track + head
            + _section(card, bg=PAPER_HI, rule=f"3px solid {INK}")
            + (_section(happened_sec) if happened_sec else "")
            + _section(crisis) + _section(centres, rule="0"))
    footer = (f'You are getting this because you play {e(ctx["power_name"])} in {e(ctx["game_name"])}. '
              f'Brink emails you when a phase needs your decision, and once more when its deadline is close '
              f'and you haven&rsquo;t marked Ready.<br>'
              f'{_link(ctx["unsubscribe_url"], "Stop turn emails")} &nbsp;&middot;&nbsp; '
              f'{_link(ctx["game_url"], "Open the game")} &nbsp;&middot;&nbsp; '
              f'{_link(ctx["site_url"], ctx["site_host"])}')
    return _document(subject(ctx), ctx["preheader"], rows, footer)


def turn_text(ctx: dict) -> str:
    display, second = headline(ctx)
    lines = [f"BRINK · {ctx['game_name']}", "", f"{display}: {second}".upper(), ctx["lede"], "",
             f"{ctx['power_name']} ({ctx['power']})  {ctx['seat_line']}",
             f"Needed:    {ctx['need']}"]
    if ctx.get("need_note"):
        lines.append(f"           {ctx['need_note']}")
    lines += [f"Deadline:  {ctx['deadline_abs']} ({ctx['deadline_rel']})", "",
              f"{ctx['cta']}: {ctx['game_url']}", "", ctx["rule_note"], ""]
    if ctx["happened"]:
        lines += [f"WHAT HAPPENED, {ctx['happened_label'].upper()}"] + ctx["happened"] + [""]
    boxes = " ".join(f"[{i}]" if i == ctx["crisis"] else str(i) for i in range(11))
    lines += [f"CRISIS TRACK  {boxes}",
              "6 Recession (builds -1) · 8 State failure · 10 Collapse (all lose)", "",
              f"Your centres: {ctx['centres']} {ctx['centres_note']}".rstrip(),
              f"Your units:   {ctx['units']} {ctx['units_note']}".rstrip(), "",
              "--",
              f"You are getting this because you play {ctx['power_name']} in {ctx['game_name']}. Brink emails you "
              "when a phase needs your decision, and once more when its deadline is close and you haven't marked "
              "Ready.",
              f"Stop turn emails: {ctx['unsubscribe_url']}",
              f"Open the game: {ctx['game_url']}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- auth emails
AUTH = {
    "magic_link": {
        "subject": "Your Brink sign-in link",
        "title": "Sign in to Brink",
        "lede": "Use this link to sign in as {{ .Email }}. It works once, and only for a short while.",
        "cta": "Sign in",
        "note": "If you didn't ask to sign in, you can ignore this email: nobody can use it without the link.",
    },
    "confirm_signup": {
        "subject": "Confirm your email for Brink",
        "title": "Confirm your email",
        "lede": "Confirm {{ .Email }} to finish signing up. Then you can join a game or start your own.",
        "cta": "Confirm and sign in",
        "note": "If you didn't sign up for Brink, you can ignore this email.",
    },
}


def auth_email(kind: str) -> tuple[str, str]:
    """(subject, html) for a Supabase Auth template, with Go-template placeholders left in."""
    a = AUTH[kind]
    url = "{{ .ConfirmationURL }}"
    body = (
        f'<tr><td bgcolor="{PAPER}" class="sec" style="padding:26px 22px 22px;background:{PAPER};">'
        f'<h1 class="display" style="margin:0;color:{INK};text-transform:uppercase;'
        f'{_font(COND, 36, 700, "0.02em", "line-height:36px;")}">{e(a["title"])}</h1>'
        f'<p style="margin:12px 0 18px;color:{INK};{_font(BODY, 14, 400, "0", "line-height:21px;")}">{a["lede"]}</p>'
        f'{button(a["cta"], url).replace(e(url), url)}'
        f'<p style="margin:14px 0 0;color:{INK_2};{_font(BODY, 12.5, 400, "0", "line-height:18px;")}">'
        f'Or paste this address into your browser:<br>'
        f'<a href="{url}" style="color:{INK_2};word-break:break-all">{url}</a></p></td></tr>')
    rows = _top_bar("") + body
    footer = f'{e(a["note"])}<br>{_link("{{ .SiteURL }}", "Brink")}'.replace(e("{{ .SiteURL }}"), "{{ .SiteURL }}")
    return a["subject"], _document(a["subject"], a["lede"].replace("{{ .Email }}", "your email"), rows, footer)


# ---------------------------------------------------------------------------------------------- unsubscribe page
def unsubscribe_page(state: str, form_action: str = "", site_url: str = "/") -> str:
    """The page the stop link opens. state: 'stopped' | 'resumed' | 'invalid'."""
    if state == "stopped":
        title, text = "Turn emails stopped", (
            "You won't get emails when your orders are due or a deadline is close. Your games carry on as "
            "before; you can turn the emails back on here or on your account page.")
    elif state == "resumed":
        title, text = "Turn emails are on", (
            "Brink will email you when a phase needs your decision, and once more when its deadline is close.")
    else:
        title, text = "This link doesn't work", (
            "It may have been cut short by your email program. Turn emails on or off from your account page.")
    action = ""
    if state in ("stopped", "resumed") and form_action:
        label = "Turn them back on" if state == "stopped" else "Stop them again"
        value = "resubscribe" if state == "stopped" else "unsubscribe"
        action = (f'<form method="post" action="{e(form_action)}" style="margin:18px 0 0">'
                  f'<input type="hidden" name="action" value="{value}">'
                  f'<button type="submit" style="height:38px;padding:0 16px;background:{PAPER_HI};color:{INK};'
                  f'border:1.5px solid {INK};border-radius:0;cursor:pointer;text-transform:uppercase;'
                  f'{_font(COND, 15, 700, "0.06em")}">{label}</button></form>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(title)} · Brink</title>
<link href="{FONTS_URL}" rel="stylesheet">
<style>
  body {{ margin:0; background:{PAPER}; color:{INK}; }}
  .sheet {{ width:min(520px, calc(100% - 32px)); margin:48px auto; border:2px solid {INK}; background:{PAPER}; }}
  .top {{ padding:14px 20px 13px; border-bottom:3px solid {INK}; }}
  .body {{ padding:22px 20px 24px; }}
  h1 {{ margin:0; text-transform:uppercase; {_font(COND, 34, 700, "0.02em", "line-height:1;")} }}
  p {{ margin:12px 0 0; max-width:60ch; {_font(BODY, 14, 400, "0", "line-height:1.45;")} }}
  a {{ color:{INK}; font-weight:600; text-underline-offset:3px; }}
  button:hover {{ background:{PAPER_2} !important; }}
  button:focus-visible, a:focus-visible {{ outline:2px solid {INK}; outline-offset:2px; }}
</style></head>
<body><main class="sheet"><div class="top">{logotype(19)}</div>
<div class="body"><h1>{e(title)}</h1><p>{e(text)}</p>{action}
<p><a href="{e(site_url.rstrip('/'))}/#/me">Your account</a> &nbsp;&middot;&nbsp; <a href="{e(site_url.rstrip('/'))}/#/">Your games</a></p>
</div></main></body></html>
"""
