#!/usr/bin/env python3
"""Refresh the dashboard's KNOWN_MARKETS list from live Polymarket markets.

The dashboard cannot discover markets itself: Gamma is blocked from the browser
(CORS) and only the CLOB endpoint is reachable, so the market list ships as a
hardcoded snapshot. That snapshot silently rots — every market in it eventually
resolves, and the scanner then reports "no opportunities" for weeks. This script
regenerates it against the live API.

What it keeps:
  * daily "above on <Month D>" ladders for BTC/ETH/SOL, today and the next two
    days, restricted to rungs whose No side is near-certain (a scalp candidate)
  * barrier ladders ("dip to $X" / "reach $X") with the same No-side band
  * at most N per (asset, direction, expiry), so the page stays readable

Schema written per entry (matches what index.html reads):
  id, title, asset, threshold, direction, noToken, resolveIso, resolveStr
`resolveIso` is the market's end date; index.html drops entries whose
resolveIso has passed, so a stale list degrades honestly instead of lying.

Usage:
  python refresh_markets.py                  # rewrite index.html + ~/dashboard.html
  python refresh_markets.py --dry-run        # print the block, write nothing
  python refresh_markets.py --target <file>  # rewrite a different file
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CG = "https://api.coingecko.com/api/v3"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGET = os.path.join(HERE, "index.html")
MIRROR = os.path.join(os.path.expanduser("~"), "dashboard.html")

START = "// __KNOWN_MARKETS_START__"
END = "// __KNOWN_MARKETS_END__"

NO_BAND = (0.90, 0.995)   # No-side price band worth showing
NO_IDEAL = 0.96           # rank rungs by distance from this
MAX_PER_GROUP = 2         # per (asset, direction, expiry)

ASSETS = ("bitcoin", "ethereum", "solana")
SYM = {"bitcoin": "btc", "ethereum": "eth", "solana": "sol"}


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-polymarket-refresh/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tokens_of(m):
    v = m.get("clobTokenIds")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return []
    return v or []


def no_price(token):
    try:
        return float(get(f"{CLOB}/midpoint?token_id={token}", timeout=10)["mid"])
    except Exception:
        return None


def discover():
    """All currently-open crypto markets, deduped by No token."""
    found = {}

    def add(ev_title, m):
        toks = tokens_of(m)
        if len(toks) < 2:
            return
        found.setdefault(toks[1], (ev_title, m))

    # daily ladders have deterministic slugs; public-search often misses them
    et = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
    for offset in (0, 1, 2):
        d = et + datetime.timedelta(days=offset)
        for asset in ASSETS:
            slug = f"{asset}-above-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"
            try:
                evs = get(f"{GAMMA}/events?slug={slug}")
            except Exception:
                continue
            if not isinstance(evs, list):
                continue
            for e in evs:
                for m in e.get("markets", []):
                    if not m.get("closed"):
                        add(e.get("title", ""), m)

    for term in ("solana dip", "solana reach", "bitcoin above", "ethereum above",
                 "bitcoin dip", "ethereum dip"):
        try:
            data = get(f"{GAMMA}/public-search?q={urllib.parse.quote(term)}&limit=20")
        except Exception:
            continue
        for evt in data.get("events", []):
            for m in evt.get("markets", []):
                if not m.get("closed"):
                    add(evt.get("title", ""), m)

    return found


def classify(ev_title, m):
    q = m.get("question") or ""
    low = q.lower()
    asset = next((SYM[a] for a in ASSETS if a in low), None)
    if not asset:
        return None
    strike_m = re.search(r"\$([0-9][0-9,]*)", q)
    if not strike_m:
        return None
    strike = int(strike_m.group(1).replace(",", ""))
    if "dip to" in low or "below" in low:
        direction = "dip"
    elif "reach" in low or "above" in low or "hit" in low:
        direction = "above"
    else:
        return None
    end = str(m.get("endDateIso") or m.get("endDate") or "")[:10]
    if not end:
        return None
    return {
        "question": q,
        "asset": asset,
        "threshold": strike,
        "direction": direction,
        "end": end,
        "slug": m.get("slug", ""),
    }


def label(end_iso):
    d = datetime.date.fromisoformat(end_iso)
    # the Dec-31 markets end 2027-01-01; label them by what the question asks
    if d.month == 1 and d.day == 1:
        return "Dec 31"
    return f"{d.strftime('%b')} {d.day}"


def section(label, entries, priced):
    """One JS comment header + the entries it covers."""
    lines = [f"  // === {label} ==="]
    for e in entries:
        mid = priced[e["noToken"]]
        lines.append(
            "  { id: '%s', title: '%s', asset: '%s', threshold: %s, direction: '%s',"
            % (e["id"], e["title"].replace("'", "\\'"), e["asset"], e["threshold"], e["direction"])
        )
        lines.append(
            "    noToken: '%s', resolveIso: '%s', resolveStr: '%s' },  // No mid %.3f"
            % (e["noToken"], e["end"], e["resolveStr"], mid)
        )
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-mirror", action="store_true", help="don't sync ~/dashboard.html")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    found = discover()
    print(f"discovered {len(found)} open markets", file=sys.stderr)

    priced, groups = {}, {}
    for no_token, (ev_title, m) in found.items():
        c = classify(ev_title, m)
        if not c or c["end"] < today:          # already resolved/expired
            continue
        p = no_price(no_token)
        if p is None or not (NO_BAND[0] <= p <= NO_BAND[1]):
            continue
        priced[no_token] = p
        c["noToken"] = no_token
        c["resolveStr"] = label(c["end"])
        c["id"] = f'{c["asset"]}-{c["direction"]}-{c["threshold"]}-{c["end"]}'
        c["title"] = (f'{c["asset"].upper()} {">" if c["direction"] == "above" else "dip to"} '
                      f'${c["threshold"]:,} · {c["resolveStr"]}')
        groups.setdefault((c["asset"], c["direction"], c["end"]), []).append(c)

    picked = []
    for key, rows in groups.items():
        # Polymarket carries occasional duplicate listings for the same question;
        # keep the one priced closest to NO_IDEAL so the page shows it once.
        rows = list({(r["asset"], r["direction"], r["threshold"], r["end"]): r for r in rows}.values())
        rows.sort(key=lambda r: abs(priced[r["noToken"]] - NO_IDEAL))
        picked.extend(rows[:MAX_PER_GROUP])
    picked.sort(key=lambda r: (r["end"], r["asset"], r["direction"], r["threshold"]))
    print(f"{len(picked)} entries kept after the No-band {NO_BAND} filter", file=sys.stderr)

    # group the output by expiry so the block reads like the hand-written one
    by_end = {}
    for e in picked:
        by_end.setdefault(e["end"], []).append(e)

    block = [START]
    block.append(f"// Hardcoded snapshot of live Polymarket markets — refreshed {today} by")
    block.append("// refresh_markets.py (the browser cannot discover markets: Gamma is CORS-blocked")
    block.append("// and only the CLOB endpoint is reachable). Entries past their resolveIso are")
    block.append("// dropped at render time, so this list degrades honestly.")
    block.append(f"const KNOWN_MARKETS_REFRESHED = '{today}';")
    block.append("const KNOWN_MARKETS = [")
    for end in sorted(by_end):
        rows = by_end[end]
        head = rows[0]["resolveStr"]
        days = (datetime.date.fromisoformat(end) - datetime.date.today()).days
        when = f"{head} (resolves in {days}d)" if days >= 0 else head
        block.extend(section(when, rows, priced))
        block.append("")
    if block[-1] == "":
        block.pop()
    # the replaced region spans the whole array literal, so close it here
    block.append("];")
    block.append(END)
    text = "\n".join(block)

    if args.dry_run:
        print(text)
        return 0

    target = os.path.abspath(args.target)
    with open(target, encoding="utf-8", newline="") as f:
        html = f.read()
    if html.count(START) != 1 or html.count(END) != 1:
        print(f"markers not found exactly once in {target} — refusing to write", file=sys.stderr)
        return 1
    crlf = "\r\n" in html
    newline = "\r\n" if crlf else "\n"
    head, rest = html.split(START, 1)
    _, tail = rest.split(END, 1)
    block = text.replace("\n", newline)
    new_html = head + block + tail
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(new_html)
    print(f"wrote {len(picked)} markets into {target}", file=sys.stderr)

    if not args.no_mirror and target == DEFAULT_TARGET:
        shutil.copyfile(target, MIRROR)
        print(f"mirrored -> {MIRROR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
