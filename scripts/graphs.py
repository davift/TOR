#!/usr/bin/env python3
import gzip
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration -- put your own fingerprints here
# ---------------------------------------------------------------------------

FINGERPRINTS = [
    "BC4736C28F92AA35EF188E84503BFF81F100C325",
    "EA11D1FE98637CC0AE1CB3CA2F2638696C6156C8",
]

ONIONOO = "https://onionoo.torproject.org"

# Onionoo asks clients to identify themselves. Edit this to point at your repo.
USER_AGENT = "tor-relay-graphs/1.0 (+https://github.com/YOUR_USER/YOUR_REPO)"

CHART_DIR = "charts"
README = "README.md"

# Two colour schemes so the graphs stay readable in GitHub's light and dark
# themes. The backgrounds are transparent; only line/text colours change.
THEMES = {
    "light": {"fg": "#24292f", "grid": "#d0d7de", "read": "#0969da", "write": "#8250df"},
    "dark": {"fg": "#c9d1d9", "grid": "#30363d", "read": "#58a6ff", "write": "#bc8cff"},
}

# Onionoo bandwidth/uptime graphs come in these fixed windows.
BANDWIDTH_WINDOWS = [("1_month", "Last month"), ("6_months", "Last 6 months")]

# One colour per relay, used when several relays share a single graph.
PALETTE = ["#0969da", "#8250df", "#3fb950", "#d29922"]


# ---------------------------------------------------------------------------
# Onionoo client
# ---------------------------------------------------------------------------


def fetch(document, **params):
    """GET one Onionoo document, gzip-aware."""
    url = f"{ONIONOO}/{document}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def index_by_fingerprint(doc, key="relays"):
    return {r.get("fingerprint", "").upper(): r for r in doc.get(key, [])}


# ---------------------------------------------------------------------------
# Graph history decoding
#
# Onionoo compresses each series: `values` are integers 0-999 (or null) that
# must be multiplied by `factor` to get real units. Timestamps start at
# `first` and advance by `interval` seconds.
# ---------------------------------------------------------------------------


def decode_history(hist):
    if not hist:
        return [], []
    first = datetime.strptime(hist["first"], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    interval = timedelta(seconds=hist["interval"])
    factor = hist["factor"]
    times, values = [], []
    for i, v in enumerate(hist["values"]):
        times.append(first + i * interval)
        values.append(math.nan if v is None else v * factor)
    return times, values


def bytes_per_sec_to_mbit(values):
    return [v * 8 / 1e6 for v in values]


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------


def style_axis(ax, theme, ylabel):
    ax.set_facecolor("none")
    ax.grid(True, color=theme["grid"], linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(theme["grid"])
    ax.tick_params(colors=theme["fg"], labelsize=8)
    ax.set_ylabel(ylabel, color=theme["fg"], fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.set_ylim(bottom=0)


def add_legend(ax, theme, loc):
    handles, _ = ax.get_legend_handles_labels()
    if not handles:
        return
    leg = ax.legend(loc=loc, fontsize=8, frameon=False)
    for text in leg.get_texts():
        text.set_color(theme["fg"])


def save(fig, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=140, transparent=True, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {path}")


def plot_relay_bandwidth(nickname, fingerprint, bw, theme_name):
    """One figure per relay: read/write bandwidth over 1 month and 6 months."""
    theme = THEMES[theme_name]
    fig, axes = plt.subplots(len(BANDWIDTH_WINDOWS), 1, figsize=(9, 5.5))
    fig.patch.set_alpha(0)

    for ax, (window, label) in zip(axes, BANDWIDTH_WINDOWS):
        rt, rv = decode_history((bw.get("read_history") or {}).get(window))
        wt, wv = decode_history((bw.get("write_history") or {}).get(window))
        if rt:
            read = bytes_per_sec_to_mbit(rv)
            ax.plot(rt, read, color=theme["read"], linewidth=1.4, label="Read")
            ax.fill_between(rt, read, color=theme["read"], alpha=0.12)
        if wt:
            write = bytes_per_sec_to_mbit(wv)
            ax.plot(wt, write, color=theme["write"], linewidth=1.4, label="Write")
            ax.fill_between(wt, write, color=theme["write"], alpha=0.12)
        if not rt and not wt:
            ax.text(0.5, 0.5, "no data for this period", ha="center", va="center",
                    transform=ax.transAxes, color=theme["fg"], fontsize=9)
        style_axis(ax, theme, "Mbit/s")
        ax.set_title(label, color=theme["fg"], fontsize=10, loc="left")
        add_legend(ax, theme, "upper left")

    fig.suptitle(f"{nickname} — bandwidth", color=theme["fg"], fontsize=12,
                 x=0.02, ha="left")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    save(fig, f"{CHART_DIR}/{fingerprint[:8]}-bandwidth-{theme_name}.png")


def plot_family_comparison(relays, bandwidth, theme_name):
    """Both relays' write bandwidth on one axis, for a family-level view."""
    theme = THEMES[theme_name]
    fig, ax = plt.subplots(figsize=(9, 3.2))
    fig.patch.set_alpha(0)

    plotted = False
    for i, fp in enumerate(FINGERPRINTS):
        bw = bandwidth.get(fp, {})
        t, v = decode_history((bw.get("write_history") or {}).get("6_months"))
        if not t:
            continue
        name = relays.get(fp, {}).get("nickname", fp[:8])
        ax.plot(t, bytes_per_sec_to_mbit(v), linewidth=1.4,
                color=PALETTE[i % len(PALETTE)], label=name)
        plotted = True

    if not plotted:
        ax.text(0.5, 0.5, "no data", ha="center", va="center",
                transform=ax.transAxes, color=theme["fg"])
    style_axis(ax, theme, "Mbit/s")
    ax.set_title("Family — write bandwidth, last 6 months", color=theme["fg"],
                 fontsize=11, loc="left")
    add_legend(ax, theme, "upper left")
    fig.tight_layout()
    save(fig, f"{CHART_DIR}/family-bandwidth-{theme_name}.png")


def plot_uptime(relays, uptime, theme_name):
    theme = THEMES[theme_name]
    fig, ax = plt.subplots(figsize=(9, 2.8))
    fig.patch.set_alpha(0)

    for i, fp in enumerate(FINGERPRINTS):
        up = uptime.get(fp, {})
        t, v = decode_history((up.get("uptime") or {}).get("1_month"))
        if not t:
            continue
        name = relays.get(fp, {}).get("nickname", fp[:8])
        ax.plot(t, [x * 100 for x in v], linewidth=1.4,
                color=PALETTE[i % len(PALETTE)], label=name)

    style_axis(ax, theme, "Uptime %")
    ax.set_ylim(0, 105)
    ax.set_title("Uptime, last month", color=theme["fg"], fontsize=11, loc="left")
    add_legend(ax, theme, "lower left")
    fig.tight_layout()
    save(fig, f"{CHART_DIR}/uptime-{theme_name}.png")


# ---------------------------------------------------------------------------
# README stats table
# ---------------------------------------------------------------------------


def human_bandwidth(bytes_per_sec):
    if not bytes_per_sec:
        return "—"
    mbit = bytes_per_sec * 8 / 1e6
    return f"{mbit:.1f} Mbit/s"


def build_table(relays):
    header = (
        "| Relay | Status | Flags | Advertised BW | Consensus weight | Country |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    )
    rows = []
    for fp in FINGERPRINTS:
        r = relays.get(fp)
        if not r:
            rows.append(f"| `{fp[:8]}…` | not found in Onionoo | — | — | — | — |")
            continue
        nickname = r.get("nickname", "unnamed")
        link = f"https://metrics.torproject.org/rs.html#details/{fp}"
        status = "🟢 running" if r.get("running") else "🔴 down"
        flags = ", ".join(r.get("flags", [])) or "—"
        bw = human_bandwidth(r.get("advertised_bandwidth"))
        weight = f"{r.get('consensus_weight', 0):,}"
        country = r.get("country_name") or r.get("country") or "—"
        rows.append(
            f"| [{nickname}]({link}) | {status} | {flags} | {bw} | {weight} | {country} |"
        )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return header + "\n".join(rows) + f"\n\n_Last updated: {stamp}_"


def inject(table):
    if not os.path.exists(README):
        print("  README.md not found, skipping table injection")
        return
    with open(README, encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(
        r"(<!-- RELAY-STATS:START -->)(.*?)(<!-- RELAY-STATS:END -->)", re.DOTALL
    )
    if not pattern.search(content):
        print("  no RELAY-STATS markers in README.md, skipping table injection")
        return
    new = pattern.sub(lambda m: f"{m.group(1)}\n{table}\n{m.group(3)}", content)
    if new != content:
        with open(README, "w", encoding="utf-8") as f:
            f.write(new)
        print("  updated README.md stats table")


# ---------------------------------------------------------------------------


def main():
    lookup = ",".join(FINGERPRINTS)
    print("Fetching Onionoo documents…")
    relays = index_by_fingerprint(fetch("details", lookup=lookup))
    bandwidth = index_by_fingerprint(fetch("bandwidth", lookup=lookup))
    uptime = index_by_fingerprint(fetch("uptime", lookup=lookup))

    missing = [fp for fp in FINGERPRINTS if fp not in relays]
    if missing:
        print(f"WARNING: not returned by Onionoo: {', '.join(missing)}", file=sys.stderr)

    print("Rendering charts…")
    for theme_name in THEMES:
        for fp in FINGERPRINTS:
            nickname = relays.get(fp, {}).get("nickname", fp[:8])
            plot_relay_bandwidth(nickname, fp, bandwidth.get(fp, {}), theme_name)
        plot_family_comparison(relays, bandwidth, theme_name)
        plot_uptime(relays, uptime, theme_name)

    inject(build_table(relays))
    print("Done.")


if __name__ == "__main__":
    main()
