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
import datetime
import html
import io
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE_TITLE = "Minneapolis Public Schools finances"

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
    every = 2 if NARROW and len(xs) > 6 else every
    return [f'<text class="tick" x="{xpos(x):.1f}" y="{H - MB + 18}" text-anchor="middle">{x}</text>'
            for k, x in enumerate(xs) if k % every == 0]


def _line_chart(label, series, fmt, tipfmt=None, y0=None, refline=None, every=1):
    """series: [{name, points: {x: y}, dashed_from: x or None}]"""
    tipfmt = tipfmt or fmt
    xs = sorted({x for s in series for x in s["points"]})
    vals = [v for s in series for v in s["points"].values()] + ([refline[0]] if refline else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    lo = y0 if y0 is not None else lo - pad
    ticks = nice_ticks(lo, hi + pad)
    lo, hi = ticks[0], ticks[-1]
    xpos = lambda x: ML + (x - xs[0]) / max(1, xs[-1] - xs[0]) * (W - ML - MR)
    ys = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    g = [svg_open(label)] + y_axis(ticks, ys, fmt) + x_labels(xs, xpos, every)
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
        tip = [f"FY{x}"] + [f"{s['name']}: {tipfmt(s['points'][x])}" + (" (budget/projection)" if s.get("dashed_from") is not None and x > s["dashed_from"] else "")
                            for s in series if x in s["points"]]
        g.append(f'<rect class="hit" x="{xpos(x) - step / 2:.1f}" y="{MT}" width="{step:.1f}" '
                 f'height="{H - MT - MB}" data-x="{xpos(x):.1f}" data-tip="{html.escape(chr(10).join(tip))}"/>')
    g.append(f'<line class="guide" x1="0" x2="0" y1="{MT}" y2="{H - MB}"/>')
    g.append("</svg>")
    table = data_table(xs, series, tipfmt)
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


def _hbar_chart(label, cats, series, fmt):
    """Grouped horizontal bars: cats = [name], series = [{name, values: {cat: v}}]."""
    rowh, gap = 16, 10
    groupH = rowh * len(series) + gap
    ml, mr = (130, 50) if NARROW else (190, 70)
    h = MT + groupH * len(cats) + 26
    hi = nice_ticks(0, max(v for s in series for v in s["values"].values()))[-1]
    xs = lambda v: ml + v / hi * (W - ml - mr)
    g = [svg_open(label).replace(f"0 0 {W} {H}", f"0 0 {W} {h}")]
    for t in nice_ticks(0, hi):
        g.append(f'<line class="grid" x1="{xs(t):.1f}" x2="{xs(t):.1f}" y1="{MT}" y2="{h - 26}"/>')
        g.append(f'<text class="tick" x="{xs(t):.1f}" y="{h - 8}" text-anchor="middle">{fmt(t)}</text>')
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


def data_table(xs, series, fmt):
    head = "<tr><th>FY</th>" + "".join(f"<th class=num>{html.escape(s['name'])}</th>" for s in series) + "</tr>"
    body = "".join("<tr><td>" + str(x) + "</td>" + "".join(
        f"<td class=num>{fmt(s['points'][x]) if x in s['points'] else '–'}</td>" for s in series) + "</tr>" for x in xs)
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def dual(fn):
    def run(label, *a, **k):
        global W, H, ML, MR, NARROW
        svgs = []
        for narrow, dims in ((False, WIDE), (True, NARROW_DIMS)):
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


line_chart, bar_chart, hbar_chart = dual(_line_chart), dual(_bar_chart), dual(_hbar_chart)


def chart_block(fig, title, caption):
    return (f'<figure class="viz"><figcaption><h3 id="{slug(title)}">{html.escape(title)}</h3>'
            f'<p>{caption}</p></figcaption>{fig["html"]}</figure>')


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


# ---------------------------------------------------------------- pages
NAV = [("index.html", "Overview"), ("structure.html", "Financial structure"), ("money.html", "Money in and out"), ("facilities.html", "Buildings"),
       ("enrollment.html", "Enrollment"), ("governance.html", "Governance"), ("sources.html", "Sources and gaps"), ("data.html", "Data")]


def page(fname, title, body, built):
    nav = "".join(f'<a href="{h}"{" aria-current=page" if h == fname else ""}>{t}</a>' for h, t in NAV)
    full = title if fname == "index.html" else f"{title} · {SITE_TITLE}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(full)}</title>
<meta name="description" content="An independent look at Minneapolis Public Schools finances since FY2015, built from audited reports, budgets and state data.">
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
--s4:#eda100;--s5:#e87ba4;--s6:#008300;--ref:#d03b3b;--code:#f3f2ef}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#1a1a19;--surface:#222221;
--border:#383835;--grid:#2e2e2c;--text:#f0efec;--text2:#c3c2b7;--muted:#9a9990;--link:#86b6ef;--s1:#3987e5;--s2:#d95926;
--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--ref:#e66767;--code:#2a2a28}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#1a1a19;--surface:#222221;--border:#383835;--grid:#2e2e2c;--text:#f0efec;
--text2:#c3c2b7;--muted:#9a9990;--link:#86b6ef;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--ref:#e66767;--code:#2a2a28}
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
.hit{fill:transparent;cursor:crosshair}
.guide{stroke:var(--muted);stroke-width:1;opacity:0;pointer-events:none}
.legend{list-style:none;padding:0;margin:0 0 6px;display:flex;flex-wrap:wrap;gap:4px 18px;font-size:.88rem;color:var(--text2)}
.legend li{margin:0;display:flex;align-items:center;gap:6px}
.swatch{width:12px;height:12px;border-radius:3px;display:inline-block}
.tooltip{position:fixed;z-index:10;pointer-events:none;background:var(--surface);color:var(--text);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;font-size:.85rem;line-height:1.45;white-space:pre-line;box-shadow:0 4px 14px rgba(0,0,0,.12);
max-width:280px;font-variant-numeric:tabular-nums}
.tooltip::first-line{font-weight:650}
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
  function hide(){tip.hidden=true;document.querySelectorAll('.guide').forEach(function(g){g.style.opacity=0});}
  document.addEventListener('pointermove',function(e){
    var t=e.target;
    if(!(t instanceof Element)||!t.classList.contains('hit')){hide();return;}
    tip.textContent=t.getAttribute('data-tip');tip.hidden=false;
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
    "enrollment_vs_population.csv": "District enrollment against Minneapolis population estimates, by school year.",
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
            "rising, federal pandemic aid filled the gap for a few years, and the district now faces structural deficits.")
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
                "State revenue rose $90.5M from FY2023 to FY2025, and transportation explains only about $11M of it. Special "
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
            '<p class="lede">District enrollment against the size of the city it serves.</p>']
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
            lambda v: f"{v:.0f}", lambda v: f"{v:.1f}")
        body.append(chart_block(c_ev, "Students and city residents",
                                f"Each series indexed to fall {b0} = 100 (the October count is scaled to the ADM index in fall {b1}, "
                                "the first year both exist). x-axis is the fall of each school year. Minneapolis' population was "
                                "roughly flat while district enrollment fell by about a fifth."))
        rows = "".join(f"<tr><td>{html.escape(r['school_year'])}</td><td class=num>{count(num(r['mps_adm_total'])) if num(r['mps_adm_total']) else '–'}</td>"
                       f"<td class=num>{count(num(r['mps_oct1_k12'])) if num(r['mps_oct1_k12']) else '–'}</td>"
                       f"<td>{html.escape(r['mps_oct1_k12_note'])}</td>"
                       f"<td class=num>{count(num(r['mpls_pop_july1_fall_year'])) if num(r['mpls_pop_july1_fall_year']) else '–'}</td></tr>"
                       for r in ev)
        body.append('<h2 id="by-year">By school year</h2><div class="table-wrap"><table><thead><tr><th>School year</th>'
                    '<th class=num>ADM</th><th class=num>Oct 1 K-12</th><th>Count basis</th><th class=num>City population (July 1)</th>'
                    f'</tr></thead><tbody>{rows}</tbody></table></div>')
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
                '<p class="lede">Who decides what at the district, and where budget decisions are actually made.</p>',
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
            charts["fullness"] = chart_block(c_full, "How full each school is",
                "Number of school buildings by enrollment as a share of capacity, 2024-25. Programs housed in a building count toward it [1].")
        fsx = src / "facilities" / "facilities_spending_series.csv"
        if fsx.exists():
            with open(fsx) as f:
                frow = list(csv.DictReader(f))
            fcol = lambda fn: {int(r["fiscal_year"]): fn(r) for r in frow}
            c_fsp = line_chart("Building-related spending, all governmental funds", [
                {"name": "Debt service", "points": fcol(lambda r: float(r["govfunds_debt_principal"]) + float(r["govfunds_debt_interest"]))},
                {"name": "Running buildings, all funds", "points": fcol(lambda r: float(r["govfunds_sites_buildings_current"]))},
                {"name": "Running buildings, General Fund", "points": fcol(lambda r: float(r["gf_sites_buildings_current"]))}],
                musd, musd1, y0=0)
            charts["spending"] = chart_block(c_fsp, "Building spending, FY2016 to FY2025",
                "Not adjusted for inflation. Debt service includes some non-building items such as leases and software subscriptions. "
                "FY2025 annual financial report, statistical Table 3 [1].")
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
