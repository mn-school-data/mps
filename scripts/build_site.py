"""Build the static website for the MPS finance analysis.

Reads the analysis and data under a district folder (default: ../mps next to
this script) and writes a self-contained static site (HTML, CSS, JS, CSVs;
relative links only, no external requests), so it opens straight from disk or
from any web host and subpath.

Usage: python3 scripts/build_site.py [--src mps] [--out _site]
Standard library only, so it runs anywhere Python 3.9+ does.

The site is meant to be anonymous: every page and data file passes through
sanitize(), and the build fails if any text in BLOCKLIST survives. Extra
patterns can come from a JSON file ({"scrub": [[pattern, replacement], ...],
"block": [pattern, ...]}) given with --scrub; it is kept out of any public copy.
"""
import argparse
import csv
import tempfile
import datetime
import html
import io
import json
import math
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE_TITLE = "Minneapolis Public Schools Analysis"

# ---------------------------------------------------------------- sanitizing
# Ordered (pattern, replacement) pairs applied to every text that reaches the
# site. They turn working notes into neutral public prose.
SCRUB = [
    (r"text of PDF uploaded by \w+ [0-9-]+;\s*", ""),
    (r"\s*\(uploaded by \w+\)", ""),
    (r"\bA separate extraction by another thread\b", "A second, independent extraction"),
    (r"\bby another thread\b", "independently"),
    (r"\*\*Public, but this environment could not open it:\*\*",
     "**Public, but not yet retrieved:**"),
    (r"is blocked by this environment's network policy", "could not be retrieved"),
    (r"\s*\(blocked by this environment's network policy\)", " (could not be retrieved)"),
    (r"\*\*BoardBook attachments are now readable\*\* through `[^`]*`; [^.]*\(a headless browser lists them\)\.",
     "**BoardBook attachments** (board meeting documents) have now been read."),
    (r"\(education\.mn\.gov blocks automated access\)", "(not yet retrieved)"),
    (r"are also blocked here now, so press figures were not re-checked",
     "could not be re-checked, so press figures stand as reported"),
    (r"\s*Opening them in a browser, or allowing [\w.]+, would fill", " They would fill"),
    (r"\s*\(tagged NEWS\)", ""),
    (r", because [^.]*blocked from the research environment\.", "."),
    (r"\s*\((?:read via search summaries; )?site blocked from the research container\)", " (read via search summaries)"),
    (r"\s*\(blocked from container\)", ""),
    (r"couldn't be read here \([^)]*blocked\)\. Opening those in a browser would confirm",
     "have not been checked against primary text. Reading them would confirm"),
    (r"this environment", "this project"),
    (r"Step \d of the project:[^.]*\.\s*", ""),
]
# Hard stop: any of these left in the output fails the build.
BLOCKLIST = [r"github\.com", r"claude", r"/mnt/", r"anthropic", r"uploaded by"]
# Soft check: printed as warnings so working-note language can be reworded.
WARNLIST = [r"\bthread\b", r"\bcontainer\b", r"this environment"]


def load_scrub(path):
    """Append private scrub rules (run after the generic ones) and block patterns."""
    extra = json.loads(Path(path).read_text())
    SCRUB.extend(tuple(r) for r in extra.get("scrub", []))
    BLOCKLIST.extend(extra.get("block", []))


def sanitize(text):
    for pat, rep in SCRUB:
        text = re.sub(pat, rep, text)
    return text


def check(name, text):
    for pat in BLOCKLIST:
        m = re.search(pat, text, re.I)
        if m:
            ctx = text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ")
            sys.exit(f"build_site: identifying text /{pat}/ in {name}: ...{ctx}...")
    for pat in WARNLIST:
        for m in re.finditer(pat, text, re.I):
            ctx = text[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
            print(f"warning: /{pat}/ in {name}: ...{ctx}...", file=sys.stderr)


# ---------------------------------------------------------------- markdown
def inline(s, links):
    """Inline markdown to HTML. `links` maps local paths to site hrefs."""
    out, i = [], 0
    code = re.compile(r"`([^`]+)`")
    parts = code.split(s)
    for n, part in enumerate(parts):
        if n % 2:  # inside backticks: local file paths become links
            href = links(part)
            t = html.escape(part)
            if href and href.startswith("sources.html"):  # working notes are not published
                out.append(f'<a href="{href}">source list</a>')
            else:
                out.append(f'<a href="{href}"><code>{t}</code></a>' if href else f"<code>{t}</code>")
            continue
        t = html.escape(part, quote=False)
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
        t = re.sub(r"\[([^\]]+)\]\(([a-z0-9_-]+\.html(?:#[a-z0-9-]+)?)\)", r'<a href="\2">\1</a>', t)
        t = re.sub(r"(?<![\"=>])(https?://[^\s<>)\]]+[^\s<>)\].,;:])",
                   lambda m: f'<a href="{m.group(1)}">{short_url(m.group(1))}</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", t)
        out.append(t)
    return "".join(out)


def short_url(u):
    m = re.match(r"https?://(?:www\.)?([^/]+)(/.*)?", u)
    host, path = m.group(1), (m.group(2) or "")
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if not name or len(path) < 30:
        return html.escape(host + path)
    return html.escape(f"{host}/…/{name}")


def md_to_html(md, links=lambda p: None):
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre>{html.escape(chr(10).join(code))}</pre>")
            continue
        m = re.match(r"(#{1,6})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl} id=\"{slug(m.group(2))}\">{inline(m.group(2), links)}</h{lvl}>")
            i += 1
            continue
        if ln.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            num = [all(re.fullmatch(r"[−\-$0-9.,%() ]+[A-Za-z ]*|–", r[k]) for r in body if k < len(r))
                   for k in range(len(head))]
            t = ["<div class=\"table-wrap\"><table><thead><tr>"]
            t += [f"<th{' class=num' if num[k] and k else ''}>{inline(c, links)}</th>" for k, c in enumerate(head)]
            t.append("</tr></thead><tbody>")
            for r in body:
                t.append("<tr>" + "".join(f"<td{' class=num' if k < len(num) and num[k] and k else ''}>{inline(c, links)}</td>"
                                          for k, c in enumerate(r)) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        m = re.match(r"\s*(\d+\.|[-*])\s+(.*)", ln)
        if m:
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            items = []  # [indent, text]; one level of nesting is rendered
            while i < len(lines):
                m = re.match(r"(\s*)(\d+\.|[-*])\s+(.*)", lines[i])
                if m:
                    items.append([len(m.group(1)) > 0 and bool(items), m.group(3)])
                elif lines[i].strip() and lines[i].startswith(("  ", "\t")) and items:
                    items[-1][1] += " " + lines[i].strip()
                else:
                    break
                i += 1
            h = [f"<{tag}>"]
            for k, (sub, text) in enumerate(items):
                if sub and not items[k - 1][0]:
                    h[-1] = h[-1].removesuffix("</li>") + "<ul>"
                h.append(f"<li>{inline(text, links)}</li>")
                if sub and (k + 1 == len(items) or not items[k + 1][0]):
                    h.append("</ul></li>")
            out.append("".join(h) + f"</{tag}>")
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,6}\s|\s*\||\s*(\d+\.|[-*])\s|```)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        text = " ".join(para)
        if text.startswith("!["):
            continue
        out.append(f"<p>{inline(text, links)}</p>")
    return "\n".join(out)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>|\*", "", s).lower()).strip("-")


def cite_links(body, sources, prefix="src"):
    """Number the <li> items of a rendered source list and link [n] markers in `body` to them."""
    n = iter(range(1, 10_000))
    sources = re.sub(r"<li>", lambda m: f'<li id="{prefix}-{next(n)}">', sources)
    body = re.sub(r"\[(\d{1,3})\](?!\()", lambda m: f'<a class="cite" href="#{prefix}-{m.group(1)}">[{m.group(1)}]</a>', body)
    return body, sources


def sections(md):
    """Split markdown into (title, body) by level-2 headings; title '' = preamble."""
    out, title, buf = [], "", []
    for ln in md.split("\n"):
        m = re.match(r"##\s+(.*)", ln)
        if m and not ln.startswith("###"):
            out.append((title, "\n".join(buf)))
            title, buf = m.group(1).strip(), []
        else:
            buf.append(ln)
    out.append((title, "\n".join(buf)))
    return out


# ---------------------------------------------------------------- data
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_panel(src):
    rows = {}
    with open(src / "data" / "mps_panel.csv") as f:
        for r in csv.DictReader(f):
            fy = int(r["fiscal_year"])
            # the panel can carry several bases per year; keep the first (audited) row
            rows.setdefault(fy, {k: (v if k == "basis" else num(v)) for k, v in r.items()})
            rows[fy]["fiscal_year"] = fy
    return rows


def load_long(path):
    out = {}
    if not path.exists():
        return out
    with open(path) as f:
        for r in csv.DictReader(f):
            v = num(r.get("value"))
            if v is not None and (r.get("fiscal_year") or "").isdigit():
                out[(int(r["fiscal_year"]), r["metric"])] = v
    return out


# ---------------------------------------------------------------- charts
SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)"]


def nice_ticks(lo, hi, n=5):
    span = hi - lo or abs(hi) or 1
    raw = span / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    start = (lo // step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        ticks.append(round(t, 10))
        t += step
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


# Every chart is drawn twice: a wide version, and a narrow one for phones
# (taller, no end labels, every other year labelled). CSS shows one of them.
WIDE, NARROW_DIMS = (720, 300, 64, 118), (420, 330, 50, 14)
W, H, ML, MR = WIDE
MT, MB = 16, 34
NARROW = False


def svg_open(label):
    return (f'<svg class="chart {"narrow" if NARROW else "wide"}" viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(label)}" '
            f'preserveAspectRatio="xMidYMid meet">')


def y_axis(ticks, ys, fmt):
    g = []
    for t in ticks:
        y = ys(t)
        g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{ML}" x2="{W - MR}" y1="{y:.1f}" y2="{y:.1f}"/>')
        g.append(f'<text class="tick" x="{ML - 8}" y="{y + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
    return g


def x_labels(xs, xpos, every=1):
    # every other label gets class "minor" so narrow screens can drop it
    if NARROW and len(xs) > 6:
        every = max(every, 2, math.ceil(len(xs) / 7))
    return [f'<text class="tick" x="{xpos(x):.1f}" y="{H - MB + 18}" text-anchor="middle">{x}</text>'
            for k, x in enumerate(xs) if k % every == 0]


def _line_chart(label, series, fmt, tipfmt=None, y0=None, refline=None, every=1, ytitle=None, above=None, below=None, xname="FY"):
    """series: [{name, points: {x: y}, dashed_from: x or None}]
    ytitle: what the vertical axis counts, drawn above the plot; above/below: notes inside the plot
    saying what values above and below the reference line mean."""
    mt = MT + (22 if ytitle else 0)
    tipfmt = tipfmt or fmt
    xs = sorted({x for s in series for x in s["points"]})
    vals = [v for s in series for v in s["points"].values()] + ([refline[0]] if refline else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    lo = y0 if y0 is not None else lo - pad
    ticks = nice_ticks(lo, hi + pad)
    lo, hi = ticks[0], ticks[-1]
    xpos = lambda x: ML + (x - xs[0]) / max(1, xs[-1] - xs[0]) * (W - ML - MR)
    ys = lambda v: mt + (hi - v) / (hi - lo) * (H - mt - MB)
    g = [svg_open(label)] + y_axis(ticks, ys, fmt) + x_labels(xs, xpos, every)
    if ytitle:
        g.append(f'<text class="axis-title" x="4" y="{MT}">{html.escape(ytitle)}</text>')
    if refline and above:
        g.append(f'<text class="ref-label" x="{ML + 8}" y="{mt + 12}">\u2191 {html.escape(above)}</text>')
    if refline and below:
        g.append(f'<text class="ref-label" x="{ML + 8}" y="{H - MB - 8}">\u2193 {html.escape(below)}</text>')
    if refline:
        v, txt = refline
        g.append(f'<line class="ref" x1="{ML}" x2="{W - MR}" y1="{ys(v):.1f}" y2="{ys(v):.1f}"/>')
        g.append(f'<text class="ref-label" x="{ML + 6}" y="{ys(v) - 6:.1f}">{html.escape(txt)}</text>')
    ends = []
    for k, s in enumerate(series):
        col = s.get("color", SERIES[k])
        pts = sorted(s["points"].items())
        cut = s.get("dashed_from")
        solid = [p for p in pts if cut is None or p[0] <= cut]
        dash = [p for p in pts if cut is not None and p[0] >= cut]
        for seg, cls in ((solid, "line"), (dash, "line dashed")):
            if len(seg) > 1:
                d = " ".join(f"{'M' if j == 0 else 'L'}{xpos(x):.1f},{ys(y):.1f}" for j, (x, y) in enumerate(seg))
                g.append(f'<path class="{cls}" style="stroke:{col}" d="{d}"/>')
        for x, y in pts:
            hollow = cut is not None and x > cut
            g.append(f'<circle class="dot{" hollow" if hollow else ""}" style="--c:{col}" '
                     f'cx="{xpos(x):.1f}" cy="{ys(y):.1f}" r="4"/>')
        lx, ly = pts[-1]
        ends.append([ys(ly), s["name"], xpos(lx)])
    # direct labels at line ends, nudged apart
    ends.sort()
    for j in range(1, len(ends)):
        ends[j][0] = max(ends[j][0], ends[j - 1][0] + 15)
    for y, name, x in ([] if NARROW else ends):
        g.append(f'<text class="end-label" x="{x + 9:.1f}" y="{y + 4:.1f}">{html.escape(name)}</text>')
    # hover columns
    step = (W - ML - MR) / max(1, len(xs) - 1)
    for x in xs:
        tip = [f"{xname}{x}"] + [f"{s['name']}: {tipfmt(s['points'][x])}" + (" (budget/projection)" if s.get("dashed_from") is not None and x > s["dashed_from"] else "")
                            for s in series if x in s["points"]]
        g.append(f'<rect class="hit" x="{xpos(x) - step / 2:.1f}" y="{mt}" width="{step:.1f}" '
                 f'height="{H - mt - MB}" data-x="{xpos(x):.1f}" data-tip="{html.escape(chr(10).join(tip))}"/>')
    g.append(f'<line class="guide" x1="0" x2="0" y1="{mt}" y2="{H - MB}"/>')
    g.append("</svg>")
    table = data_table(xs, series, tipfmt, xname.strip() or "FY")
    return "".join(g), series, table


def _bar_chart(label, xs, stacks, fmt, tipfmt=None, projected=(), projected_note="budget projection"):
    """Stacked bars. stacks: [{name, points: {x: y}}] bottom to top."""
    tipfmt = tipfmt or fmt
    totals = [sum(s["points"].get(x, 0) for s in stacks) for x in xs]
    ticks = nice_ticks(0, max(totals) * 1.05)
    hi = ticks[-1]
    band = (W - ML - MR) / len(xs)
    bw = band * 0.62
    xpos = lambda x: ML + band * (xs.index(x) + 0.5)
    ys = lambda v: MT + (hi - v) / hi * (H - MT - MB)
    g = [svg_open(label)] + y_axis(ticks, ys, fmt) + x_labels(xs, xpos)
    for x in xs:
        base = 0
        segs = [(k, s["points"][x]) for k, s in enumerate(stacks) if s["points"].get(x)]
        for j, (k, v) in enumerate(segs):
            top = j == len(segs) - 1
            y1, y2 = ys(base + v), ys(base)
            gap = 0 if j == 0 else 2  # surface gap between stacked segments
            h = max(0.0, y2 - y1 - gap)
            cls = "bar hollow" if x in projected else "bar"
            r = 4 if top else 0
            g.append(bar_path(xpos(x) - bw / 2, y1, bw, h, r, cls, stacks[k].get("color", SERIES[k])))
            base += v
        tip = [f"FY{x}" + (f" ({projected_note})" if x in projected else "")]
        tip += [f"{s['name']}: {tipfmt(s['points'][x])}" for s in reversed(stacks) if x in s["points"]]
        if len(segs) > 1:
            tip.append(f"Total: {tipfmt(sum(v for _, v in segs))}")
        g.append(f'<rect class="hit" x="{xpos(x) - band / 2:.1f}" y="{MT}" width="{band:.1f}" '
                 f'height="{H - MT - MB}" data-tip="{html.escape(chr(10).join(tip))}"/>')
    g.append("</svg>")
    table = data_table(xs, list(reversed(stacks)), tipfmt)
    return "".join(g), stacks, table


def bar_path(x, y, w, h, r, cls, col):
    r = min(r, h, w / 2)
    if h <= 0:
        return ""
    d = (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
         f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z")
    return f'<path class="{cls}" style="--c:{col}" d="{d}"/>'


def _hbar_chart(label, cats, series, fmt, xtitle=None):
    """Grouped horizontal bars: cats = [name], series = [{name, values: {cat: v}}]."""
    rowh, gap = 16, 10
    groupH = rowh * len(series) + gap
    ml, mr = (130, 50) if NARROW else (190, 70)
    h = MT + groupH * len(cats) + 26 + (20 if xtitle else 0)
    hi = nice_ticks(0, max(v for s in series for v in s["values"].values()))[-1]
    xs = lambda v: ml + v / hi * (W - ml - mr)
    g = [svg_open(label).replace(f"0 0 {W} {H}", f"0 0 {W} {h}")]
    for t in nice_ticks(0, hi):
        g.append(f'<line class="grid" x1="{xs(t):.1f}" x2="{xs(t):.1f}" y1="{MT}" y2="{h - 26 - (20 if xtitle else 0)}"/>')
        g.append(f'<text class="tick" x="{xs(t):.1f}" y="{h - 8 - (20 if xtitle else 0)}" text-anchor="middle">{fmt(t)}</text>')
    if xtitle:
        g.append(f'<text class="axis-title" x="{(ml + W - mr) / 2:.1f}" y="{h - 6}" text-anchor="middle">{html.escape(xtitle)}</text>')
    for i, c in enumerate(cats):
        y0 = MT + i * groupH
        g.append(f'<text class="cat" x="{ml - 10}" y="{y0 + rowh * len(series) / 2 + 4:.1f}" text-anchor="end">{html.escape(c)}</text>')
        for k, s in enumerate(series):
            v = s["values"].get(c, 0)
            y = y0 + k * rowh
            w = xs(v) - ml
            r = min(4, w)
            d = (f"M{ml},{y + 1} L{ml + w - r:.1f},{y + 1} Q{ml + w:.1f},{y + 1} {ml + w:.1f},{y + 1 + r} "
                 f"L{ml + w:.1f},{y + rowh - 1 - r} Q{ml + w:.1f},{y + rowh - 1} {ml + w - r:.1f},{y + rowh - 1} L{ml},{y + rowh - 1} Z")
            g.append(f'<path class="bar" style="--c:{SERIES[k]}" d="{d}"/>')
            g.append(f'<text class="val" x="{ml + w + 5:.1f}" y="{y + rowh - 4}">{fmt(v)}</text>')
        tip = [c] + [f"{s['name']}: {fmt(s['values'].get(c, 0))}" for s in series]
        g.append(f'<rect class="hit" x="0" y="{y0 - gap / 2}" width="{W}" height="{groupH}" '
                 f'data-tip="{html.escape(chr(10).join(tip))}"/>')
    g.append("</svg>")
    rows = "".join(f"<tr><td>{html.escape(c)}</td>" + "".join(f"<td class=num>{fmt(s['values'].get(c, 0))}</td>" for s in series) + "</tr>" for c in cats)
    table = ("<table><thead><tr><th></th>" + "".join(f"<th class=num>{html.escape(s['name'])}</th>" for s in series)
             + f"</tr></thead><tbody>{rows}</tbody></table>")
    return "".join(g), series, table


def _dot_chart(label, cats, series, fmt, notes=None, xtitle=None, ml_wide=150, ml_narrow=74):
    """Dot plot: one row per category, one dot per series on a shared x axis with a zero line.
    series = [{name, values: {cat: v}}]; notes = {cat: text} drawn at the right of the row and the row shaded."""
    notes = notes or {}
    rowh = 30
    ml, mr = (ml_narrow, 14) if NARROW else (ml_wide, 190)
    h = MT + rowh * len(cats) + 30 + (20 if xtitle else 0)
    xb = h - (20 if xtitle else 0)
    vals = [v for s in series for v in s["values"].values()] + [0]
    ticks = nice_ticks(min(vals), max(vals))
    lo, hi = ticks[0], ticks[-1]
    xs = lambda v: ml + (v - lo) / (hi - lo) * (W - ml - mr)
    g = [svg_open(label).replace(f"0 0 {W} {H}", f"0 0 {W} {h}")]
    for i, c in enumerate(cats):
        if c in notes:
            g.append(f'<rect class="band" x="{ml - (70 if NARROW else 140)}" y="{MT + i * rowh}" width="{W - ml + (70 if NARROW else 140) - (0 if NARROW else mr - 180)}" height="{rowh}"/>')
    for t in ticks:
        g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{xs(t):.1f}" x2="{xs(t):.1f}" y1="{MT}" y2="{xb - 30}"/>')
        g.append(f'<text class="tick" x="{xs(t):.1f}" y="{xb - 12}" text-anchor="middle">{fmt(t)}</text>')
    if xtitle:
        g.append(f'<text class="axis-title" x="{(ml + W - mr) / 2:.1f}" y="{h - 6}" text-anchor="middle">{html.escape(xtitle)}</text>')
    for i, c in enumerate(cats):
        y = MT + i * rowh + rowh / 2
        g.append(f'<text class="cat" x="{ml - 10}" y="{y + 4:.1f}" text-anchor="end">{html.escape(c)}</text>')
        pts = [s["values"][c] for s in series if c in s["values"]]
        g.append(f'<line class="grid" x1="{xs(min(pts)):.1f}" x2="{xs(max(pts)):.1f}" y1="{y:.1f}" y2="{y:.1f}"/>')
        for k, s in enumerate(series):
            if c in s["values"]:
                g.append(f'<circle class="dot" style="--c:{s.get("color", SERIES[k])}" cx="{xs(s["values"][c]):.1f}" cy="{y:.1f}" r="6"/>')
        if c in notes and not NARROW:
            g.append(f'<text class="ref-label" x="{W - mr + 14}" y="{y + 4:.1f}">{html.escape(notes[c])}</text>')
        tip = [c] + [f"{s['name']}: {fmt(s['values'][c])}" for s in series if c in s["values"]]
        g.append(f'<rect class="hit" x="0" y="{MT + i * rowh}" width="{W}" height="{rowh}" data-tip="{html.escape(chr(10).join(tip))}"/>')
    g.append("</svg>")
    rows = "".join(f"<tr><td>{html.escape(c)}</td>" + "".join(f"<td class=num>{fmt(s['values'][c]) if c in s['values'] else '–'}</td>" for s in series) + "</tr>" for c in cats)
    table = ("<table><thead><tr><th></th>" + "".join(f"<th class=num>{html.escape(s['name'])}</th>" for s in series)
             + f"</tr></thead><tbody>{rows}</tbody></table>")
    return "".join(g), series, table


def _scatter_chart(label, groups, xfmt, yfmt, xtitle, ytitle, fit=True, fit_note="straight-line fit (our estimate)", tipx=None):
    """Scatter plot. groups: [{name, color, r, points: [(x, y, tip)], label: bool}] drawn in order (background first).
    fit: dashed least-squares line over every point, labelled with the share of the spread it explains."""
    tipx = tipx or xfmt
    mt, mb = MT + 22, MB + 22
    allp = [(x, y) for gr in groups for x, y, _ in gr["points"]]
    xt = nice_ticks(min(p[0] for p in allp), max(p[0] for p in allp))
    yt = nice_ticks(min(0, min(p[1] for p in allp)), max(p[1] for p in allp))
    xr = lambda v: ML + (v - xt[0]) / (xt[-1] - xt[0]) * (W - ML - MR)
    ys = lambda v: mt + (yt[-1] - v) / (yt[-1] - yt[0]) * (H - mt - mb)
    g = [svg_open(label)]
    for t in yt:
        g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{ML}" x2="{W - MR}" y1="{ys(t):.1f}" y2="{ys(t):.1f}"/>')
        g.append(f'<text class="tick" x="{ML - 8}" y="{ys(t) + 4:.1f}" text-anchor="end">{yfmt(t)}</text>')
    for k, t in enumerate(xt):
        if NARROW and k % 2:
            continue
        g.append(f'<line class="grid" x1="{xr(t):.1f}" x2="{xr(t):.1f}" y1="{mt}" y2="{H - mb}"/>')
        g.append(f'<text class="tick" x="{xr(t):.1f}" y="{H - mb + 18}" text-anchor="middle">{xfmt(t)}</text>')
    g.append(f'<text class="axis-title" x="4" y="{MT}">{html.escape(ytitle)}</text>')
    g.append(f'<text class="axis-title" x="{(ML + W - MR) / 2:.1f}" y="{H - 6}" text-anchor="middle">{html.escape(xtitle)}</text>')
    if fit and len(allp) > 2:
        n = len(allp)
        mx, my = sum(p[0] for p in allp) / n, sum(p[1] for p in allp) / n
        sxy = sum((a - mx) * (b - my) for a, b in allp)
        sxx = sum((a - mx) ** 2 for a, _ in allp)
        syy = sum((b - my) ** 2 for _, b in allp)
        b = sxy / sxx
        x0, x1 = min(p[0] for p in allp), max(p[0] for p in allp)
        g.append(f'<line class="ref" style="stroke:var(--text2)" x1="{xr(x0):.1f}" x2="{xr(x1):.1f}" '
                 f'y1="{ys(my + b * (x0 - mx)):.1f}" y2="{ys(my + b * (x1 - mx)):.1f}"/>')
        g.append(f'<text class="ref-label" x="{W - MR}" y="{mt + 12}" text-anchor="end">'
                 f'{html.escape(fit_note)}: explains {100 * sxy * sxy / (sxx * syy):.0f}% of the spread</text>')
    for gr in groups:
        for x, y, tip in gr["points"]:
            g.append(f'<circle class="dot" style="--c:{gr["color"]};stroke-width:1" cx="{xr(x):.1f}" cy="{ys(y):.1f}" r="{gr.get("r", 3)}"/>')
    for gr in groups:
        for x, y, tip in gr["points"]:
            g.append(f'<circle class="hit" cx="{xr(x):.1f}" cy="{ys(y):.1f}" r="7" data-tip="{html.escape(tip)}"/>')
            if gr.get("label"):
                g.append(f'<text class="maplbl" x="{xr(x) + 9:.1f}" y="{ys(y) - 7:.1f}" style="font-size:12px">{html.escape(gr["name"])}</text>')
    g.append("</svg>")
    rows = sorted(((tip.split(chr(10))[0], x, y) for gr in groups for x, y, tip in gr["points"]), key=lambda r: r[0])
    table = (f"<table><thead><tr><th>District</th><th class=num>{html.escape(xtitle)}</th><th class=num>{html.escape(ytitle)}</th></tr></thead><tbody>"
             + "".join(f"<tr><td>{html.escape(nm)}</td><td class=num>{tipx(x)}</td><td class=num>{yfmt(y)}</td></tr>" for nm, x, y in rows)
             + "</tbody></table>")
    return "".join(g), [{"name": gr["name"], "color": gr["color"]} for gr in reversed(groups)], table


def data_table(xs, series, fmt, xhead="FY"):
    head = f"<tr><th>{html.escape(xhead)}</th>" + "".join(f"<th class=num>{html.escape(s['name'])}</th>" for s in series) + "</tr>"
    body = "".join("<tr><td>" + str(x) + "</td>" + "".join(
        f"<td class=num>{fmt(s['points'][x]) if x in s['points'] else '–'}</td>" for s in series) + "</tr>" for x in xs)
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def dual(fn):
    def run(label, *a, wide_mr=None, **k):
        global W, H, ML, MR, NARROW
        svgs = []
        wide = WIDE if wide_mr is None else WIDE[:3] + (wide_mr,)  # room for longer end labels
        for narrow, dims in ((False, wide), (True, NARROW_DIMS)):
            W, H, ML, MR = dims
            NARROW = narrow
            svg, series, table = fn(label, *a, **k)
            svgs.append(svg)
        W, H, ML, MR = WIDE
        NARROW = False
        return figure(label, "".join(svgs), series, table)
    return run


def figure(label, svg, series, table):
    legend = ""
    if len(series) > 1:
        legend = '<ul class="legend">' + "".join(
            f'<li><span class="swatch" style="background:{s.get("color", SERIES[k])}"></span>{html.escape(s["name"])}</li>'
            for k, s in enumerate(series)) + "</ul>"
    return {"label": label, "html": f'{legend}<div class="chart-box">{svg}</div>'
            f'<details class="table-view"><summary>Show the numbers</summary><div class="table-wrap">{table}</div></details>'}


line_chart, bar_chart, hbar_chart, dot_chart = dual(_line_chart), dual(_bar_chart), dual(_hbar_chart), dual(_dot_chart)
scatter_chart = dual(_scatter_chart)


def chart_block(fig, title, caption):
    return (f'<figure class="viz"><figcaption><h3 id="{slug(title)}">{html.escape(title)}</h3>'
            f'<p>{caption}</p></figcaption>{fig["html"]}</figure>')


# ---- maps (school geography): GeoJSON polygons in lon/lat, drawn as inline SVG
FULL_LO, FULL_HI = 30, 100            # % full mapped onto the color ramp; values outside are clamped
FULL_RAMP = ("#2c4f9e", "#8fd47f")     # blue (empty) to green (full), interpolated in OKLab
ROLE_NAME = {"community": "Community school", "magnet": "Citywide magnet", "specialty": "Specialty or alternative site"}


def _oklab(hexc):
    lin = [((c / 255 + .055) / 1.055) ** 2.4 if c / 255 > .04045 else c / 255 / 12.92
           for c in (int(hexc[i:i + 2], 16) for i in (1, 3, 5))]
    l, m, s = (sum(a * b for a, b in zip(row, lin)) ** (1 / 3) for row in (
        (.4122214708, .5363325363, .0514459929), (.2119034982, .6806995451, .1073969566), (.0883024619, .2817188376, .6299787005)))
    return (.2104542553 * l + .7936177850 * m - .0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + .4505937099 * s,
            .0259040371 * l + .7827717662 * m - .8086757660 * s)


def _hex(lab):
    L, a, b = lab
    l, m, s = ((L + .3963377774 * a + .2158037573 * b) ** 3, (L - .1055613458 * a - .0638541728 * b) ** 3,
               (L - .0894841775 * a - 1.2914855480 * b) ** 3)
    rgb = (4.0767416621 * l - 3.3077115913 * m + .2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
           -.0041960863 * l - .7034186147 * m + 1.7076147010 * s)
    enc = [1.055 * max(0, c) ** (1 / 2.4) - .055 if c > .0031308 else 12.92 * c for c in rgb]
    return "#" + "".join(f"{round(min(1, max(0, c)) * 255):02x}" for c in enc)


def full_color(u):
    f = min(1, max(0, (u - FULL_LO) / (FULL_HI - FULL_LO)))
    a, b = _oklab(FULL_RAMP[0]), _oklab(FULL_RAMP[1])
    return _hex(tuple(x + (y - x) * f for x, y in zip(a, b)))


def map_projection(fcs, width, pad=8):
    pts = [c for fc in fcs for f in fc["features"] for ring in _rings(f["geometry"]) for c in ring]
    lon0, lon1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lat0, lat1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = math.cos(math.radians((lat0 + lat1) / 2))
    s = (width - 2 * pad) / ((lon1 - lon0) * k)
    height = round((lat1 - lat0) * s + 2 * pad)
    return (lambda lon, lat: (pad + (lon - lon0) * k * s, pad + (lat1 - lat) * s)), height


def _rings(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return [ring for poly in polys for ring in poly]


def geom_path(geom, P):
    return " ".join("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (P(*c) for c in ring)) + " Z" for ring in _rings(geom))


def label_point(geom):
    # the inside point farthest from the edges of the largest outer ring (coarse grid search; good enough for labels)
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    ring = max((p[0] for p in polys), key=lambda r: abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:]))))
    k = math.cos(math.radians(ring[0][1]))
    segs = list(zip(ring, ring[1:]))

    def inside(x, y):
        c = False
        for (x0, y0), (x1, y1) in segs:
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                c = not c
        return c

    def edge_dist(x, y):
        best = 1e9
        for (x0, y0), (x1, y1) in segs:
            dx, dy = (x1 - x0) * k, y1 - y0
            px, py = (x - x0) * k, y - y0
            u = max(0, min(1, (px * dx + py * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(px - u * dx, py - u * dy))
        return best

    xs, ys = [c[0] for c in ring], [c[1] for c in ring]
    n = 30
    cands = [(xs and min(xs) + (max(xs) - min(xs)) * (i + .5) / n, min(ys) + (max(ys) - min(ys)) * (j + .5) / n)
             for i in range(n) for j in range(n)]
    return max((c for c in cands if inside(*c)), key=lambda c: edge_dist(*c))


def school_marker(role, x, y, fill, tip):
    cls = f'mk" style="fill:{fill}' if fill else "mk none"
    if role == "magnet":
        shape = f'<path class="{cls}" d="M{x:.1f},{y - 6.5:.1f} L{x + 6.5:.1f},{y:.1f} L{x:.1f},{y + 6.5:.1f} L{x - 6.5:.1f},{y:.1f} Z"/>'
    elif role == "specialty":
        shape = f'<rect class="{cls}" x="{x - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" rx="1.5"/>'
    else:
        shape = f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="4.5"/>'
    return shape.replace('class="mk', 'class="mk-ring', 1) + shape + f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="10" data-tip="{html.escape(tip)}"/>'


def school_tip(s):
    lines = [s["short_name"], f'{ROLE_NAME[s["role"]]}, grades {s["grades"]}']
    if s["utilization_pct"]:
        lines.append(f'{int(float(s["students"])):,} students, {int(float(s["capacity"])):,} seats: {float(s["utilization_pct"]):.0f}% full')
    return "\n".join(lines)


LANDMARKS = {"features": []}   # river, lakes and interstates for orientation; filled from geography/landmarks.geojson


def landmarks_svg(P, labels=True):
    g, lab = [], []
    for f in LANDMARKS["features"]:
        p, geo = f["properties"], f["geometry"]
        if p["kind"] == "water":
            g.append(f'<path class="water" d="{geom_path(geo, P)}"/>')
    for f in LANDMARKS["features"]:
        p, geo = f["properties"], f["geometry"]
        if p["kind"] != "highway":
            continue
        lines = geo["coordinates"] if geo["type"] == "MultiLineString" else [geo["coordinates"]]
        d = " ".join("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (P(*c) for c in ln)) for ln in lines)
        g.append(f'<path class="hwy-case" d="{d}"/><path class="hwy" d="{d}"/>')
        if labels:
            # label the longest piece about a third of the way along (I-94) or two thirds (I-35W)
            ln = max(lines, key=len)
            x, y = P(*ln[int(len(ln) * {"I-94": .3, "I-394": .45}.get(p["name"], .72))])
            w = 7 + 6 * len(p["name"])
            lab.append(f'<g class="hwy-lbl"><rect x="{x - w / 2:.1f}" y="{y - 7:.1f}" width="{w}" height="14" rx="3"/>'
                       f'<text x="{x:.1f}" y="{y + 3.5:.1f}" text-anchor="middle">{html.escape(p["name"])}</text></g>')
    return "".join(g), "".join(lab)


LANDMARK_KEY = ('<li><span class="swatch water"></span>Mississippi River and lakes</li>'
                '<li><svg class="key" viewBox="0 0 16 16" aria-hidden="true"><path class="hwy-case" d="M0,8 L16,8"/>'
                '<path class="hwy" d="M0,8 L16,8"/></svg>I-35W, I-94 and I-394</li>')


def area_map(label, areas, P, width, height, schools=(), charters=(), outline=None, fill=True, labels=True, lbl_cls="maplbl"):
    g = [f'<svg class="map" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}">']
    for f in areas["features"]:
        p = f["properties"]
        cls = f'area" style="fill:{full_color(p["utilization_pct"])}' if fill else "area plain"
        g.append(f'<path class="{cls}" d="{geom_path(f["geometry"], P)}"/>')
    marks, mark_labels = landmarks_svg(P)
    g.append(marks)
    if outline:
        for f in outline["features"]:
            g.append(f'<path class="outline" d="{geom_path(f["geometry"], P)}"/>')
    for f in areas["features"]:
        p = f["properties"]
        if fill:
            tip = [p["area"], ("Served by " + " and ".join(p["buildings"])) if len(p["buildings"]) > 1 else "",
                   f'{int(p["students"]):,} students, {int(p["capacity"]):,} seats: {p["utilization_pct"]:.0f}% full',
                   f'{p["sq_mi"]:.1f} square miles']
            if p.get("middle"):
                tip.append(f'Goes on to {p["middle"]} (middle) and {p["high"]} (high)')
            if p.get("elementary_areas"):
                tip.append(f'Made of {p["elementary_areas"]} elementary areas')
        else:
            tip = [p["area"].title()]
        g.append(f'<path class="hit" d="{geom_path(f["geometry"], P)}" data-tip="{html.escape(chr(10).join(t for t in tip if t))}"/>')
    for c in charters:
        x, y = P(float(c["lon"]), float(c["lat"]))
        g.append(f'<circle class="charter" cx="{x:.1f}" cy="{y:.1f}" r="2.6"/>'
                 f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="7" data-tip="{html.escape("Charter school site" + chr(10) + c["programs"].replace("; ", chr(10)))}"/>')
    for s in schools:
        x, y = P(float(s["lon"]), float(s["lat"]))
        col = full_color(float(s["utilization_pct"])) if s["utilization_pct"] else ""
        g.append(school_marker(s["role"], x, y, col, school_tip(s)))
    if labels:
        for f in (outline or areas)["features"]:
            x, y = P(*label_point(f["geometry"]))
            pr = f["properties"]
            name = pr["area"].title() if not fill or outline else pr.get("label", pr["area"])
            g.append(f'<text class="{lbl_cls}" x="{x:.1f}" y="{y + 3:.1f}" text-anchor="middle">{html.escape(name)}</text>')
    g.append(mark_labels)
    g.append("</svg>")
    return "".join(g)


def change_map(label, pieces, P, width, height, new_lines, old_lines, labels, key):
    g = [f'<svg class="map" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}">'
         f'<defs><marker id="ah-{key}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         '<path class="arrowhead" d="M0,0 L10,5 L0,10 Z"/></marker></defs>']
    for f in pieces["features"]:
        g.append(f'<path class="piece {"chg" if f["properties"]["changed"] else "same"}" d="{geom_path(f["geometry"], P)}"/>')
    marks, mark_labels = landmarks_svg(P)
    g.append(marks)
    for f in old_lines["features"]:
        g.append(f'<path class="old-line" d="{geom_path(f["geometry"], P)}"/>')
    for f in new_lines["features"]:
        g.append(f'<path class="new-line" d="{geom_path(f["geometry"], P)}"/>')
    arrows = []
    for i, f in enumerate(pieces["features"]):
        p = f["properties"]
        tip = (f'{p["before"]} → {p["after"]}' if p["changed"] else f'Kept {p["after"]}') + \
              f'\n2020-21: {p["before"]}\n2025-26: {p["after"]}\n{p["sq_mi"]:.2f} square miles'
        aid = ""
        if p.get("from_school"):
            aid = f"arr-{key}-{i}"
            (x0, y0), (x1, y1) = P(p["from_lon"], p["from_lat"]), P(p["to_lon"], p["to_lat"])
            d = math.hypot(x1 - x0, y1 - y0) or 1
            ex, ey = x1 - (x1 - x0) / d * 6, y1 - (y1 - y0) / d * 6   # stop short of the new school's dot
            # curve slightly so arrows read as movement, not boundary lines
            mx, my = (x0 + ex) / 2 - (ey - y0) * .15, (y0 + ey) / 2 + (ex - x0) * .15
            anchor = lambda x: "start" if x < 70 else "end" if x > width - 70 else "middle"
            lbl = lambda x, y, s, other: (f'<text class="maplbl-big arrow-lbl" x="{x + {"start": -4, "end": 4}.get(anchor(x), 0):.1f}" '
                                          f'y="{y + (-9 if y < other else 16):.1f}" text-anchor="{anchor(x)}">{html.escape(s)}</text>')
            arrows.append(f'<g class="arrow" id="{aid}"><path class="arrow-piece" d="{geom_path(f["geometry"], P)}"/>'
                          f'<path class="arrow-line" d="M{x0:.1f},{y0:.1f} Q{mx:.1f},{my:.1f} {ex:.1f},{ey:.1f}" marker-end="url(#ah-{key})"/>'
                          f'<circle class="arrow-from" cx="{x0:.1f}" cy="{y0:.1f}" r="4.5"/><circle class="arrow-to" cx="{x1:.1f}" cy="{y1:.1f}" r="4.5"/>'
                          + lbl(x0, y0, p["from_school"], y1) + lbl(x1, y1, p["to_school"], y0) + "</g>")
            tip += f'\nArrow: {p["from_school"]} building → {p["to_school"]} building'
        g.append(f'<path class="hit" d="{geom_path(f["geometry"], P)}" data-tip="{html.escape(tip)}"'
                 + (f' data-arrow="{aid}"' if aid else "") + "/>")
    g.append(mark_labels)
    g += arrows
    if labels:
        for f in pieces["features"]:
            p = f["properties"]
            if p["changed"] and p["sq_mi"] >= .9:
                x, y = P(*label_point(f["geometry"]))
                g.append(f'<text class="maplbl" x="{x:.1f}" y="{y + 3:.1f}" text-anchor="middle">{html.escape(p["before"])} → {html.escape(p["after"])}</text>')
    g.append("</svg>")
    return "".join(g)


# ---- statewide district maps (tests and college page): sequential blue bins
DIST_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#0d366b"]


def district_map(label, fc, values, P, width, height, cuts, fmt, keep=None, labels=()):
    """values: {mde_id: number}; cuts: ascending bin edges (len(DIST_RAMP) - 1); keep: optional set of ids to draw;
    labels: [(mde_id, text)] drawn at the district's label point."""
    g = [f'<svg class="map" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}">']
    feats = [f for f in fc["features"] if keep is None or f["properties"]["mde_id"] in keep]
    for f in feats:
        p = f["properties"]
        v = values.get(p["mde_id"])
        tip = html.escape(p["name"] + chr(10) + (fmt(v) if v is not None else "no figure (too few students, or no high school)"))
        # one path per district carries both the fill and the tooltip (inline fill beats .hit's transparent fill)
        fill = "var(--code)" if v is None else DIST_RAMP[sum(v >= c for c in cuts)]
        g.append(f'<path class="area hit" style="fill:{fill};stroke-width:.5" d="{geom_path(f["geometry"], P)}" data-tip="{tip}"/>')
    byid = {f["properties"]["mde_id"]: f for f in feats}
    for i, text in labels:
        if i in byid:
            x, y = P(*label_point(byid[i]["geometry"]))
            g.append(f'<text class="maplbl" x="{x:.1f}" y="{y + 3:.1f}" text-anchor="middle">{html.escape(text)}</text>')
    g.append("</svg>")
    return "".join(g)


def district_legend(cuts, fmt):
    edges = [None] + list(cuts) + [None]
    items = []
    for k, col in enumerate(DIST_RAMP):
        lo, hi = edges[k], edges[k + 1]
        txt = f"under {fmt(hi)}" if lo is None else f"{fmt(lo)} or more" if hi is None else f"{fmt(lo)} to {fmt(hi)}"
        items.append(f'<li><span class="swatch" style="background:{col}"></span>{txt}</li>')
    items.append('<li><span class="swatch" style="background:var(--code);outline:1px solid var(--muted)"></span>no figure</li>')
    return '<ul class="legend">' + "".join(items) + "</ul>"


def map_legend(roles=("community", "magnet", "specialty"), charters=False, bands=True):
    items, ramp = [], ""
    if bands:
        stops = ", ".join(f"{full_color(v)} {100 * (v - FULL_LO) / (FULL_HI - FULL_LO):.0f}%" for v in range(FULL_LO, FULL_HI + 1, 10))
        ramp = (f'<ul class="legend"><li class="ramp"><span>{FULL_LO}% full or less</span><span class="ramp-bar" '
                f'style="background:linear-gradient(90deg, {stops})"></span><span>{FULL_HI}% or more</span></li></ul>')
    icon = {"community": '<circle class="mk none" cx="8" cy="8" r="4.5"/>',
            "magnet": '<path class="mk none" d="M8,1.5 L14.5,8 L8,14.5 L1.5,8 Z"/>',
            "specialty": '<rect class="mk none" x="3.5" y="3.5" width="9" height="9" rx="1.5"/>'}
    items += [f'<li><svg class="key" viewBox="0 0 16 16" aria-hidden="true">{icon[r]}</svg>{ROLE_NAME[r]}</li>' for r in roles]
    if charters:
        items.append('<li><svg class="key" viewBox="0 0 16 16" aria-hidden="true"><circle class="charter" cx="8" cy="8" r="2.6"/></svg>Charter school site</li>')
    return ramp + '<ul class="legend">' + "".join(items) + LANDMARK_KEY + "</ul>"


# number formats
def musd(v):
    if v == 0:
        return "$0"
    return f"${v / 1e6:,.0f}M" if abs(v) >= 1e7 else f"${v / 1e6:,.1f}M"


def musd1(v):
    return f"${v / 1e6:,.1f}M"


def usd(v):
    return f"${v:,.0f}"


def count(v):
    return f"{v:,.0f}"


def pct(v):
    return f"{v:.1f}%" if v % 1 else f"{v:.0f}%"


def enrollment_grades(src, links, md):
    """Enrollment page: drop by grade and level, grade-to-grade retention, recovery scenarios."""
    root = src / "enrollment"
    gpath = root / "mde_grade_enrollment.csv"
    if not gpath.exists():
        return []
    G = ["KG"] + [str(i) for i in range(1, 13)]
    d = {}
    with open(gpath) as f:
        for r in csv.DictReader(f):
            d[(r["entity"], int(r["fiscal_year"]), r["grade"])] = int(r["oct1_count"])
    years = sorted({y for (e, y, g) in d if e == "Minneapolis"})
    levels = [("Elementary (K-5)", G[:6]), ("Middle (6-8)", G[6:9]), ("High (9-12)", G[9:])]
    lv = lambda e, y, gs: sum(d[(e, y, g)] for g in gs)
    out = []
    c_lv = line_chart("Students by school level", [
        {"name": n, "points": {y: lv("Minneapolis", y, gs) for y in years}} for n, gs in levels],
        lambda v: f"{v / 1000:.0f}k" if v >= 1000 else f"{v:.0f}", count, every=2,
        ytitle="Students enrolled on October 1")
    gname = lambda g: "Kindergarten" if g == "KG" else f"Grade {g}"
    c_gr = hbar_chart("Fall 2025 enrollment as a share of fall 2019, by grade", [gname(g) for g in G], [
        {"name": e, "values": {gname(g): 100 * d[(e, 2026, g)] / d[(e, 2020, g)] for g in G}}
        for e in ("Minneapolis", "St. Paul", "Statewide")], lambda v: f"{v:.0f}%",
        xtitle="Students in the grade in fall 2025, as % of the same grade in fall 2019")
    c_k = line_chart("Kindergarten classes, indexed", [
        {"name": e, "points": {y: 100 * d[(e, y, "KG")] / d[(e, 2020, "KG")] for y in years}}
        for e in ("Minneapolis", "St. Paul", "Statewide")], lambda v: f"{v:.0f}", lambda v: f"{v:.1f}", refline=(100, "Fall 2019 = 100"), every=2,
        ytitle="Kindergartners enrolled, fall 2019 = 100")
    with open(root / "excess_departures.csv") as f:
        ex = {int(r["fiscal_year"]): float(r["shortfall"]) for r in csv.DictReader(f)}
    c_ex = line_chart("Students lost each year beyond the pre-2020 norm", [
        {"name": "Extra who left", "points": ex}], count, count, refline=(0, "Usual pre-2020 year"),
        ytitle="Students who left beyond a usual pre-2020 year (grades 1-12)",
        above="Above 0: more students left than usual", below="Below 0: more students stayed or joined than usual")
    secs = {}
    ga = root / "grade_analysis.md"
    if ga.exists():
        secs = {t: md(b) for t, b in sections(sanitize(ga.read_text())) if t}
    take = lambda pat: next((secs.pop(t) for t in list(secs) if re.match(pat, t)), "")
    out += ['<h2 id="by-grade">Where the drop happened</h2>',
            "<p>October 1 counts by grade from the Minnesota Department of Education, fall 2007 to fall 2025, K-12 only. "
            "FY2020 is fall 2019, the last count before the pandemic.</p>", take(r"Summary"),
            chart_block(c_lv, "Students by school level",
                        "October 1 K-12 count by level. Elementary peaked in FY2014 and was already falling before 2020; "
                        "middle school fell most after 2020 (-20% from fall 2019 to fall 2025), high school least (-11%)."),
            chart_block(c_gr, "Every grade got smaller",
                        "Each grade's fall 2025 count as a share of the same grade in fall 2019. In Minneapolis every grade fell; "
                        "grades 6-9 and kindergarten fell most. Statewide, only the early grades fell, which reflects fewer births."),
            chart_block(c_k, "Kindergarten did not come back",
                        "Kindergarten count indexed to fall 2019 = 100. Statewide kindergarten dipped in fall 2020 and bounced back in fall 2021; "
                        "Minneapolis' stayed about 19% lower. Statewide kindergarten has been falling since with births."),
            chart_block(c_ex, "When enrolled students left",
                        "Each year, every class normally shrinks or grows a little as it moves up a grade. "
                        "We take each class's size the previous fall, apply the average change for that grade step in FY2016-FY2020, "
                        "and add up what grades 1-12 should have been. The line is that expected count minus the actual count: "
                        "above zero, that many more students left than in a usual pre-2020 year; below zero, more stayed or joined than usual. "
                        "In fall 2021 (FY2022) about 1,890 more students left than usual. Since fall 2023 the line is below zero. "
                        "Kindergarten is not included; it is shown in the chart above.")]
    gp = root / "grade_progression.csv"
    if gp.exists():
        with open(gp) as f:
            gpr = list(csv.DictReader(f))
        ordn = lambda g: "K" if g == "KG" else g + {"1": "st", "2": "nd", "3": "rd"}.get(g, "th")
        steps = [(f"{G[i]}_to_{G[i + 1]}", f"{ordn(G[i])} to {ordn(G[i + 1])}") for i in range(11)]  # 11th to 12th left out, see caption

        def step_avg(ent, yrs):
            rows = [r for r in gpr if r["entity"] == ent and int(r["fiscal_year"]) in yrs]
            return {lab: 100 * (sum(float(r[k]) for r in rows) / len(rows) - 1) for k, lab in steps}
        c_ret = dot_chart("How much a class grows or shrinks when it moves up a grade", [lab for _, lab in steps], [
            {"name": "MPS before 2020", "values": step_avg("Minneapolis", range(2016, 2021))},
            {"name": "MPS 2020-2022", "values": step_avg("Minneapolis", range(2021, 2024))},
            {"name": "MPS since 2023", "values": step_avg("Minneapolis", range(2024, 2027))},
            {"name": "Statewide since 2023", "values": step_avg("Statewide", range(2024, 2027)), "color": "var(--muted)"}],
            lambda v: f"{v:+.0f}%" if round(v) else "0%",
            notes={"5th to 6th": "Into middle school: MPS loses ~11%", "8th to 9th": "Into high school: MPS gains ~11%"},
            xtitle="Change in class size when it moves up a grade (left = students left, right = students joined)")
        out.append(chart_block(c_ret, "Where classes lose and gain students",
                               "Each dot is the average change in a class's size from one fall to the next as it moves up a grade "
                               "(for example, this year's 3rd graders against last year's 2nd graders). Left of zero the class lost students, "
                               "right of zero it gained. Before 2020 MPS lost 3-6% of each elementary class every year, while classes statewide "
                               "grew slightly. In fall 2020-2022 every step got worse. Since fall 2023 MPS classes hold their size through "
                               "elementary and middle school, the move into 6th grade still loses about 11%, and 9th grade gains about 11%, "
                               "more than before. 11th to 12th is left out: 12th grade also counts students taking extra years, so it is "
                               "20-25% larger everywhere. Averages of FY2016-FY2020, FY2021-FY2023 and FY2024-FY2026."))
    secs = {t: b for t, b in secs.items() if not re.match(r"Grade-to-grade", t)}
    with open(root / "recovery_scenarios.csv") as f:
        sc = list(csv.DictReader(f))
    names = list(dict.fromkeys(r["scenario"] for r in sc))
    hist = {y: lv("Minneapolis", y, G) for y in years if y >= 2014}
    proj = lambda n: {2026: hist[2026], **{int(r["fiscal_year"]): int(r["k12"]) for r in sc if r["scenario"] == n}}
    ref = (hist[2020], f"Fall 2019: {hist[2020]:,}")
    kset = ["Choices stay as now", "Half way back at K", "Fully back at K", "Leaving like pre-2020",
            "District +10% target"]
    tset = ["Choices stay as now", "Fewer leave at 6th", "More join at 9th", "All three gains"]
    yfmt = lambda v: f"{v / 1000:.0f}k"
    ytitle = "K-12 students enrolled on October 1 (projected after FY2026)"
    c_s1 = line_chart("Recovery scenarios: families choosing MPS for kindergarten", [{"name": "Actual", "points": hist}] +
                      [{"name": n, "points": proj(n), "dashed_from": 2026} for n in kset if n in names], yfmt, count, refline=ref, every=3,
                      ytitle=ytitle, wide_mr=150)
    c_s2 = line_chart("Recovery scenarios: families staying at 6th grade or joining at 9th", [{"name": "Actual", "points": hist}] +
                      [{"name": n, "points": proj(n), "dashed_from": 2026, "color": SERIES[[1, 4, 5, 3][k]]} for k, n in enumerate(tset) if n in names],
                      yfmt, count, refline=ref, every=3, ytitle=ytitle, wide_mr=150)
    rec = take(r"Recovery scenarios")
    cut = rec.find("<div class=\"table-wrap\">")
    head, tail = (rec[:cut], rec[cut:]) if cut > 0 else (rec, "")
    out += ['<h2 id="recovery-scenarios">Recovery scenarios, FY2027-FY2039</h2>', head,
            chart_block(c_s1, "If more or fewer families choose MPS",
                        "Every line has births falling 1.4% a year. Choices stay as now: MPS keeps 4.16% of Minnesota kindergartners and every "
                        "grade step stays at its FY2024-FY2026 average. Half way back at K / Fully back at K: that kindergarten share rises over "
                        "three years to 4.36% / 4.56% (the FY2017-FY2020 average). Leaving like pre-2020: kindergarten share as now, but every "
                        "grade step returns to its FY2016-FY2020 average. District +10% target: the kindergarten share the district's planned "
                        "+10% elementary growth by 2035-36 would need (4.97%, last seen in fall 2014). Solid line is actual, dashed lines are projections."),
            chart_block(c_s2, "If more families stay at 6th grade or join at 9th",
                        "Every line has births falling 1.4% a year and the kindergarten share as now, unless stated. Fewer leave at 6th: the "
                        "class moving into 6th keeps 94% of its students instead of 89%. More join at 9th: 9th grade gains 18% over the "
                        "previous year's 8th grade instead of 11%, as in St. Paul. All three gains: those two plus the kindergarten share "
                        "half way back (4.36%)."), tail]
    for t, b in secs.items():
        out += [f'<h3 id="{slug(t)}">{html.escape(t)}</h3>', b]
    return out


# ---------------------------------------------------------------- pages
NAV = [("index.html", "Overview"), ("governance.html", "Governance"), ("structure.html", "Financial structure"), ("unions.html", "Unions"), ("meals.html", "Meal funding"), ("money.html", "Money in and out"), ("geography.html", "School geography"),
       ("facilities.html", "Buildings"), ("enrollment.html", "Enrollment"), ("outcomes.html", "Tests and college"), ("sources.html", "Sources and gaps"), ("data.html", "Data")]


def page(fname, title, body, built):
    nav = "".join(f'<a href="{h}"{" aria-current=page" if h == fname else ""}>{t}</a>' for h, t in NAV)
    full = title if fname == "index.html" else f"{title} · {SITE_TITLE}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(full)}</title>
<meta name="description" content="An independent look at Minneapolis Public Schools since FY2015 (finances, enrollment, buildings and governance), built from audited reports, budgets and state data.">
<link rel="stylesheet" href="assets/site.css">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect x='1' y='8' width='3' height='7' rx='1' fill='%232a78d6'/%3E%3Crect x='6.5' y='4' width='3' height='11' rx='1' fill='%232a78d6'/%3E%3Crect x='12' y='1' width='3' height='14' rx='1' fill='%23eb6834'/%3E%3C/svg%3E">
</head>
<body>
<header class="site-header"><div class="wrap">
<a class="brand" href="index.html">{SITE_TITLE}</a>
<nav>{nav}</nav>
</div></header>
<main class="wrap">
{body}
</main>
<footer class="wrap site-footer">
<p>Compiled from public documents: the district's audited annual financial reports, budget books and board materials, Minnesota Department of Education reports, federal data and press coverage. Every figure links back to its source on the <a href="sources.html">sources page</a>; the underlying tables are on the <a href="data.html">data page</a>. Fiscal years end June 30 (FY2024 is the 2023-24 school year). Last built {built}.</p>
</footer>
<div class="tooltip" role="status" hidden></div>
<script src="assets/site.js"></script>
</body>
</html>
"""


CSS = r"""
:root{color-scheme:light;--bg:#fcfcfb;--surface:#ffffff;--border:#e6e5e1;--grid:#ecebe7;
--text:#0b0b0b;--text2:#52514e;--muted:#74736f;--link:#1c5cab;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
--s4:#eda100;--s5:#e87ba4;--s6:#008300;--ref:#d03b3b;--code:#f3f2ef;--water:#c6def2;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19;--surface:#222221;
--border:#383835;--grid:#2e2e2c;--text:#f0efec;--text2:#c3c2b7;--muted:#9a9990;--link:#86b6ef;--s1:#3987e5;--s2:#d95926;
--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--ref:#e66767;--code:#2a2a28;--water:#30506b;
}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19;--surface:#222221;--border:#383835;--grid:#2e2e2c;--text:#f0efec;
--text2:#c3c2b7;--muted:#9a9990;--link:#86b6ef;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--ref:#e66767;--code:#2a2a28;--water:#30506b;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:0 16px}
a{color:var(--link)}
a.cite{text-decoration:none;font-size:.85em;white-space:nowrap}
li[id]{scroll-margin-top:1em}
li:target{background:var(--code);border-radius:4px}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.88em}
.site-header{border-bottom:1px solid var(--border);background:var(--surface)}
.site-header .wrap{display:flex;flex-wrap:wrap;gap:6px 24px;align-items:baseline;padding-top:14px;padding-bottom:12px}
.brand{font-weight:650;color:var(--text);text-decoration:none;font-size:1.05rem}
nav{display:flex;flex-wrap:wrap;gap:4px 16px}
nav a{color:var(--text2);text-decoration:none;font-size:.95rem;padding:2px 0;border-bottom:2px solid transparent}
nav a[aria-current]{color:var(--text);border-bottom-color:var(--s1)}
nav a:hover{color:var(--text)}
main{padding-top:28px;padding-bottom:40px}
h1{font-size:2rem;line-height:1.2;margin:.2em 0 .4em;letter-spacing:-.01em}
h2{font-size:1.4rem;line-height:1.3;margin:2.2em 0 .6em;padding-top:.6em;border-top:1px solid var(--border)}
h3{font-size:1.08rem;margin:1.6em 0 .4em}
.lede{font-size:1.15rem;color:var(--text2);max-width:46em}
p,li{max-width:46em;overflow-wrap:break-word}
p a,li a{overflow-wrap:anywhere}
li{margin:.45em 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:24px 0 8px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.kpi .k-label{font-size:.85rem;color:var(--text2)}
.kpi .k-value{font-size:1.75rem;font-weight:650;line-height:1.2;margin:4px 0;font-variant-numeric:tabular-nums}
.kpi .k-note{font-size:.82rem;color:var(--muted);line-height:1.4}
figure.viz{margin:28px 0;padding:16px;background:var(--surface);border:1px solid var(--border);border-radius:10px}
figure.viz figcaption h3{margin:0 0 4px}
figure.viz figcaption p{margin:0 0 10px;color:var(--text2);font-size:.92rem}
.chart-box{position:relative}
svg.chart{width:100%;height:auto;display:block;overflow:visible;font-size:12px}
svg.chart.narrow{display:none;font-size:13px}svg.chart.narrow .cat{font-size:12px}
.grid{stroke:var(--grid);stroke-width:1}
.grid.zero{stroke:var(--muted)}
.tick{fill:var(--muted);font-variant-numeric:tabular-nums}
.cat{fill:var(--text2)}
.val{fill:var(--text2);font-size:11px;font-variant-numeric:tabular-nums}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.line.dashed{stroke-dasharray:5 4}
.dot{fill:var(--c);stroke:var(--surface);stroke-width:2}
.dot.hollow{fill:var(--surface);stroke:var(--c);stroke-width:2}
.bar{fill:var(--c)}
.bar.hollow{fill:transparent;stroke:var(--c);stroke-width:1.5;stroke-dasharray:4 3}
.end-label{fill:var(--text2);font-size:12px}
.ref{stroke:var(--ref);stroke-width:1.5;stroke-dasharray:6 4}
.ref-label{fill:var(--text2);font-size:11px}
.band{fill:var(--code)}
.axis-title{fill:var(--text2);font-size:12px;font-weight:600}
svg.map{width:100%;height:auto;display:block;margin:0 auto;font-size:9px}
svg.map.big{max-width:560px}
.map-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.map-pair h4{margin:0 0 4px;font-size:.9rem;color:var(--text2);font-weight:600;text-align:center}
.area{stroke:var(--surface);stroke-width:1.2;stroke-linejoin:round}
.area.plain{fill:var(--code);stroke:var(--muted);stroke-width:.6}
.legend li.ramp{gap:8px}.ramp-bar{width:180px;height:12px;border-radius:3px;display:inline-block}
.outline{fill:none;stroke:var(--text);stroke-width:1.6;stroke-linejoin:round;pointer-events:none}
.mk{stroke:var(--text);stroke-width:1.3}.mk.none{fill:var(--surface)}.mk-ring{fill:none;stroke:var(--surface);stroke-width:4.5}
.charter{fill:var(--muted);stroke:var(--surface);stroke-width:.8}
.maplbl,.maplbl-big{fill:var(--text);font-weight:600;paint-order:stroke;stroke:var(--surface);stroke-width:2.6px;stroke-linejoin:round;pointer-events:none}
.maplbl-big{font-size:12px}
svg.key{width:16px;height:16px;flex:none}
.piece{stroke:none}.piece.chg,.swatch.chg{fill:var(--s2);background:var(--s2);opacity:.75}.piece.same,.swatch.same{fill:var(--code);background:var(--code)}
.swatch.same{border:1px solid var(--border)}
.new-line{fill:none;stroke:var(--text);stroke-width:1.1;stroke-linejoin:round;pointer-events:none}
.arrow{display:none;pointer-events:none}.arrow.on{display:inline}
.arrow-piece{fill:none;stroke:var(--text);stroke-width:2}
.arrow-line{fill:none;stroke:var(--text);stroke-width:2.2;stroke-linecap:round}.arrowhead{fill:var(--text)}
.arrow-from{fill:var(--surface);stroke:var(--text);stroke-width:2}.arrow-to{fill:var(--text);stroke:var(--surface);stroke-width:1.5}
.water,.swatch.water{fill:var(--water);background:var(--water);stroke:none;pointer-events:none}
.hwy-case{fill:none;stroke:var(--surface);stroke-width:4;stroke-linejoin:round;stroke-linecap:round;pointer-events:none}
.hwy{fill:none;stroke:var(--text2);stroke-width:1.8;stroke-linejoin:round;stroke-linecap:round;pointer-events:none}
.hwy-lbl{pointer-events:none}.hwy-lbl rect{fill:var(--surface);stroke:var(--text2);stroke-width:1}
.hwy-lbl text{fill:var(--text);font-size:9.5px;font-weight:700}
.old-line{fill:none;stroke:var(--text);stroke-width:1.1;stroke-dasharray:3 2.5;opacity:.7;pointer-events:none}
@media (max-width:600px){.map-pair{grid-template-columns:1fr}svg.map{font-size:11px}}
.hit{fill:transparent;cursor:crosshair}
.guide{stroke:var(--muted);stroke-width:1;opacity:0;pointer-events:none}
.legend{list-style:none;padding:0;margin:0 0 6px;display:flex;flex-wrap:wrap;gap:4px 18px;font-size:.88rem;color:var(--text2)}
.legend li{margin:0;display:flex;align-items:center;gap:6px}
.swatch{width:12px;height:12px;border-radius:3px;display:inline-block}
.tooltip{position:fixed;z-index:10;pointer-events:none;background:var(--surface);color:var(--text);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;font-size:.85rem;line-height:1.45;white-space:pre-line;box-shadow:0 4px 14px rgba(0,0,0,.12);
max-width:280px;font-variant-numeric:tabular-nums}
.tooltip::first-line{font-weight:650}
.tooltip.see-through{background:color-mix(in srgb,var(--surface) 35%,transparent);box-shadow:none;border-color:color-mix(in srgb,var(--border) 60%,transparent);text-shadow:0 0 2px var(--surface),0 0 2px var(--surface),0 0 3px var(--surface)}
pre{background:var(--code);padding:12px 14px;border-radius:8px;overflow-x:auto;font-size:.82rem;line-height:1.45}
details.table-view{margin-top:8px;font-size:.9rem}
details.table-view summary{cursor:pointer;color:var(--text2)}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:12px 0}
table{border-collapse:collapse;font-size:.88rem;width:100%}
th,td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{font-weight:600;color:var(--text2);background:var(--surface)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.note{font-size:.9rem;color:var(--text2)}
.downloads td:first-child{white-space:nowrap}
.site-footer{border-top:1px solid var(--border);color:var(--muted);font-size:.85rem;padding-top:16px;padding-bottom:40px}
@media (max-width:600px){h1{font-size:1.6rem}.kpi .k-value{font-size:1.5rem}figure.viz{padding:12px 10px;margin:20px -6px}
svg.chart.wide{display:none}svg.chart.narrow{display:block}}
@media print{.site-header nav,.tooltip{display:none}pre{background:var(--code);padding:12px 14px;border-radius:8px;overflow-x:auto;font-size:.82rem;line-height:1.45}
details.table-view{display:block}figure.viz{break-inside:avoid}}
"""

JS = r"""
(function(){
  var tip=document.querySelector('.tooltip');
  var shown=null;
  function arrow(id){if(shown&&shown.id===id)return;if(shown)shown.classList.remove('on');shown=id?document.getElementById(id):null;if(shown)shown.classList.add('on');}
  function hide(){tip.hidden=true;arrow(null);document.querySelectorAll('.guide').forEach(function(g){g.style.opacity=0});}
  document.addEventListener('pointermove',function(e){
    var t=e.target;
    if(!(t instanceof Element)||!t.classList.contains('hit')){hide();return;}
    tip.textContent=t.getAttribute('data-tip');tip.hidden=false;arrow(t.getAttribute('data-arrow'));tip.classList.toggle('see-through',!!t.closest('svg.map'));
    var w=tip.offsetWidth,h=tip.offsetHeight,x=e.clientX+14,y=e.clientY+14;
    if(x+w>window.innerWidth-8)x=e.clientX-w-14;
    if(y+h>window.innerHeight-8)y=e.clientY-h-14;
    tip.style.left=Math.max(8,x)+'px';tip.style.top=Math.max(8,y)+'px';
    var svg=t.ownerSVGElement,g=svg&&svg.querySelector('.guide'),gx=t.getAttribute('data-x');
    if(g&&gx){g.setAttribute('x1',gx);g.setAttribute('x2',gx);g.style.opacity=.6;}
  });
  document.addEventListener('pointerleave',hide);
  window.addEventListener('scroll',hide,{passive:true});
})();
"""

# Short descriptions for the data downloads; unknown files still get listed.
DATA_NOTES = {
    "mde_compensatory_mps.csv": "State compensatory education revenue for MPS by fiscal year, FY2013-2028, with the free and reduced-price counts behind it (MDE Minnesota Funding Reports).",
    "meal_linked_revenue.csv": "Meal-linked money: Title I and child nutrition spending, the food service fund by year, and House Research compensatory counts.",
    "fte_by_bargaining_group.csv": "Staff (FTE) by job classification FY2016-2025 with the bargaining group each maps to (our mapping), plus our FY2025 payroll estimate and its assumptions.",
    "teacher_union_flows.csv": "Payments between AFT, NEA, Education Minnesota and Local 59 by year, from federal LM-2 reports and Form 990 Schedule I.",
    "irs990_by_org_year.csv": "IRS Form 990 totals ($ thousands) for Local 59, Education Minnesota, AFT, NEA, SEIU 284, Teamsters 320, AFSCME Council 5 and labor federations.",
    "cfb_funds.csv": "Minnesota Campaign Finance Board itemized receipts and spending of union political funds (itemized rows over $200 only).",
    "mps_panel.csv": "One row per fiscal year: general fund revenue, spending, balances, enrollment, per-student figures, staffing. Start here.",
    "acfr24_statistical.csv": "10-year audited general fund history FY2015-2024 from the FY2024 annual financial report's statistical section.",
    "acfr25_statistical.csv": "FY2025 audited figures from the FY2025 annual financial report.",
    "acfr2024_statistical_10yr.csv": "Second, independent extraction of the FY2024 report's 10-year tables, used as a cross-check.",
    "budget_fy2026_fy2027.csv": "FY2026 budget, balancing sources, projected balances and FY2027 gap estimates.",
    "acfr_general_fund_operations.csv": "General fund revenue and spending detail from the FY2022-2024 annual reports.",
    "acfr_general_fund_balance.csv": "General fund balance components from the annual reports.",
    "acfr_general_fund_mdna_tables.csv": "General fund tables from the reports' management discussion and analysis.",
    "acfr_governmental_funds.csv": "All governmental funds: revenue and spending totals.",
    "acfr_long_term_liabilities.csv": "Debt, pensions and other long-term liabilities.",
    "acfr_net_position.csv": "District-wide net position.",
    "acfr_enrollment.csv": "Enrollment (average daily membership) from the annual reports.",
    "acfr_supplement_budgetbook_gf.csv": "General fund figures from budget books, filling years the reports do not cover.",
    "gapfill_general_fund.csv": "FY2015-2017 general fund figures from a 2018 bond official statement.",
    "mde_finance.csv": "Minnesota Department of Education finance figures for the district.",
    "mde_comparisons.csv": "State comparisons: revenue per pupil, referendum revenue, peer districts.",
    "mde_enrollment.csv": "State enrollment figures.",
    "mde_state.csv": "Statewide figures used as benchmarks.",
    "nces_finance.csv": "Federal (NCES F-33) revenue and spending per student.",
    "building_utilization_fy2025.csv": "Every district building in 2024-25: seats, students, how full, and which bar of the Buildings page charts it counts in (or why not).",
    "facilities_spending_series.csv": "Building operations, capital and debt spending FY2016-2025: all governmental funds, General Fund, and Building Construction fund.",
    "school_levy_history.csv": "School property tax levy by purpose and Minneapolis tax base, taxes payable 2016-2026.",
    "debt_service_projection.csv": "Debt payments on existing bonds and two borrowing scenarios to FY2040 (estimate, not a district projection).",
    "enrollment_vs_population.csv": "District enrollment against Minneapolis population estimates, by school year.",
    "mde_grade_enrollment.csv": "October 1 enrollment by grade for Minneapolis, St. Paul and the state, FY2008-FY2026 (MDE), with a download link for each source file.",
    "grade_progression.csv": "Grade-to-grade retention ratios by year: each grade's count over the previous grade's count a year earlier.",
    "excess_departures.csv": "Students in grades 1-12 missing each year compared with pre-2020 grade-to-grade retention.",
    "enrollment_decomposition.csv": "Fall 2019 to fall 2025 K-12 change split into the pre-2020 trend, smaller kindergarten classes and retention.",
    "recovery_scenarios.csv": "Our own cohort-survival projections of K-12 enrollment FY2027-FY2039 under stated assumptions, by level.",
    "recovery_grid.csv": "Projected K-12 enrollment in FY2033 and FY2039 by kindergarten size and retention assumption.",
    "attendance_area_summary.csv": "Each elementary, middle and high school attendance area in 2025-26: its community school, size, and 2024-25 seats and students.",
    "school_locations.csv": "Location, grades, type (community, magnet, specialty) and 2024-25 seats and students of each school building on the School geography maps.",
    "seats_by_high_school_area.csv": "Seats, students and empty seats in all school buildings inside each 2025-26 high school area, 2024-25.",
    "change_high_2021.geojson": "Overlay of 2020-21 and 2025-26 high school areas: each piece of land with its old and new high school, and whether it changed.",
    "change_elementary_2021.geojson": "Overlay of 2020-21 and 2025-26 elementary areas: each piece of land with its old and new elementary area, and whether its school changed.",
    "outcomes_trends.csv": "Math and reading proficiency, four-year graduation, and college start and degree rates by year for Minneapolis, St. Paul, our first- and second-ring suburb groups (graduate-weighted, estimate) and the state.",
    "district_outcomes.csv": "Every Minnesota school district: 2025 math and graduation, pooled college start (classes 2022-24) and bachelor's (2015-17) rates (our estimate), spending per student and staffing.",
    "charter_locations.csv": "Charter school sites inside Minneapolis in 2025-26, from the state school locations file.",
}


def kpi(label, value, note):
    return (f'<div class="kpi"><div class="k-label">{label}</div><div class="k-value">{value}</div>'
            f'<div class="k-note">{note}</div></div>')


def build(src, out):
    panel = load_panel(src)
    stat = load_long(src / "data" / "acfr24_statistical.csv")
    stat.update(load_long(src / "data" / "acfr25_statistical.csv"))
    report = sanitize((src / "analysis" / "report.md").read_text())
    secs = sections(report)
    data_files = sorted((src / "data").glob("*.csv")) + sorted((src / "enrollment").glob("*.csv"))
    data_names = {p.name for p in data_files}

    def links(path):
        name = path.rsplit("/", 1)[-1]
        if name in data_names:
            return f"data/{name}"
        if path.startswith(("../sources", "sources/")):
            return "sources.html#every-document"
        if path.startswith(("../data", "data/")):
            return "data.html"
        return None

    def md(text):
        return md_to_html(text, links)

    used = set()

    def sec(match):
        for t, body in secs:
            if t and re.search(match, t, re.I):
                used.add(t)
                return t, body
        return None, ""

    actual = [fy for fy in sorted(panel) if panel[fy].get("basis", "").startswith("audited")]
    last, first = actual[-1], actual[0]
    budget_yrs = [fy for fy in sorted(panel) if fy > last and panel[fy].get("revenue")]
    P = panel

    def pts(key, years, scale=1):
        return {fy: P[fy][key] * scale for fy in years if P[fy].get(key) is not None}

    # ---- charts
    rv_years = actual + budget_yrs
    c_revexp = line_chart("General fund revenue and spending", [
        {"name": "Revenue", "points": pts("revenue", rv_years), "dashed_from": last},
        {"name": "Spending", "points": pts("expenditure", rv_years), "dashed_from": last}],
        musd, musd1)
    other = {fy: P[fy]["ending_balance"] - P[fy]["unassigned"] for fy in actual
             if P[fy].get("ending_balance") and P[fy].get("unassigned")}
    c_bal = bar_chart("General fund ending balance", actual, [
        {"name": "Unassigned (free to spend)", "points": pts("unassigned", actual)},
        {"name": "Committed, assigned and other", "points": other, "color": "var(--s3)"}],
        musd, musd1)
    c_unas = line_chart("Unassigned balance as a share of spending", [
        {"name": "Unassigned share", "points": pts("unassigned_pct_of_exp", rv_years), "dashed_from": last}],
        pct, pct, y0=0, refline=(8, "Board policy floor: 8%"))
    c_adm = line_chart("Students counted for state aid (ADM)", [
        {"name": "Students (ADM)", "points": pts("adm", actual)}], count, count)
    c_ppe = line_chart("General fund spending per student", [
        {"name": "In 2024 dollars", "points": pts("exp_per_adm_2024usd", actual)},
        {"name": "As spent", "points": pts("exp_per_adm", actual)}], usd, usd)
    c_src = line_chart("General fund revenue by source", [
        {"name": "State", "points": pts("rev_state", actual)},
        {"name": "Property tax", "points": pts("rev_property_tax", actual)},
        {"name": "Federal", "points": pts("rev_federal", actual)}], musd, musd1, y0=0)
    base = P[first]
    idx = lambda key: {fy: 100 * P[fy][key] / base[key] for fy in actual if P[fy].get(key)}
    c_staff = line_chart("Students and staff, indexed to FY2015 = 100", [
        {"name": "All employees", "points": idx("employees_fte")},
        {"name": "Teachers", "points": idx("teachers_fte")},
        {"name": "Students", "points": idx("adm")}],
        lambda v: f"{v:.0f}", lambda v: f"{v:.1f}")
    progs = [("Regular instruction", "gf_exp_regular_instruction"),
             ("Special education", "gf_exp_special_ed_instruction"),
             ("Pupil support", "gf_exp_pupil_support"),
             ("Instructional support", "gf_exp_instructional_support"),
             ("Sites and buildings", "gf_exp_sites_buildings"),
             ("District support", "gf_exp_district_support"),
             ("Administration", "gf_exp_administration")]
    prog_years = [y for y in (first, last) if all((y, m) in stat for _, m in progs)]
    c_prog = hbar_chart("General fund spending by program", [p for p, _ in progs],
                        [{"name": f"FY{y}", "values": {p: stat[(y, m)] for p, m in progs}} for y in prog_years],
                        musd) if prog_years else None

    # ---- spending detail: program over time, per student after inflation, by object, fixed vs variable
    mdna = load_long(src / "data" / "acfr_general_fund_mdna_tables.csv")
    bud = load_long(src / "data" / "budget_fy2026_fy2027.csv")
    g = lambda y, *ms: sum(stat.get((y, m), 0) for m in ms)
    groups = [("Regular instruction", ("gf_exp_regular_instruction", "gf_exp_vocational_instruction")),
              ("Special education", ("gf_exp_special_ed_instruction",)),
              ("Pupil support", ("gf_exp_pupil_support",)),
              ("Instructional support", ("gf_exp_instructional_support",)),
              ("Administration and district support", ("gf_exp_administration", "gf_exp_district_support")),
              ("Buildings, capital and other", ("gf_exp_sites_buildings", "gf_exp_capital_outlay", "gf_exp_debt_service",
                                                "gf_exp_fiscal_fixed", "gf_exp_community_ed"))]
    prog_all = [y for y in actual if (y, "gf_exp_regular_instruction") in stat]
    c_prog_years = bar_chart("General fund spending by program, by year", prog_all,
                             [{"name": n, "points": {y: g(y, *ms) for y in prog_all}} for n, ms in groups],
                             musd, musd1) if prog_all else None
    # 2024 dollars, using the same CPI factors as the panel's per-student column
    defl = {fy: P[fy]["exp_per_adm_2024usd"] / P[fy]["exp_per_adm"] for fy in actual
            if P[fy].get("exp_per_adm_2024usd") and P[fy].get("exp_per_adm")}
    pp_years = [y for y in prog_all if y in defl and P[y].get("adm")]
    per = lambda ms: {y: g(y, *ms) * defl[y] / P[y]["adm"] for y in pp_years}
    instr_pp = per(groups[0][1] + groups[1][1])
    c_per = line_chart("General fund spending per student outside instruction, 2024 dollars", [
        {"name": "Pupil support", "points": per(groups[2][1])},
        {"name": "Central admin", "points": per(groups[4][1])},
        {"name": "Buildings, other", "points": per(groups[5][1])},
        {"name": "Instr. support", "points": per(groups[3][1])}],
        usd, usd, y0=0) if pp_years else None
    objs = [("Salaries", "salaries", "gf_salaries_budget"), ("Benefits", "employee_benefits", "gf_benefits_budget"),
            ("Purchased services", "purchased_services", "gf_purchased_services_budget"),
            ("Supplies and materials", "supplies_materials", "gf_supplies_budget")]
    obj_years = sorted({y for (y, m) in mdna if m == "gf_exp_object_total"})
    obj_proj = [2027] if all((2027, b) in bud for _, _, b in objs) and (2027, "gf_expenditure_adopted") in bud else []
    c_obj = None
    if obj_years:
        stacks = []
        for n, m, b in objs:
            pts_ = {y: mdna[(y, f"gf_exp_object_{m}")] for y in obj_years if (y, f"gf_exp_object_{m}") in mdna}
            pts_.update({y: bud[(y, b)] for y in obj_proj})
            stacks.append({"name": n, "points": pts_})
        rest = {y: mdna.get((y, "gf_exp_object_capital_expenditures"), 0) + mdna.get((y, "gf_exp_object_other_expenditures"), 0)
                for y in obj_years}
        rest.update({y: bud[(y, "gf_expenditure_adopted")] - sum(bud[(y, b)] for _, _, b in objs) for y in obj_proj})
        stacks.append({"name": "Capital and other", "points": rest})
        c_obj = bar_chart("General fund spending by type", obj_years + obj_proj, stacks, musd, musd1, projected=obj_proj)
    sp = src / "analysis" / "sped_pupil_support_series.csv"
    c_sped = c_pupil = None
    if sp.exists():
        with open(sp) as f:
            srows = [r for r in csv.DictReader(f) if r["fiscal_year"].isdigit()]
        sa = max(int(r["fiscal_year"]) for r in srows if r["basis"].startswith("audited"))
        col = lambda k: {int(r["fiscal_year"]): float(r[k]) for r in srows if r[k]}
        reg = {y: stat[(y, "gf_exp_regular_instruction")] for y in col("sped_instruction_usd") if (y, "gf_exp_regular_instruction") in stat}
        c_sped = line_chart("Regular and special education instruction", [
            {"name": "Regular instruction", "points": reg},
            {"name": "Special education", "points": col("sped_instruction_usd"), "dashed_from": sa}],
            musd, musd1, y0=0)
        c_pupil = line_chart("Pupil support", [
            {"name": "Pupil support", "points": col("pupil_support_usd"), "dashed_from": sa}],
            musd, musd1, y0=0)
    c_state = None
    sr = src / "analysis" / "state_revenue_by_stream.csv"
    if sr.exists():
        with open(sr) as f:
            srow = [r for r in csv.DictReader(f) if r["state_revenue_total_usd"]]
        fnum = lambda r, k: float(r[k]) if r[k] else None
        split = [r for r in srow if r["special_ed_state_aid_usd"]]
        pick = lambda fn: {int(r["fiscal_year"]): fn(r) for r in split if fn(r)}
        c_state = bar_chart("General fund state revenue by stream", [int(r["fiscal_year"]) for r in srow], [
            {"name": "Other state aid", "points": pick(lambda r: fnum(r, "other_state_aid_usd"))},
            {"name": "Other special education aid", "points": pick(lambda r: fnum(r, "special_ed_state_aid_usd")
                - (fnum(r, "of_which_special_transport_aid_usd") or 0) - (fnum(r, "of_which_cross_subsidy_reduction_aid_usd") or 0))},
            {"name": "Special ed cross-subsidy aid", "points": pick(lambda r: fnum(r, "of_which_cross_subsidy_reduction_aid_usd"))},
            {"name": "Special transportation aid", "points": pick(lambda r: fnum(r, "of_which_special_transport_aid_usd"))},
            {"name": "State aid, split not available", "color": "var(--muted)",
             "points": {int(r["fiscal_year"]): float(r["state_revenue_total_usd"]) for r in srow if not r["special_ed_state_aid_usd"]}}],
            musd, musd1)
    c_comp = c_routes = None
    pc, rc = src / "analysis" / "pupil_support_components.csv", src / "analysis" / "transportation_route_costs.csv"
    if pc.exists():
        with open(pc) as f:
            prow = list(csv.DictReader(f))
        px = [int(r["fiscal_year"]) for r in prow]
        c_comp = bar_chart("Pupil support split into transportation and other student services", px, [
            {"name": "Transportation", "points": {int(r["fiscal_year"]): float(r["transportation_usd"]) for r in prow}},
            {"name": "Other student services", "points": {int(r["fiscal_year"]): float(r["other_student_services_usd"]) for r in prow}}],
            musd, musd1, projected=[int(r["fiscal_year"]) for r in prow if r["basis"].startswith("estimate")],
            projected_note="estimated split")
    if rc.exists():
        with open(rc) as f:
            rrow = list(csv.DictReader(f))
        c_routes = hbar_chart("Transportation route costs by service", [r["service"] for r in rrow],
                              [{"name": "Cost", "values": {r["service"]: float(r["usd"]) for r in rrow}}], musd1)
    fixed = []
    cs = src / "analysis" / "cost_structure_by_program.csv"
    if cs.exists():
        with open(cs) as f:
            fixed = list(csv.DictReader(f))
    short = lambda c: re.sub(r"\s*\(.*?\)", "", c).replace("Administration and district support", "Admin and district support")
    for r in fixed:
        r["category"] = short(r["category"])
    c_fixed = hbar_chart("Costs that changed with enrollment, FY2016 to FY2025", [r["category"] for r in fixed], [
        {"name": "FY2016, in 2025 dollars", "values": {r["category"]: float(r["fy2016_real_2025usd"]) for r in fixed}},
        {"name": "FY2025", "values": {r["category"]: float(r["fy2025_usd"]) for r in fixed}}], musd) if fixed else None

    # ---- KPIs
    peak = max(actual, key=lambda fy: P[fy]["adm"])
    adm_chg = P[last]["adm"] / P[peak]["adm"] - 1
    real_chg = P[last]["exp_per_adm_2024usd"] / P[first]["exp_per_adm_2024usd"] - 1
    kpis = "".join([
        kpi("Students", f"{adm_chg:+.0%}", f"{count(P[peak]['adm'])} in FY{peak} to {count(P[last]['adm'])} in FY{last}"),
        kpi("General fund spending", musd(P[last]["expenditure"]), f"FY{last}, up from {musd(P[first]['expenditure'])} in FY{first}"),
        kpi("Spending per student, after inflation", f"{real_chg:+.0%}",
            f"{usd(P[first]['exp_per_adm_2024usd'])} to {usd(P[last]['exp_per_adm_2024usd'])} (2024 dollars), FY{first} to FY{last}"),
        kpi("Unassigned balance", pct(P[last]["unassigned_pct_of_exp"]),
            f"of spending at the end of FY{last}; board policy floor is 8%"),
    ])
    if budget_yrs:
        b = budget_yrs[0]
        kpis += kpi(f"FY{b} budget gap", musd(P[b]["expenditure"] - P[b]["revenue"]),
                    f"spending {musd(P[b]['expenditure'])} against revenue {musd(P[b]['revenue'])}, covered with one-time money")

    built = datetime.date.today().isoformat()
    pages = {}

    # ---- overview
    _, short = sec(r"short version")
    _, openq = sec(r"open questions")
    pre = re.sub(r"\A\s*#\s+.*\n", "", secs[0][1])
    lede = ("How Minneapolis Public Schools' money has changed since FY2015: enrollment fell by a fifth, spending kept "
            "rising, federal pandemic aid filled the gap for a few years, and the district's own projections now show structural deficits.")
    body = [f"<h1>{SITE_TITLE}, FY{first}–FY{max(rv_years)}</h1>", f'<p class="lede">{lede}</p>',
            f'<div class="kpis">{kpis}</div>',
            "<h2 id=\"the-short-version\">The short version</h2>", md(short),
            chart_block(c_revexp, "Revenue and spending",
                        f"General fund, FY{first}–FY{last} audited. "
                        + (f"FY{budget_yrs[0]} is the proposed budget (dashed, hollow points). " if budget_yrs else "")
                        + "Hover or tap a year for exact figures."),
            chart_block(c_bal, "Ending balance",
                        "General fund balance at June 30. Only the unassigned part is free to cover shortfalls; the rest is "
                        "committed, assigned to planned uses, restricted or nonspendable."),
            chart_block(c_unas, "The operating cushion",
                        "Unassigned balance as a share of that year's actual spending, against the board's 8% floor. "
                        + (f"FY{budget_yrs[0]} is the budget's projection. " if budget_yrs else "")
                        + "The district's own reports divide by budgeted spending, so their percentages differ slightly."),
            chart_block(c_adm, "Enrollment", "Average daily membership counted for state aid, including pre-K."),
            chart_block(c_ppe, "Spending per student",
                        "General fund spending divided by ADM, as spent and in 2024 dollars (CPI-U). Nearly all of the "
                        "real increase came after FY2021."),
            "<h2 id=\"open-questions\">Open questions</h2>", md(openq),
            f'<h2 id="about">About these figures</h2>{md(pre)}']
    pages["index.html"] = ("Overview", SITE_TITLE, "\n".join(body))

    # ---- money
    t1, byyear = sec(r"by year")
    t2, where = sec(r"where the money goes")
    body = ["<h1>Money in and out</h1>",
            '<p class="lede">Where the general fund\'s money comes from, what it pays for, and how staffing moved against enrollment.</p>',
            chart_block(c_src, "Revenue by source",
                        "State aid is the bulk. Federal money roughly tripled in FY2022–2024 with pandemic relief, then fell back in FY2025."),
            *([chart_block(c_state, "State revenue by stream",
                "State revenue rose $90.5M from FY2023 to FY2025, of which about $11M is transportation. Special "
                "education aid added $38.3M, mostly $27.3M of cross-subsidy aid from a 2023 state law; transportation aid "
                "fell in FY2024 before rising in FY2025. All other state aid added $52.2M. Totals are audited; the special "
                "education split is the district's, from budget books for FY2015 to FY2018 (when it includes transportation) "
                "and not available for FY2019 to FY2022.")] if c_state else []),
            chart_block(c_staff, "Students and staff",
                        "Full-time equivalents and ADM, each indexed to FY2015. Staff grew while students fell, until FY2025's cuts."),
            ]
    if where:
        body += [f"<h2 id=\"{slug(t2)}\">{inline(t2, links)}</h2>", md(where)]
    if c_prog:
        body.append(chart_block(c_prog, "Spending by program",
                                f"General fund spending by program, FY{prog_years[0]} and FY{prog_years[-1]}."))
    if c_prog_years:
        y0, y1 = prog_all[0], prog_all[-1]
        body.append(chart_block(c_prog_years, "Spending by program, every year",
            f"FY{y0}–FY{y1}, not adjusted for inflation. Regular instruction includes vocational; the last group adds "
            "building operations, capital outlay, debt payments and small fixed-cost and community lines. FY2025's jump in "
            "special education and drop in regular instruction mostly reflects recoding, not a change in services."))
    if c_per:
        a, b = pp_years[0], pp_years[-1]
        body.append(chart_block(c_per, "Per student, after inflation",
            f"General fund spending per student (ADM) in 2024 dollars. Instruction itself (regular and special education) "
            f"barely moved: {usd(instr_pp[a])} per student in FY{a} and {usd(instr_pp[b])} in FY{b}. The growth is outside "
            "the classroom: pupil support (mostly transportation and student services) and central administration and "
            "district support. Instructional support covers curriculum, coaching, libraries and similar services."))
    if c_obj:
        cap = (f"Audited FY{obj_years[0]}–FY{obj_years[-1]}" + (f", and the FY{obj_proj[0]} adopted budget (hollow bar)" if obj_proj else "")
               + ". Salaries and benefits are about three quarters of spending. Purchased services (contracts) nearly doubled "
               "from FY2021 to FY2024. In the budget, capital and other is what is left after the four main types.")
        body.append(chart_block(c_obj, "Spending by type", cap))
    if c_sped:
        body.append(chart_block(c_sped, "Special education and regular instruction",
            "Audited through FY2025; FY2026 (amended) and FY2027 (adopted) special education budgets are dashed. In FY2025 "
            "special education rose $23M while regular instruction fell $14M: much of the jump is costs moved from one "
            "category to the other, not new spending."))
        body.append(chart_block(c_pupil, "Pupil support",
            "Transportation plus health, counseling, social work and attendance services. Audited through FY2025; "
            "FY2026 and FY2027 budgets are dashed."))
        body.append(md(
            "- **Pupil support is mostly buses.** It went from $64M (FY2021) to $118M (FY2024). The district's own "
            "transportation staff fell from 191 to 113 FTE between FY2016 and FY2025 while spending rose, so the growth is "
            "contracted bus and van service (inferred from staffing and spending moving in opposite directions). Special "
            "education transportation alone went from $28.4M (FY2022) to $45.5M (FY2025) on the district's figures. FY2024 "
            "came in $17.4M over budget, and the FY2025 audit's finding on payments above contract terms names transportation invoices.\n"
            "- **Special education was flat for a decade, then jumped.** Instruction spending stayed at about $115–121M from "
            "FY2015 to FY2024, a 25% drop after inflation, then reached $142M in FY2025, with $156M budgeted for FY2026 and $170M "
            "for FY2027. In FY2025 regular instruction fell $14M as special education rose $23M, and teachers and support staff "
            "both fell. That year the district found it had been recording special education staff and services as regular "
            "instruction and under-claiming state aid; correcting it recovers about $21.5M over FY2026–2027 (district figures). "
            "So most of the jump looks like the same people recorded in the right category, plus raises (inferred).\n"
            "- **State aid now covers more of it.** On the district's figures, special education costs rose $38M over three aid "
            "years while net state aid rose $63M, so the share MPS pays from general money fell from about $55M to $30M a year."))
    ps = src / "analysis" / "pupil_support_site.md"
    if ps.exists():
        ptext = sanitize(ps.read_text())
        ptitle = re.match(r"#\s+(.*)", ptext)
        ptext = re.sub(r"\A\s*#\s+.*\n", "", ptext)
        body.append(f'<h2 id="pupil-support-detail">{inline(ptitle.group(1), links) if ptitle else "Pupil support"}</h2>')
        body.append(md(ptext))
        if c_comp:
            body.append(chart_block(c_comp, "Transportation drove the rise",
                "FY2021 split from state figures quoted by the district; FY2024 and FY2025 splits are estimates from "
                "department budgets and the audited totals (hollow bars)."))
        if c_routes:
            body.append(chart_block(c_routes, "What the routes cost",
                "The district's transportation route costs by service, in its FY2024 budget ($60.3M), the only year with this breakdown. "
                "Special education and homeless, highly mobile and foster routes are 70% of the total."))
    if c_fixed:
        body.append(chart_block(c_fixed, "Costs that changed with enrollment",
            "Students (ADM) fell 18% from FY2016 to FY2025. Instruction and instructional support fell faster than that after "
            "inflation; administration, pupil support and other fixed costs rose. Roughly two-thirds of spending follows "
            "enrollment and one-third does not, so each lost student removes more revenue than cost. FY2016 is converted "
            "to 2025 dollars with the consumer price index."))
    if byyear:
        body += [f"<h2 id=\"{slug(t1)}\">{inline(t1, links)}</h2>", md(byyear)]
    pages["money.html"] = ("Money in and out", "Money in and out", "\n".join(body))

    # ---- enrollment
    ev_path = src / "enrollment" / "enrollment_vs_population.csv"
    body = ["<h1>Enrollment</h1>",
            '<p class="lede">District enrollment against the size of the city it serves, where the drop happened by grade, '
            'and what recovery could look like.</p>']
    if ev_path.exists():
        with open(ev_path) as f:
            ev = list(csv.DictReader(f))
        key = lambda r: int(r["fall_year"])
        adm = {key(r): num(r["mps_adm_total"]) for r in ev if num(r["mps_adm_total"])}
        oct1 = {key(r): num(r["mps_oct1_k12"]) for r in ev if num(r["mps_oct1_k12"])}
        pop = {key(r): num(r["mpls_pop_july1_fall_year"]) for r in ev if num(r["mpls_pop_july1_fall_year"])}
        b0 = min(set(adm) & set(pop))
        b1 = min(set(oct1) & set(pop))
        c_ev = line_chart("Enrollment and city population, indexed", [
            {"name": "City population", "points": {y: 100 * v / pop[b0] for y, v in pop.items()}},
            {"name": "Enrollment (ADM)", "points": {y: 100 * v / adm[b0] for y, v in adm.items() if y >= b0}},
            {"name": "Oct 1 K-12 count", "points": {y: 100 * v / oct1[b1] * adm[b1] / adm[b0]
                                                    for y, v in oct1.items()}}],
            lambda v: f"{v:.0f}", lambda v: f"{v:.1f}", ytitle=f"Size relative to fall {min(set(adm) & set(pop))} (= 100)")
        body.append(chart_block(c_ev, "Students and city residents",
                                f"Each series indexed to fall {b0} = 100 (the October count is scaled to the ADM index in fall {b1}, "
                                "the first year both exist). x-axis is the fall of each school year. Minneapolis' population was "
                                "roughly flat while district enrollment fell by about a fifth."))
        rows = "".join(f"<tr><td>{html.escape(r['school_year'])}</td><td class=num>{count(num(r['mps_adm_total'])) if num(r['mps_adm_total']) else '–'}</td>"
                       f"<td class=num>{count(num(r['mps_oct1_k12'])) if num(r['mps_oct1_k12']) else '–'}</td>"
                       f"<td>{html.escape(r['mps_oct1_k12_note'])}</td>"
                       f"<td class=num>{count(num(r['mpls_pop_july1_fall_year'])) if num(r['mpls_pop_july1_fall_year']) else '–'}</td></tr>"
                       for r in ev)
        by_year = ('<h2 id="by-year">By school year</h2><div class="table-wrap"><table><thead><tr><th>School year</th>'
                    '<th class=num>ADM</th><th class=num>Oct 1 K-12</th><th>Count basis</th><th class=num>City population (July 1)</th>'
                    f'</tr></thead><tbody>{rows}</tbody></table></div>')
        body += enrollment_grades(src, links, md)
        body.append(by_year)
    en_src = src / "enrollment" / "sources.md"
    if en_src.exists():
        for t, b in sections(sanitize(en_src.read_text())):
            if t and not re.search(r"not reachable|environment", t, re.I):
                body += [f"<h2 id=\"{slug(t)}\">{inline(t, links)}</h2>", md(b)]
    pages["enrollment.html"] = ("Enrollment", "Enrollment", "\n".join(body))

    # ---- governance (written by a separate research strand; optional)
    gov = src / "governance" / "governance.md"
    if gov.exists():
        gtext = sanitize(gov.read_text())
        title = re.match(r"#\s+(.*)", gtext)
        gtext = re.sub(r"\A\s*#\s+.*\n", "", gtext)
        gtext = gtext.replace("listed in [sources.md](sources.md)", "listed at the end of this page").replace("[sources.md](sources.md)", "the sources below")
        body = [f"<h1>{inline(title.group(1), links) if title else 'Governance'}</h1>",
                '<p class="lede">Who decides what at the district, and where budget decisions are made.</p>',
                md(gtext)]
        gsrc = src / "governance" / "sources.md"
        if gsrc.exists():
            st = re.sub(r"\A\s*#\s+.*\n", "", sanitize(gsrc.read_text()))
            gbody, glist = cite_links("\n".join(body), md(st))
            body = [gbody, '<h2 id="governance-sources">Sources</h2>', glist]
        pages["governance.html"] = ("Governance", "Governance", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "governance.html"]

    # ---- financial structure (a primer for readers used to business accounting; optional)
    fs = src / "analysis" / "financial_structure.md"
    if fs.exists():
        ftext = sanitize(fs.read_text())
        title = re.match(r"#\s+(.*)", ftext)
        ftext = re.sub(r"\A\s*#\s+.*\n", "", ftext)
        main_md, _, src_md = ftext.partition("\n## Sources\n")
        fbody, flist = cite_links(md(main_md), md(src_md))
        body = [f"<h1>{inline(title.group(1), links) if title else 'Financial structure'}</h1>", fbody]
        if src_md.strip():
            body += ['<h2 id="sources">Sources</h2>', flist]
        pages["structure.html"] = ("Financial structure", "Financial structure", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "structure.html"]

    # ---- unions and labor contracts (optional)
    us = src / "analysis" / "unions_site.md"
    udir = src / "unions"
    if us.exists() and (udir / "fte_by_bargaining_group.csv").exists():
        utext = sanitize(us.read_text())
        title = re.match(r"#\s+(.*)", utext)
        utext = re.sub(r"\A\s*#\s+.*\n", "", utext)
        main_md, _, src_md = utext.partition("\n## Sources\n")
        with open(udir / "fte_by_bargaining_group.csv") as f:
            frows = list(csv.DictReader(f))
        yrs = [int(c[2:]) for c in frows[0] if re.fullmatch(r"FY\d{4}", c)]
        PROF = ("AMP", "MAAS", "MACA", "Principals")
        grp = [("Teachers", lambda r: r["classification"] == "Teachers"),
               ("Support professionals (ESP)", lambda r: r["classification"] == "ESP"),
               ("Professional and admin", lambda r: r["classification"] in PROF),
               ("All other staff", lambda r: r["classification"] not in PROF + ("Teachers", "ESP"))]
        charts = {}
        c_staff = line_chart("District staff by bargaining group, FY2016 to FY2025", [
            {"name": n, "points": {y: sum(float(r[f"FY{y}"]) for r in frows if fn(r)) for y in yrs}} for n, fn in grp],
            count, y0=0, ytitle="full-time-equivalent staff")
        charts["staff"] = chart_block(c_staff, "Staff by bargaining group",
            "Full-time-equivalent employees by job classification, grouped by the bargaining group each classification falls in "
            "(grouping is ours). Annual financial report FY2025, Table 22 [4].")
        pay = {}
        for r in frows:
            g = r["bargaining_group_inferred"]
            if "Local 59" in g and "Adult" not in g:
                g = "Teachers" if "teachers" in g else "Support professionals"
            elif r["classification"] in ("AMP", "MAAS", "MACA"):
                g = "Professional and admin"
            elif r["classification"] == "Principals":
                g = "Principals Forum"
            elif g.startswith("SEIU"):
                g = "SEIU 284"
            elif g.startswith("Teamsters"):
                g = "Teamsters 320"
            elif g.startswith("Minneapolis Building Trades") or g.startswith("IBEW") or g == "Machinists":
                g = "Trades"
            elif g.startswith("AFSCME"):
                g = "AFSCME 56"
            elif "Adult" in g:
                g = "Adult education"
            else:
                g = "Not represented"
            pay[g] = pay.get(g, 0) + float(r["FY2025_est_salaries_plus_benefits"] or 0)
        cats = sorted(pay, key=lambda k: -pay[k])
        c_pay = hbar_chart("Estimated FY2025 salaries plus benefits by bargaining group", cats,
                           [{"name": "Salaries and benefits (estimate)", "values": pay}], musd,
                           xtitle="estimated FY2025 salaries plus benefits, all funds")
        charts["payroll"] = chart_block(c_pay, "Estimated payroll by bargaining group",
            f"Our estimate, about {musd(sum(pay.values()))} in all: FY2025 staff counts [4] times an assumed average pay per group, "
            "plus benefits at 36% of salaries. Assumptions for each row are in the data file.")
        fl = udir / "filings" / "teacher_union_flows.csv"
        irs = udir / "filings" / "irs990_by_org_year.csv"
        if fl.exists() and irs.exists():
            with open(fl) as f:
                flows = {r["flow"]: r for r in csv.DictReader(f)}
            with open(irs) as f:
                rev = {int(r["FYE"][:4]): float(r["revenue"]) * 1e3 for r in csv.DictReader(f) if r["org"] == "MFE Local 59"}
            fcol = lambda k: {y: float(flows[k][str(y)]) for y in range(2016, 2026) if flows[k].get(str(y))}
            c_l59 = line_chart("Local 59 revenue and grants from its state and national unions, 2016 to 2025", [
                {"name": "Local 59 revenue", "points": rev},
                {"name": "From Education Minnesota", "points": fcol("EdMN->Local59 grants (LM-2 Sch17)")},
                {"name": "From AFT and NEA", "points": {y: fcol("AFT->Local59").get(y, 0) + fcol("NEA->Local59").get(y, 0) for y in fcol("AFT->Local59")}}],
                musd, musd1, y0=0, ytitle="dollars per year")
            charts["local59"] = chart_block(c_l59, "Local 59 and its parent unions",
                "Local 59 revenue from its IRS Form 990 (fiscal years ending in August) [25]; payments from Education Minnesota, "
                "AFT and NEA from their federal LM-2 reports [26]. Report years differ slightly, so the lines align only roughly.")
            data_files += [fl, irs, udir / "filings" / "cfb_funds.csv"]
        data_files.append(udir / "fte_by_bargaining_group.csv")
        html_main = re.sub(r"<p>\{\{chart:(\w+)\}\}</p>|\{\{chart:(\w+)\}\}",
                           lambda m: "@@CHART:" + (m.group(1) or m.group(2)) + "@@", md(main_md))
        html_main = re.sub(r"@@CHART:(\w+)@@", lambda m: charts.get(m.group(1), ""), html_main)
        ubody, ulist = cite_links(html_main, md(src_md))
        body = [f"<h1>{inline(title.group(1), links) if title else 'Unions'}</h1>", ubody]
        if src_md.strip():
            body += ['<h2 id="sources">Sources</h2>', ulist]
        pages["unions.html"] = ("Unions", "Unions and labor contracts", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "unions.html"]

    # ---- meal funding: compensatory revenue, Title I, food service (optional)
    ms = src / "analysis" / "meals_site.md"
    mc, ml = src / "data" / "mde_compensatory_mps.csv", src / "data" / "meal_linked_revenue.csv"
    if ms.exists() and mc.exists() and ml.exists():
        mtext = sanitize(ms.read_text())
        title = re.match(r"#\s+(.*)", mtext)
        mtext = re.sub(r"\A\s*#\s+.*\n", "", mtext)
        main_md, _, src_md = mtext.partition("\n## Sources\n")
        with open(mc) as f:
            crows = {int(r["fiscal_year"]): r for r in csv.DictReader(f)}
        mlong = {}
        with open(ml) as f:
            for r in csv.DictReader(f):
                mlong.setdefault(r["metric"], {})[int(r["fiscal_year"])] = float(r["value"])
        mcol = lambda *ks: {y: v for k in ks for y, v in mlong.get(k, {}).items()}
        charts = {}
        c_comp = line_chart("MPS state compensatory education revenue, FY2013 to FY2028", [
            {"name": "Compensatory revenue", "points": {y: float(r["compensatory_revenue_usd"]) for y, r in crows.items()},
             "dashed_from": 2025}], musd, musd1, y0=0, ytitle="state compensatory revenue, dollars per fiscal year")
        charts["comp"] = chart_block(c_comp, "Compensatory revenue",
            "District totals from MDE's compensatory revenue reports [1]. Dashed after FY2025: FY2026 is preliminary, "
            "FY2027 and FY2028 are MDE estimates (FY2027 before its separate hold-harmless amount).")
        c_share = line_chart("Share of MPS students counted for compensatory revenue, FY2013 to FY2028", [
            {"name": "Counted share", "points": {y: 100 * float(r["adjusted_count"]) / float(r["enrollment"]) for y, r in crows.items()},
             "dashed_from": 2025}], pct, y0=0, ytitle="free-eligible plus half of reduced-price students, % of October 1 enrollment")
        charts["share"] = chart_block(c_share, "Share of students counted",
            "Free-eligible students plus half of reduced-price students, divided by enrollment, both from the October 1 count "
            "a year before the fiscal year [1]. From FY2027 only students found through direct certification count.")
        c_fed = line_chart("Federal Title I and child nutrition spending at MPS, FY2013 to FY2025", [
            {"name": "Title I", "points": mcol("title_i_part_a_expenditures", "title_i_84010_total_expenditures")},
            {"name": "Child nutrition", "points": mcol("child_nutrition_cluster_expenditures")}],
            musd, musd1, y0=0, ytitle="federal spending, dollars per fiscal year")
        charts["federal"] = chart_block(c_fed, "Title I and child nutrition",
            "Expenditures from the audited Schedule of Expenditures of Federal Awards [6][7]. Title I is Part A where the "
            "audit lists it separately, the full Title I line otherwise; FY2013 child nutrition is not available.")
        c_food = line_chart("MPS food service fund revenue, spending and general fund transfers, FY2014 to FY2025", [
            {"name": "Revenue", "points": mcol("food_service_rev_total")},
            {"name": "Spending", "points": mcol("food_service_expenditures")},
            {"name": "Transfer in from general fund", "points": mcol("food_service_transfer_in_from_gf")}],
            musd, musd1, y0=0, ytitle="food service fund, dollars per fiscal year")
        charts["food"] = chart_block(c_food, "Food service fund",
            "Actual revenue (federal, state, meal sales and other) and spending from the audited food service fund schedules; "
            "transfers from the general fund from the same reports [5].")
        data_files += [mc, ml]
        html_main = re.sub(r"<p>\{\{chart:(\w+)\}\}</p>|\{\{chart:(\w+)\}\}",
                           lambda m: "@@CHART:" + (m.group(1) or m.group(2)) + "@@", md(main_md))
        html_main = re.sub(r"@@CHART:(\w+)@@", lambda m: charts.get(m.group(1), ""), html_main)
        mbody, mlist = cite_links(html_main, md(src_md))
        body = [f"<h1>{inline(title.group(1), links) if title else 'Meal funding'}</h1>", mbody]
        if src_md.strip():
            body += ['<h2 id="sources">Sources</h2>', mlist]
        pages["meals.html"] = ("Meal funding", "Meal funding", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "meals.html"]

    # ---- tests, graduation and college paths (optional)
    os_ = src / "analysis" / "outcomes_site.md"
    od = src / "outcomes"
    if os_.exists() and (od / "outcomes_trends.csv").exists() and (od / "district_outcomes.csv").exists():
        otext = sanitize(os_.read_text())
        title = re.match(r"#\s+(.*)", otext)
        otext = re.sub(r"\A\s*#\s+.*\n", "", otext)
        main_md, _, src_md = otext.partition("\n## Sources\n")
        tr = {}
        with open(od / "outcomes_trends.csv") as f:
            for r in csv.DictReader(f):
                tr.setdefault(r["metric"], {}).setdefault(r["entity"], {})[int(r["year"])] = float(r["value"])
        with open(od / "district_outcomes.csv") as f:
            dist = list(csv.DictReader(f))
        ENT = {"Minneapolis": "var(--s2)", "St. Paul": "var(--s5)", "First-ring suburbs": "var(--s1)",
               "Second-ring suburbs": "var(--s3)", "Statewide": "var(--muted)"}

        def oseries(metric, names):
            return [{"name": n, "points": tr[metric][n], "color": ENT[n]} for n in names if n in tr.get(metric, {})]
        three, five = ["Minneapolis", "St. Paul", "Statewide"], list(ENT)
        ocharts = {}
        ocharts["math"] = chart_block(line_chart("Percent of students proficient in math, 2015 to 2026",
            oseries("mca_math_proficient_pct", three), pct, y0=0, ytitle="students meeting or exceeding standards, % of those tested",
            xname="Spring ", wide_mr=110), "Math proficiency, all grades tested",
            "All students in grades 3 to 8 and 11 [1]. 2015-2018 count the MCA only; from 2019 MTAS and Alternate MCA (alternate tests "
            "for some students with disabilities) are included (a small share of students tested). No tests in 2020; the line joins 2019 and 2021.")
        ocharts["reading"] = chart_block(line_chart("Percent of students proficient in reading, 2015 to 2026",
            oseries("mca_reading_proficient_pct", three), pct, y0=0, ytitle="students meeting or exceeding standards, % of those tested",
            xname="Spring ", wide_mr=110), "Reading proficiency, all grades tested",
            "Grades 3 to 8 and 10 [1]. 2015-2018 count the MCA only; from 2019 MTAS and Alternate MCA are included. "
            "2026 is the first year of a new reading test (MCA-IV), so the last step partly reflects the new test.")
        if "mca_science_proficient_pct" in tr:
            ocharts["science"] = chart_block(line_chart("Percent of students proficient in science, 2015 to 2026",
                oseries("mca_science_proficient_pct", three), pct, y0=0, ytitle="students meeting or exceeding standards, % of those tested",
                xname="Spring ", wide_mr=110), "Science proficiency, grades 5, 8 and high school",
                "Science is tested in grades 5 and 8 and once in high school [1]. 2015-2018 count the MCA only; from 2019 MTAS and "
                "Alternate MCA are included. A new science test started in 2025, so 2025 and 2026 are not comparable with earlier years.")
        ocharts["act_took"] = chart_block(line_chart("Percent of graduates who took the ACT before graduating, classes of 2008 to 2024",
            oseries("act_took_before_graduating_pct", three), pct, y0=0, ytitle="took the ACT before graduating, % of high school graduates",
            xname="Class of ", wide_mr=110), "Who takes the ACT",
            "Graduates who took the ACT at any point before graduating [4]. Minnesota paid for every junior to take it for the classes "
            "of about 2016 to 2020; the pandemic and the end of that program cut participation.")
        ocharts["act_score"] = chart_block(line_chart("Average ACT composite score of graduates who took it, classes of 2008 to 2024",
            oseries("act_mean_composite", three), lambda v: f"{v:.1f}", y0=15, ytitle="average ACT composite score (scale 1 to 36), graduates who took it",
            xname="Class of ", wide_mr=110), "Average ACT score",
            "Average ACT composite score of graduates who took it, as SLEDS reports it [4]. When fewer students take the test, the takers are a more "
            "selected group, so averages after 2020 are not comparable with earlier years.")
        ocharts["grad"] = chart_block(line_chart("Four-year graduation rate, classes of 2015 to 2025",
            oseries("four_year_grad_rate_pct", three), pct, y0=0, ytitle="graduated within four years, % of the ninth-grade cohort",
            xname="Class of ", wide_mr=110), "Four-year graduation rate",
            "All students, each district's four-year cohort [2].")
        ocharts["four"] = chart_block(line_chart("Percent of graduates starting at a four-year college, classes of 2010 to 2024",
            oseries("four_year_college_pct", five), pct, y0=0, ytitle="started at a four-year college the fall after graduating, % of graduates",
            xname="Class of ", wide_mr=150), "Starting at a four-year college",
            "Any four-year college, in Minnesota or another state, as a share of all high school graduates [3]. "
            "Suburb groups are ours, graduate-weighted (<em>estimate</em>).")
        ocharts["two"] = chart_block(line_chart("Percent of graduates starting at a two-year or technical college, classes of 2010 to 2024",
            oseries("two_year_college_pct", five), pct, y0=0, ytitle="started at a two-year or shorter college the fall after graduating, % of graduates",
            xname="Class of ", wide_mr=150), "Starting at a two-year or technical college",
            "Public and private two-year colleges in Minnesota, plus two-year and shorter programs in other states [3]. "
            "Suburb groups are ours, graduate-weighted (<em>estimate</em>).")
        ocharts["bach"] = chart_block(line_chart("Percent of graduates who have earned a bachelor's degree, classes of 2007 to 2018",
            oseries("bachelors_earned_pct", five), pct, y0=0, ytitle="earned a bachelor's degree by SLEDS's latest data year, % of graduates",
            xname="Class of ", wide_mr=150), "Bachelor's degrees earned",
            "Degrees from any college SLEDS tracks, counted at its latest data year, so later classes have had less time [3]. "
            "Suburb groups are ours, graduate-weighted (<em>estimate</em>).")
        SHORT = {"West St. Paul-Mendota Heights-Eagan": "West St. Paul-MH-E", "St. Anthony-New Brighton": "St. Anthony-NB",
                 "North St. Paul-Maplewood-Oakdale": "N. St. Paul-M-O", "Rosemount-Apple Valley-Eagan": "Rosemount-AV-E",
                 "Burnsville-Eagan-Savage": "Burnsville-E-S", "South Washington County": "South Washington"}
        metro = sorted((r for r in dist if r["group"] != "other" and num(r["four_year_college_2022_2024_pct"]) is not None),
                       key=lambda r: -num(r["four_year_college_2022_2024_pct"]))
        nm = lambda r: SHORT.get(r["district"], r["district"])
        cats = [nm(r) for r in metro]
        mfig = dot_chart("Four-year college start and bachelor's degrees, Minneapolis, St. Paul and the suburbs", cats, [
            {"name": "Started at a four-year college, classes 2022-24", "color": "var(--s1)",
             "values": {nm(r): num(r["four_year_college_2022_2024_pct"]) for r in metro}},
            {"name": "Earned a bachelor's degree, classes 2015-17", "color": "var(--s4)",
             "values": {nm(r): num(r["bachelors_earned_2015_2017_pct"]) for r in metro if num(r["bachelors_earned_2015_2017_pct"]) is not None}}],
            pct, notes={nm(r): ("Minneapolis" if r["group"] == "Minneapolis" else "St. Paul") for r in metro if r["group"] in ("Minneapolis", "St. Paul")},
            xtitle="% of each district's high school graduates", ml_wide=170, ml_narrow=118)
        ring_rows = "".join(f"<tr><td>{html.escape(r['district'])}</td><td>{html.escape(r['group'])}</td></tr>"
                            for r in sorted(metro, key=lambda r: (r["group"], r["district"])))
        ocharts["metro"] = (chart_block(mfig, "Metro districts, highest four-year start first",
            "Each row is a district; shaded rows are Minneapolis and St. Paul. Both measures are our graduate-weighted averages over "
            "three classes (<em>estimate</em>) [3].")
            .replace("</figure>", '<details class="table-view"><summary>Which districts are in each ring (our grouping)</summary>'
                     f'<div class="table-wrap"><table><thead><tr><th>District</th><th>Group</th></tr></thead><tbody>{ring_rows}</tbody></table></div></details></figure>'))
        # maps: statewide and metro close-up for math and four-year college
        with open(od / "districts_simplified.geojson") as f:
            dfc = json.load(f)
        metro_ids = {r["mde_id"] for r in dist if r["group"] != "other"}
        mfc = {"features": [f for f in dfc["features"] if f["properties"]["mde_id"] in metro_ids]}
        Ps, hs = map_projection([dfc], 420)
        Pm, hm = map_projection([mfc], 420)
        lbl = [("0001-03", "Minneapolis"), ("0625-01", "St. Paul")]
        mblocks = []
        for key, mtitle, cuts, desc, minn in (
                ("math_2025_pct_proficient", "Math proficiency, 2025", [20, 30, 40, 50, 60],
                 "students proficient in math, % of those tested", "math_2025_tested"),
                ("four_year_college_2022_2024_pct", "Starting at a four-year college, classes 2022-24", [25, 35, 45, 55, 65],
                 "graduates starting at a four-year college (classes 2022-24 pooled, our estimate)", "graduates_2022_2024")):
            vals = {r["mde_id"]: num(r[key]) for r in dist if num(r[key]) is not None and (num(r[minn]) or 0) >= 30}
            fmt = lambda v, d=desc: f"{v:.1f}%: {d}"
            pair = [f"<div><h4>Minnesota</h4>{district_map(mtitle, dfc, vals, Ps, 420, hs, cuts, fmt)}</div>",
                    f"<div><h4>Twin Cities close-up</h4>{district_map(mtitle + ', metro', mfc, vals, Pm, 420, hm, cuts, fmt, labels=lbl)}</div>"]
            mblocks.append(f'<figure class="viz"><figcaption><h3 id="{slug(mtitle)}">{html.escape(mtitle)}</h3>'
                           f"<p>Shading is the {html.escape(desc)}. Grey districts have fewer than 30 students tested or graduating, "
                           "or no figure. The close-up shows Minneapolis, St. Paul and our first- and second-ring suburbs [1][3][6].</p></figcaption>"
                           f'{district_legend(cuts, lambda v: f"{v}%")}<div class="chart-box map-pair">{"".join(pair)}</div></figure>')
        ocharts["maps"] = "".join(mblocks)
        # district decisions: three scatters against 2025 math
        sty = {"other": ("Other Minnesota districts", "var(--muted)", 2.6), "second ring": ("Second-ring suburbs (our grouping)", "var(--s3)", 3.6),
               "first ring": ("First-ring suburbs (our grouping)", "var(--s1)", 3.6), "St. Paul": ("St. Paul", "var(--s5)", 6),
               "Minneapolis": ("Minneapolis", "var(--s2)", 6)}
        sblocks = []
        for col, xtitle, xfmt, head, cap in (
                ("spend_per_adm_served_fy2025", "spending per student, FY2025 ($ per average daily membership served)",
                 lambda v: f"${v / 1000:.0f}k", "Spending per student",
                 "Report Card total spending per student, FY2025 [5]. Districts above $40,000 per student are left out."),
                ("students_per_licensed_teacher_2025", "students per licensed teacher (Oct. 1 students / teacher FTE), 2024-25",
                 lambda v: f"{v:g}", "Students per licensed teacher", "October 1 enrollment divided by licensed teacher full-time equivalents [5]."),
                ("pct_educators_over_3yrs_experience_2025", "educators with more than three years' experience, %, 2024-25",
                 lambda v: f"{v:.0f}%", "Educator experience", "Share of licensed educators with more than three years of experience [5].")):
            groups = []
            for gk, (gname, gcol, gr) in sty.items():
                pts_ = []
                for r in dist:
                    x, y, n = num(r[col]), num(r["math_2025_pct_proficient"]), num(r["math_2025_tested"])
                    if r["group"] != gk or x is None or y is None or (n or 0) < 100:
                        continue
                    if col.startswith("spend") and x > 40000:
                        continue
                    pts_.append((x, y, f"{r['district']}\n{head}: {xfmt(x) if not col.startswith('spend') else usd(x)}\nMath proficient 2025: {y:.1f}%"))
                groups.append({"name": gname, "color": gcol, "r": gr, "points": pts_, "label": gk in ("Minneapolis", "St. Paul")})
            sf_ = scatter_chart(f"{head} and 2025 math proficiency, every district with 100 or more students tested", groups, xfmt, pct,
                                xtitle, "students proficient in math, 2025, % of those tested",
                                tipx=usd if col.startswith("spend") else xfmt)
            sblocks.append(chart_block(sf_, head, "Each dot is a district with 100 or more students tested. " + cap +
                                       " The dashed line is our straight-line fit (<em>estimate</em>); it shows association, not cause."))
        ocharts["choices"] = "".join(sblocks)
        data_files += [od / "district_outcomes.csv", od / "outcomes_trends.csv"]
        html_main = re.sub(r"<p>\{\{chart:(\w+)\}\}</p>|\{\{chart:(\w+)\}\}",
                           lambda m: "@@CHART:" + (m.group(1) or m.group(2)) + "@@", md(main_md))
        html_main = re.sub(r"@@CHART:(\w+)@@", lambda m: ocharts.get(m.group(1), ""), html_main)
        obody, olist = cite_links(html_main, md(src_md), prefix="osrc")
        body = [f"<h1>{inline(title.group(1), links) if title else 'Tests and college'}</h1>", obody]
        if src_md.strip():
            body += ['<h2 id="sources">Sources</h2>', olist]
        pages["outcomes.html"] = ("Tests and college", "Tests, graduation and college", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "outcomes.html"]

    # ---- school geography: attendance areas and buildings (optional)
    ga = src / "analysis" / "geography_site.md"
    gdir = src / "geography"
    if ga.exists() and (gdir / "attendance_areas_2025_26.geojson").exists():
        gtext = sanitize(ga.read_text())
        title = re.match(r"#\s+(.*)", gtext)
        gtext = re.sub(r"\A\s*#\s+.*\n", "", gtext)
        main_md, _, src_md = gtext.partition("\n## Sources\n")
        load = lambda n: json.loads((gdir / n).read_text())
        elem, mid, high = load("attendance_areas_2025_26.geojson"), load("middle_areas_2025_26.geojson"), load("high_areas_2025_26.geojson")
        old, old_high = load("attendance_areas_2020_21.geojson"), load("high_areas_2020_21.geojson")
        if (gdir / "landmarks.geojson").exists():
            LANDMARKS.update(load("landmarks.geojson"))
        with open(gdir / "school_locations.csv") as f:
            schools = list(csv.DictReader(f))
        with open(gdir / "charter_locations.csv") as f:
            charters = list(csv.DictReader(f))
        for n in ("attendance_area_summary.csv", "school_locations.csv", "charter_locations.csv"):
            data_files.append(gdir / n)
        dl = lambda n: f'<a href="data/{n}" download>{n}</a>'
        gcharts = {}
        P, h = map_projection([elem], 560)
        svg = area_map("Elementary attendance areas colored by how full their community school is", elem, P, 560, h,
                       schools=[s for s in schools if s["level"] == "elementary"], charters=charters)
        trs = "".join(
            f"<tr><td>{html.escape(p['area'])}</td><td>{html.escape(', '.join(p['buildings']))}</td>"
            f"<td>{html.escape(p['middle'])}</td><td>{html.escape(p['high'])}</td><td class=num>{p['sq_mi']:.1f}</td>"
            f"<td class=num>{int(p['capacity']):,}</td><td class=num>{int(p['students']):,}</td><td class=num>{p['utilization_pct']:.0f}%</td></tr>"
            for p in sorted((f["properties"] for f in elem["features"]), key=lambda p: p["utilization_pct"]))
        mag = "".join(
            f"<tr><td>{html.escape(s['short_name'])}</td><td>{ROLE_NAME[s['role']]}</td><td>{html.escape(s['grades'])}</td>"
            f"<td class=num>{int(float(s['capacity'])):,}</td><td class=num>{int(float(s['students'])):,}</td><td class=num>{float(s['utilization_pct']):.0f}%</td></tr>"
            for s in sorted((s for s in schools if s["role"] != "community"), key=lambda s: (s["level"], float(s["utilization_pct"]))))
        etable = ('<details class="table-view"><summary>Show each attendance area</summary><div class="table-wrap"><table>'
                  "<thead><tr><th>Area</th><th>Community school</th><th>Middle</th><th>High</th><th class=num>Sq mi</th>"
                  f"<th class=num>Seats</th><th class=num>Students</th><th class=num>Full</th></tr></thead><tbody>{trs}</tbody></table></div></details>"
                  '<details class="table-view"><summary>Show schools without an attendance area</summary><div class="table-wrap"><table>'
                  "<thead><tr><th>School</th><th>Type</th><th>Grades</th><th class=num>Seats</th><th class=num>Students</th>"
                  f"<th class=num>Full</th></tr></thead><tbody>{mag}</tbody></table></div></details>")
        gcharts["elementary"] = (
            '<figure class="viz"><figcaption><h3 id="elementary-map">Elementary attendance areas and how full their schools are</h3>'
            "<p>Each area is shaded by how full its community school building was in 2024-25 (students as a share of seats; where two buildings "
            "split the grades, both are combined). Markers are school buildings, shaded the same way. Diamonds are citywide magnets, which have no "
            "area of their own. Gray dots are charter school sites. Hover or tap for numbers. Boundaries are the state's 2025-26 file; seats and "
            f"students are the district's 2024-25 building table [1][2][3]. Data: {dl('attendance_area_summary.csv')}, {dl('school_locations.csv')}, "
            f"{dl('charter_locations.csv')}.</p></figcaption>{map_legend(charters=True)}"
            f'<div class="chart-box">{svg.replace(chr(60) + "svg class=" + chr(34) + "map" + chr(34), chr(60) + "svg class=" + chr(34) + "map big" + chr(34), 1)}</div>{etable}</figure>')
        P2, h2 = map_projection([mid], 420)
        pair = []
        for lvl, fc in (("Middle school areas", mid), ("High school areas", high)):
            key = "middle" if fc is mid else "high"
            pair.append(f"<div><h4>{lvl}</h4>" + area_map(lvl, fc, P2, 420, h2, schools=[s for s in schools if s["level"] == key],
                                                           lbl_cls="maplbl-big") + "</div>")
        gcharts["middle_high"] = (
            '<figure class="viz"><figcaption><h3 id="middle-high-maps">Middle and high school areas</h3>'
            "<p>Each middle and high school area is a set of whole elementary areas, so a home address points to one school at each level. "
            "Shading is how full the area's community building was in 2024-25. Franklin (middle) and FAIR (high) are citywide magnets; "
            f"squares are specialty and alternative sites [1][2][3].</p></figcaption>{map_legend()}"
            f'<div class="chart-box map-pair">{"".join(pair)}</div></figure>')
        with open(gdir / "seats_by_high_school_area.csv") as f:
            hrow = sorted(csv.DictReader(f), key=lambda r: float(r["utilization_pct"]))
        data_files.append(gdir / "seats_by_high_school_area.csv")
        hnames = [f'{r["high_school_area"]} ({float(r["utilization_pct"]):.0f}% full)' for r in hrow]
        c_hs = hbar_chart("Seats and students in the buildings inside each high school area, 2024-25", hnames, [
            {"name": "Seats", "values": {n: float(r["capacity"]) for n, r in zip(hnames, hrow)}},
            {"name": "Students", "values": {n: float(r["students"]) for n, r in zip(hnames, hrow)}}], count)
        gcharts["by_high"] = chart_block(c_hs, "Seats and students by part of the city",
            "All school buildings that sit inside each high school area, at every grade level and including magnets, counted where the building is, "
            f"not where the students live. 2024-25 seats and students from the district building table [1]; areas from the state file [2]. Data: {dl('seats_by_high_school_area.csv')}.")
        chg_h, chg_e = load("change_high_2021.geojson"), load("change_elementary_2021.geojson")
        mi = lambda fc, c: sum(f["properties"]["sq_mi"] for f in fc["features"] if f["properties"]["changed"] == c)
        city_mi = mi(chg_h, True) + mi(chg_h, False)
        P3, h3 = map_projection([old, elem], 420)
        pair = [f"<div><h4>{t}</h4>" + change_map(t, fc, P3, 420, h3, new_lines, old_lines, labels, "h" if fc is chg_h else "e") + "</div>"
                for t, fc, new_lines, old_lines, labels in (
                    (f"High school: {100 * mi(chg_h, True) / city_mi:.0f}% of the city changed", chg_h, high, old_high, True),
                    (f"Elementary school: {100 * mi(chg_e, True) / city_mi:.0f}% of the city changed", chg_e, elem, old, False))]
        gcharts["before_after"] = (
            '<figure class="viz"><figcaption><h3 id="before-after-maps">What the 2021 redesign changed</h3>'
            "<p>Each map lays the 2025-26 attendance areas (solid lines) over the 2020-21 ones (dashed lines). Orange land was assigned to a different "
            "school after the redesign; gray land kept the same school. Labels on the high school map read old school, then new. "
            "Henry High was renamed Camden High in 2024 and counts as the same school [14]. Hover or tap an orange piece to see an arrow from the "
            "building of its old school to the building of its new one. Arrows use today's building locations; Jefferson is drawn at its building, "
            "which now houses Ella Baker, and where an old or new area had two campuses the nearer one is used. "
            "Shares are of land area, not of students; slivers under 0.02 square miles are treated as unchanged. State attendance area files for each "
            f"school year [2][4]; our overlay. Data: {dl('change_high_2021.geojson')}, {dl('change_elementary_2021.geojson')}.</p></figcaption>"
            '<ul class="legend"><li><span class="swatch chg"></span>Assigned to a different school</li>'
            '<li><span class="swatch same"></span>Same school</li>'
            '<li><svg class="key" viewBox="0 0 16 16" aria-hidden="true"><line class="new-line" x1="0" y1="8" x2="16" y2="8"/></svg>2025-26 boundary</li>'
            '<li><svg class="key" viewBox="0 0 16 16" aria-hidden="true"><line class="old-line" x1="0" y1="8" x2="16" y2="8"/></svg>2020-21 boundary</li>' + LANDMARK_KEY + '</ul>'
            f'<div class="chart-box map-pair">{"".join(pair)}</div></figure>')
        for n in ("change_high_2021.geojson", "change_elementary_2021.geojson"):
            data_files.append(gdir / n)
        html_main = re.sub(r"<p>\{\{chart:(\w+)\}\}</p>|\{\{chart:(\w+)\}\}",
                           lambda m: "@@CHART:" + (m.group(1) or m.group(2)) + "@@", md(main_md))
        html_main = re.sub(r"@@CHART:(\w+)@@", lambda m: gcharts.get(m.group(1), ""), html_main)
        gbody, glist = cite_links(html_main, md(src_md))
        body = [f"<h1>{inline(title.group(1), links) if title else 'School geography'}</h1>", gbody]
        if src_md.strip():
            body += ['<h2 id="sources">Sources</h2>', glist]
        pages["geography.html"] = ("School geography", "School geography", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "geography.html"]

    # ---- buildings and closures (optional)
    fa = src / "analysis" / "facilities_site.md"
    if fa.exists():
        atext = sanitize(fa.read_text())
        title = re.match(r"#\s+(.*)", atext)
        atext = re.sub(r"\A\s*#\s+.*\n", "", atext)
        main_md, _, src_md = atext.partition("\n## Sources\n")
        charts = {}
        bf = src / "facilities" / "buildings_fy2025.csv"
        if bf.exists():
            with open(bf) as f:
                brow = [r for r in csv.DictReader(f) if r["utilization_pct"]]
            lv = [("Elementary", "Elementary Schools"), ("Middle and K-8", "Middle Schools"), ("High", "High Schools")]
            seats = {n: sum(float(r["capacity"]) for r in brow if r["category"] == c) for n, c in lv}
            kids = {n: sum(float(r["enrollment_incl_programs"]) for r in brow if r["category"] == c) for n, c in lv}
            c_util = hbar_chart("Seats and students by school level, 2024-25", [n for n, _ in lv], [
                {"name": "Seats", "values": seats}, {"name": "Students", "values": kids}], count)
            charts["utilization"] = chart_block(c_util, "Seats and students, 2024-25",
                f"Capacity and enrollment of the {len(brow)} active school buildings, by level. Overall they are 64% full. "
                "District building table in the FY2025 annual financial report [1].")
            bands = [("Under 50%", 0, 50), ("50-60%", 50, 60), ("60-70%", 60, 70), ("70-85%", 70, 85), ("85% or more", 85, 999)]
            cnt = {n: sum(1 for r in brow if lo <= float(r["utilization_pct"]) < hi) for n, lo, hi in bands}
            c_full = hbar_chart("School buildings by how full they are, 2024-25", [n for n, _, _ in bands],
                                [{"name": "Buildings", "values": cnt}], count)
            # per-building table: how each facility rolls up into the two charts
            with open(bf) as f:
                allrow = list(csv.DictReader(f))
            lvname = dict((c, n) for n, c in lv)
            band_of = lambda u: next(n for n, lo, hi in bands if lo <= u < hi)
            host, out_rows = None, []
            for r in allrow:
                note = r["square_feet_note"]
                if r["program_in_building"] == "False":
                    host = r
                if r["utilization_pct"]:
                    lvl, band, why = lvname[r["category"]], band_of(float(r["utilization_pct"])), ""
                elif r["program_in_building"] == "True" and host["utilization_pct"]:
                    lvl, band, why = "", "", f"program housed in {host['facility']}; its students are counted in that building"
                elif r["program_in_building"] == "True":
                    lvl, band, why = "", "", f"program housed in {host['facility']}, which is not a regular K-12 school building"
                elif "online" in r["facility"].lower():
                    lvl, band, why = "", "", "online school, no building"
                elif "vacant" in r["facility"].lower():
                    lvl, band, why = "", "", "vacant building"
                elif r["category"] in ("Administrative", "Other"):
                    lvl, band, why = "", "", "not a regular K-12 school building"
                else:
                    lvl, band, why = "", "", "no capacity listed in the district table"
                out_rows.append({"facility": r["facility"], "district_category": r["category"],
                                 "chart_level": lvl, "fullness_band": band, "in_charts": "yes" if lvl else "no",
                                 "why_not_in_charts": why, "constructed": r["constructed"], "square_feet": r["square_feet"],
                                 "capacity": r["capacity"], "enrollment_2024_25": r["enrollment_2024_25"],
                                 "enrollment_incl_programs": r["enrollment_incl_programs"], "utilization_pct": r["utilization_pct"],
                                 "square_feet_note": note,
                                 "source": "MPS FY2025 ACFR, Table 25 Building Information: https://resources.finalsite.net/images/v1779805179/mplsk12mnus/elwrwxqjnvk3prui1aav/FinancialStatement2025.pdf"})
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(out_rows[0]), lineterminator="\n")
            w.writeheader(); w.writerows(out_rows)
            derived = Path(tempfile.mkdtemp()) / "building_utilization_fy2025.csv"
            derived.write_text(buf.getvalue())
            data_files.append(derived)
            dl = '<a href="data/building_utilization_fy2025.csv" download>building_utilization_fy2025.csv</a>'
            charts["utilization"] = charts["utilization"].replace("</p></figcaption>",
                f" Every building, with the bar it counts in, is in {dl} and listed under the next chart.</p></figcaption>", 1)
            trs = "".join(
                f"<tr><td>{html.escape(r['facility'])}</td><td>{html.escape(r['chart_level'] or '–')}</td>"
                f"<td>{html.escape(r['fullness_band'] or '–')}</td><td class=num>{r['capacity'] or '–'}</td>"
                f"<td class=num>{r['enrollment_incl_programs'] or r['enrollment_2024_25'] or '–'}</td>"
                f"<td class=num>{(r['utilization_pct'] + '%') if r['utilization_pct'] else '–'}</td>"
                f"<td>{html.escape(r['why_not_in_charts'])}</td></tr>"
                for r in sorted(out_rows, key=lambda r: (r["in_charts"] != "yes", float(r["utilization_pct"] or 0))))
            per_bldg = ('<details class="table-view"><summary>Show each building</summary><div class="table-wrap"><table>'
                        "<thead><tr><th>Building</th><th>Level bar</th><th>Fullness bar</th><th class=num>Seats</th>"
                        "<th class=num>Students</th><th class=num>Full</th><th>Not charted because</th></tr></thead>"
                        f"<tbody>{trs}</tbody></table></div></details>")
            charts["fullness"] = chart_block(c_full, "How full each school is",
                "Number of school buildings by enrollment as a share of capacity, 2024-25. Programs housed in a building count toward it [1]. "
                f"Per-building data: {dl}.").replace("</figure>", per_bldg + "</figure>", 1)
        for extra in ("facilities_spending_series.csv", "school_levy_history.csv", "debt_service_projection.csv"):
            if (src / "facilities" / extra).exists():
                data_files.append(src / "facilities" / extra)
        fsx = src / "facilities" / "facilities_spending_series.csv"
        if fsx.exists():
            with open(fsx) as f:
                frow = list(csv.DictReader(f))
            fcol = lambda fn: {int(r["fiscal_year"]): fn(r) for r in frow}
            fnum2 = lambda r, k: float(r[k]) if r.get(k) else None
            c_fsp = line_chart("Building-related spending by fund", [
                {"name": "Debt service", "points": fcol(lambda r: float(r["govfunds_debt_principal"]) + float(r["govfunds_debt_interest"]))},
                {"name": "Projects, total", "points": {k: v for k, v in fcol(lambda r: fnum2(r, "building_construction_fund_total_approx")).items() if v}},
                {"name": "Projects, expensed", "points": {k: v for k, v in fcol(lambda r: fnum2(r, "building_construction_fund_current")).items() if v}},
                {"name": "General Fund", "points": fcol(lambda r: float(r["gf_sites_buildings_current"]))}],
                musd, musd1, y0=0)
            charts["spending"] = chart_block(c_fsp, "Building spending, FY2016 to FY2025",
                "General Fund: running buildings from the operating budget. Projects: the Building Construction fund, paid from bonds and the maintenance levy; "
                "\"expensed\" is project work booked as ordinary spending, and \"total\" adds capitalized projects (approximate: all-funds capital outlay "
                "minus the General Fund's). Building operating spending across all funds is General Fund plus Projects, expensed "
                "(FY2025: $39.9M + $57.6M = $97.6M). Not adjusted for inflation. Debt service is all funds and "
                "includes some non-building items such as leases and software subscriptions. Annual financial reports, governmental funds statements and statistical Table 3 [1].")
        lv_f = src / "facilities" / "school_levy_history.csv"
        if lv_f.exists():
            with open(lv_f) as f:
                lrow = list(csv.DictReader(f))
            ly = [int(r["taxes_payable_year"]) for r in lrow]
            lcol = lambda fn: {int(r["taxes_payable_year"]): fn(r) for r in lrow}
            c_levy = bar_chart("School property tax levy by purpose, taxes payable 2016 to 2026", ly, [
                {"name": "Building debt", "points": lcol(lambda r: float(r["debt_service_levy"]))},
                {"name": "Operating and community service", "points": lcol(lambda r: float(r["general_fund_levy"]) + float(r["community_service_levy"]))}],
                musd, musd1)
            charts["levy"] = chart_block(c_levy, "School levy by purpose",
                "Certified school levy by the year the taxes are paid. The operating part includes the voter-approved referendums and technology levy. "
                "Annual financial report Tables 10-11 [1]; 2026 from the state certification [14].")
        dp_f = src / "facilities" / "debt_service_projection.csv"
        if dp_f.exists():
            with open(dp_f) as f:
                drow = [r for r in csv.DictReader(f) if int(r["fiscal_year"]) >= 2027]
            dcol = lambda k: {int(r["fiscal_year"]): float(r[k]) * 1e6 for r in drow}
            c_debt = line_chart("Projected debt service on bonds and certificates, FY2027 to FY2040 (estimate)", [
                {"name": "Keep borrowing about $80M a year", "points": dcol("scenario_A_80M_new_per_year_M")},
                {"name": "Borrow only for maintenance, about $38M a year", "points": dcol("scenario_B_38M_LTFM_only_per_year_M")},
                {"name": "No new borrowing after Dec 2025", "points": dcol("plus_dec2025_issues_M")}],
                musd, musd1, y0=0)
            charts["debt"] = chart_block(c_debt, "Debt payments under three borrowing paths (estimate)",
                "Our projection, not the district's. Existing payments are from the FY2025 annual financial report, Note 5, with its 5-year bands "
                "spread evenly, so the steps in FY2031 and FY2036 are artifacts. New bonds are assumed to be 20-year at 5% [1].")
        html_main = re.sub(r"<p>\{\{chart:(\w+)\}\}</p>|\{\{chart:(\w+)\}\}",
                           lambda m: "@@CHART:" + (m.group(1) or m.group(2)) + "@@", md(main_md))
        html_main = re.sub(r"@@CHART:(\w+)@@", lambda m: charts.get(m.group(1), ""), html_main)
        abody, alist = cite_links(html_main, md(src_md))
        body = [f"<h1>{inline(title.group(1), links) if title else 'Buildings'}</h1>", abody]
        if src_md.strip():
            body += ['<h2 id="sources">Sources</h2>', alist]
        pages["facilities.html"] = ("Buildings", "Buildings", "\n".join(body))
    else:
        NAV[:] = [n for n in NAV if n[0] != "facilities.html"]

    # ---- sources
    t3, found = sec(r"sources found")
    t4, missing = sec(r"still missing")
    urls = {}
    for p in data_files:
        with open(p) as f:
            for r in csv.DictReader(f):
                text = " ".join(v for k, v in r.items() if k and "source" in k and v)
                for u in re.findall(r"https?://[^\s,;)\"]+", text):
                    u = u.rstrip(".")
                    urls.setdefault(u, set()).add(p.name)
    url_rows = "".join(f'<tr><td><a href="{html.escape(u)}">{short_url(u)}</a></td><td>{", ".join(sorted(fs))}</td></tr>'
                       for u, fs in sorted(urls.items()))
    body = ["<h1>Sources and gaps</h1>",
            '<p class="lede">What the figures come from and what is still missing.</p>']
    if found:
        body += [f"<h2 id=\"{slug(t3)}\">{inline(t3, links)}</h2>", md(found)]
    if missing:
        body += [f"<h2 id=\"{slug(t4)}\">{inline(t4, links)}</h2>", md(missing)]
    body += ['<h2 id="every-document">Every document cited in the data</h2>',
             f'<p class="note">{len(urls)} documents, with the data files that cite them.</p>',
             f'<div class="table-wrap"><table><thead><tr><th>Document</th><th>Cited in</th></tr></thead><tbody>{url_rows}</tbody></table></div>']
    pages["sources.html"] = ("Sources and gaps", "Sources and gaps", "\n".join(body))

    # ---- data
    (out / "data").mkdir(parents=True, exist_ok=True)
    rows = []
    for p in data_files:
        text = sanitize(p.read_text())
        check(f"data/{p.name}", text)
        (out / "data" / p.name).write_text(text)
        recs = list(csv.DictReader(io.StringIO(text)))
        cols = list(recs[0].keys()) if recs else []
        yrs = sorted({int(r[c]) for r in recs for c in ("fiscal_year", "fall_year") if (r.get(c) or "").isdigit()})
        span = f"{yrs[0]}–{yrs[-1]}" if yrs else ""
        rows.append(f'<tr><td><a href="data/{p.name}" download>{p.name}</a></td><td>{html.escape(DATA_NOTES.get(p.name, ""))}</td>'
                    f'<td class=num>{len(recs):,}</td><td class=num>{span}</td></tr>')
    body = ["<h1>Data</h1>",
            '<p class="lede">Every table behind this site, as CSV. Most files are long format: one row per figure, with '
            '<code>fiscal_year</code>, <code>metric</code>, <code>value</code>, <code>units</code> and the source document.</p>',
            '<div class="table-wrap"><table class="downloads"><thead><tr><th>File</th><th>What it holds</th>'
            f'<th class=num>Rows</th><th class=num>Years</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>',
            '<p class="note">Figures marked audited come from the district\'s annual comprehensive financial reports. '
            'Budget and projection rows are the district\'s plans at the time, not results.</p>']
    pages["data.html"] = ("Data", "Data", "\n".join(body))

    skipped = [t for t, _ in secs if t and t not in used
               and not re.search(r"^charts$|how to rebuild|step 2|comparison", t, re.I)]
    for t in skipped:
        print(f"warning: report section '{t}' is not placed on any page", file=sys.stderr)

    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "assets" / "site.css").write_text(CSS.strip() + "\n")
    (out / "assets" / "site.js").write_text(JS.strip() + "\n")
    (out / ".nojekyll").write_text("")
    for fname, (_, title, body) in pages.items():
        text = page(fname, title, body, built)
        check(fname, text)
        (out / fname).write_text(text)
    print(f"wrote {len(pages)} pages and {len(data_files)} data files to {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=HERE.parent / "mps", help="district folder (default: ../mps)")
    ap.add_argument("--out", type=Path, default=HERE.parent / "_site", help="output folder (default: ../_site)")
    ap.add_argument("--scrub", type=Path, help="JSON file of extra scrub/block patterns "
                    "(default: site_scrub_private.json next to this script, if present)")
    a = ap.parse_args()
    scrub = a.scrub or (HERE / "site_scrub_private.json")
    if scrub.exists():
        load_scrub(scrub)
    if a.out.exists():
        shutil.rmtree(a.out)
    build(a.src.resolve(), a.out.resolve())


if __name__ == "__main__":
    main()
