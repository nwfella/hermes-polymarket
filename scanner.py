#!/usr/bin/env python3
"""
Polymarket Opportunity Scanner — CLI version for cron jobs.
Scans daily crypto markets for scalp and asymmetric opportunities.

Usage:
    python3 scanner.py                    # Full scan, print results
    python3 scanner.py --brief            # Compact table format
    python3 scanner.py --top 3            # Show only top 3 plays
"""

import json
import sys
import os
import urllib.request
import urllib.parse
import re
from datetime import datetime, timezone
from time import sleep

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CG = "https://api.coingecko.com/api/v3"

SEARCH_TERMS = ["bitcoin above", "ethereum above", "solana dip", "solana reach"]
CRYPTO_IDS = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana"}


def _get(url: str, timeout: int = 15) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-scanner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fmt_pct(v):
    return f"{float(v) * 100:.1f}%"


def fmt_doll(v):
    return f"${float(v):.2f}"


def fmt_num(v):
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.0f}"


def get_spot_prices():
    """Fetch BTC, ETH, SOL prices from CoinGecko."""
    data = _get(f"{CG}/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true")
    return {
        "btc": {"usd": data["bitcoin"]["usd"], "change": data["bitcoin"].get("usd_24h_change", 0)},
        "eth": {"usd": data["ethereum"]["usd"], "change": data["ethereum"].get("usd_24h_change", 0)},
        "sol": {"usd": data["solana"]["usd"], "change": data["solana"].get("usd_24h_change", 0)},
    }


def search_events():
    """Search Gamma API for crypto daily events."""
    events = []
    for term in SEARCH_TERMS:
        try:
            data = _get(f"{GAMMA}/public-search?q={urllib.parse.quote(term)}&limit=5")
            for evt in data.get("events", []):
                active = [m for m in evt.get("markets", []) if not m.get("closed")]
                if active:
                    events.append({"title": evt["title"], "slug": evt.get("slug", ""), "markets": active})
        except Exception as e:
            print(f"  [warn] Search failed for '{term}': {e}", file=sys.stderr)
    return events


def parse_market_field(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def analyze_market(m, prices):
    """Analyze a market and classify as scalp, asymmetric, or contrarian."""
    outcomes = parse_market_field(m.get("outcomes", "[]"))
    price_strs = parse_market_field(m.get("outcomePrices", "[]"))
    if not isinstance(price_strs, list) or len(price_strs) < 2:
        return None

    yes_p = float(price_strs[0])
    no_p = float(price_strs[1])
    question = (m.get("question", "") or "").lower()

    # Detect asset and threshold
    asset = None
    threshold = 0
    direction = None

    for name, key in [("bitcoin", "btc"), ("ethereum", "eth"), ("solana", "sol")]:
        if name in question:
            asset = key
            break

    import re
    price_match = re.search(r'\$([0-9,]+)', question)
    if price_match:
        threshold = float(price_match.group(1).replace(",", ""))

    if "above" in question:
        direction = "above"
    elif "dip to" in question or "below" in question:
        direction = "dip"

    # Safety analysis
    spot_price = prices[asset]["usd"] if asset and asset in prices else None
    move_needed = None
    safety_color = "#555"
    safety_label = "—"
    is_safe = False

    if spot_price and threshold > 0 and direction:
        if direction == "above":
            move_needed = ((threshold - spot_price) / spot_price) * 100
            if move_needed > 0:
                is_safe = move_needed > 5
                safety_label = f"BTC needs +{move_needed:.1f}%" if asset == "btc" else f"Needs +{move_needed:.1f}%"
                safety_color = "#4ade80" if move_needed > 5 else "#facc15"
            else:
                safety_label = "⚠️ Already above threshold"
                safety_color = "#ef4444"
        elif direction == "dip":
            move_needed = ((spot_price - threshold) / spot_price) * 100
            if move_needed > 0:
                is_safe = move_needed > 10
                safety_label = f"SOL needs -{move_needed:.1f}%" if asset == "sol" else f"Needs -{move_needed:.1f}%"
                safety_color = "#4ade80" if move_needed > 10 else "#facc15"
            else:
                safety_label = "⚠️ Already below threshold"
                safety_color = "#ef4444"

    # Classify
    if no_p >= 0.95:
        play_type = "💰 SCALP"
    elif yes_p <= 0.05:
        play_type = "🎰 ASYMMETRIC"
    elif yes_p <= 0.15:
        play_type = "🎯 EDGE BET"
    else:
        play_type = "📊 NEUTRAL"

    # Parse tokens
    tokens = parse_market_field(m.get("clobTokenIds", "[]"))
    no_token = tokens[1] if isinstance(tokens, list) and len(tokens) >= 2 else None

    # Resolution date
    resolve_match = re.findall(r'june (\d+)', question, re.IGNORECASE)
    if resolve_match:
        days = sorted(set(int(d) for d in resolve_match))
        resolve_str = f"Jun {days[0]}" if len(days) == 1 else f"Jun {days[0]}-{days[-1]}"
    else:
        resolve_str = "—"

    # Score
    score = 0
    if play_type == "💰 SCALP":
        score = min(move_needed or 0, 50) * (1 - no_p) * 100
    elif "ASYMMETRIC" in play_type or "EDGE" in play_type:
        score = min(100, (1 / yes_p) * (1.5 if is_safe else 0.5))

    return {
        "question": m.get("question", "?"),
        "slug": m.get("slug", ""),
        "volume": float(m.get("volume", 0)),
        "yes_p": yes_p, "no_p": no_p,
        "play_type": play_type,
        "asset": asset, "threshold": threshold,
        "spot_price": spot_price if spot_price else None,
        "safety_label": safety_label,
        "is_safe": is_safe,
        "no_token": no_token,
        "resolve_str": resolve_str,
        "score": score,
        "outcomes": outcomes,
    }


def fetch_orderbook(token_id):
    """Fetch orderbook and return best ask info."""
    if not token_id:
        return None
    try:
        book = _get(f"{CLOB}/book?token_id={token_id}", timeout=10)
        asks = sorted(book.get("asks", []), key=lambda x: float(x.get("price", 0)))
        if not asks:
            return None
        best = asks[0]
        price = float(best["price"])
        size = float(best["size"])
        return {
            "ask_price": price,
            "ask_size": size,
            "total_cost": price * size,
            "return_pct": (1 - price) * 100,
        }
    except Exception:
        return None


def main():
    brief = "--brief" in sys.argv
    top_n = None
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        if idx + 1 < len(sys.argv):
            top_n = int(sys.argv[idx + 1])

    # 1. Spot prices
    try:
        prices = get_spot_prices()
    except Exception as e:
        print(f"❌ Failed to fetch spot prices: {e}")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    header = f"🔍 Polymarket Scanner · {now.strftime('%B %d, %Y at %I:%M %p').lstrip('0').replace(' 0', ' ')} ET"
    print("=" * (len(header) + 2))
    print(f" {header} ")
    print("=" * (len(header) + 2))
    print()

    # Spot ticker
    if not brief:
        print(f"  BTC: ${prices['btc']['usd']:,.0f} ({prices['btc']['change']:+.2f}%)")
        print(f"  ETH: ${prices['eth']['usd']:,.0f} ({prices['eth']['change']:+.2f}%)")
        print(f"  SOL: ${prices['sol']['usd']:,.2f} ({prices['sol']['change']:+.2f}%)")
        print()

    # 2. Search events
    events = search_events()

    # 3. Analyze
    candidates = []
    seen = set()
    for evt in events:
        for m in evt["markets"]:
            slug = m.get("slug", "")
            if slug in seen:
                continue
            seen.add(slug)
            a = analyze_market(m, prices)
            if a and (a["no_p"] >= 0.85 or a["yes_p"] <= 0.15):
                candidates.append(a)

    if not candidates:
        print("  No obvious opportunities found right now.\n")
        return

    # 4. Fetch orderbooks for scalp candidates
    scalp_cands = [c for c in candidates if c["play_type"] == "💰 SCALP" and c["no_token"]]
    for c in scalp_cands[:8]:
        book = fetch_orderbook(c["no_token"])
        if book:
            c["book"] = book
            # Calculate safety margin from safety_label
            move = 5  # default safe margin
            try:
                m = re.search(r'([\d.]+)', c["safety_label"])
                if m:
                    move = float(m.group(1))
            except:
                pass
            c["score"] = move * book["return_pct"]
            if book["total_cost"] > 100:
                c["score"] *= 1.2

    # 5. Sort
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Separate
    scalps = [c for c in candidates if c["play_type"] == "💰 SCALP"][:5]
    edges = [c for c in candidates if c["play_type"] != "💰 SCALP"][:5]
    rest = [c for c in candidates if c not in scalps and c not in edges][:3]

    # --- SCALPS ---
    if scalps:
        print("💰 SCALP PLAYS (Buy No, hold to resolution)")
        if brief:
            print(f"{'Market':<55} {'Resolves':<10} {'No%':<8} {'Return':<8} {'Cost':<10} {'Safety'}")
            print("-" * 110)
            for c in scalps:
                q = c["question"][:50] + ".." if len(c["question"]) > 50 else c["question"]
                ret = f"{c['book']['return_pct']:.2f}%" if c.get("book") else "—"
                cost = f"${c['book']['total_cost']:.2f}" if c.get("book") else "—"
                safe = f"{'🟢' if c['is_safe'] else '🟡'}"
                print(f"{q:<55} {c['resolve_str']:<10} {c['no_p']*100:<7.1f}% {ret:<8} {cost:<10} {safe} {c['safety_label']}")
        else:
            for c in scalps:
                print(f"\n  {'─' * 60}")
                print(f"  {c['question']}")
                print(f"  Resolves: {c['resolve_str']}  |  No: {c['no_p']*100:.1f}%  |  Vol: {fmt_num(c['volume'])}")
                if c.get("book"):
                    print(f"  Best ask: {c['book']['ask_price']*100:.1f}¢  |  Size: {c['book']['ask_size']:.2f}  |  Return: {c['book']['return_pct']:.2f}%  |  Cost: ${c['book']['total_cost']:.2f}")
                print(f"  Safety: {'🟢' if c['is_safe'] else '🟡'} {c['safety_label']}")

    # --- EDGES ---
    if edges:
        print(f"\n\n🎯 ASYMMETRIC PLAYS (Buy Yes, small cost × huge upside)")
        if brief:
            print(f"{'Market':<55} {'Resolves':<10} {'Yes%':<8} {'Upside':<8} {'Vol':<10} {'Safety'}")
            print("-" * 110)
            for c in edges:
                q = c["question"][:50] + ".." if len(c["question"]) > 50 else c["question"]
                upside = f"{1/c['yes_p']:.1f}x" if c['yes_p'] > 0 else "∞"
                safe = f"{'🟢' if c['is_safe'] else '🟡'}"
                print(f"{q:<55} {c['resolve_str']:<10} {c['yes_p']*100:<7.1f}% {upside:<8} {fmt_num(c['volume']):<10} {safe} {c['safety_label']}")
        else:
            for c in edges:
                upside = f"{1/c['yes_p']:.1f}x" if c['yes_p'] > 0 else "∞×"
                print(f"\n  {'─' * 60}")
                print(f"  {c['question']}")
                print(f"  Resolves: {c['resolve_str']}  |  Yes: {c['yes_p']*100:.2f}¢  |  Upside: {upside}  |  Vol: {fmt_num(c['volume'])}")
                print(f"  Safety: {'🟢' if c['is_safe'] else '🟡'} {c['safety_label']}")

    print()
    print("─" * 60)
    print(f"  Analyzed {len(candidates)} markets · Always check orderbook before trading")
    print()


if __name__ == "__main__":
    main()
