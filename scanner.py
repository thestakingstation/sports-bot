import os
import json
import uuid
import math
import requests
from scipy.stats import norm
from datetime import datetime, timezone

THE_ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "YOUR_THE_ODDS_API_KEY_HERE")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")

SPORTS_TO_SCAN = [
    {"sport_key": "americanfootball_nfl", "market_key": "spreads"},
    {"sport_key": "soccer_epl", "market_key": "totals"},
]

REGIONS = "us"
ODDS_FORMAT = "american"
TOP_N_TO_POST = 3

STD_DEV_NFL = 13.5
STD_DEV_NBA = 12.0
HOME_FIELD_ADVANTAGE = 2.5

TOTALS_LINE_FALLBACK = 2.5
KELLY_FRACTION = 0.25

OSRS_GOLD = 0xC2A649
COIN_STACK_THUMB = "https://oldschool.runescape.wiki/images/Coins_10000.png"

RATINGS_FILE = "ratings.json"
PICKS_LOG_FILE = "picks_log.json"


def load_ratings():
    defaults = {
        "power_ratings": {},
        "league_avg_home_goals": 1.5,
        "league_avg_away_goals": 1.2,
        "team_attack_strength": {},
        "team_defense_strength": {},
    }
    if not os.path.exists(RATINGS_FILE):
        print(f"No {RATINGS_FILE} found yet — using neutral defaults until ratings_builder.py runs.")
        return defaults
    try:
        with open(RATINGS_FILE) as f:
            data = json.load(f)
        for key in defaults:
            data.setdefault(key, defaults[key])
        return data
    except Exception as e:
        print(f"⚠️ Failed to load {RATINGS_FILE}: {e} — using neutral defaults.")
        return defaults


RATINGS = load_ratings()
POWER_RATINGS = RATINGS["power_ratings"]
LEAGUE_AVG_HOME_GOALS = RATINGS["league_avg_home_goals"]
LEAGUE_AVG_AWAY_GOALS = RATINGS["league_avg_away_goals"]
TEAM_ATTACK_STRENGTH = RATINGS["team_attack_strength"]
TEAM_DEFENSE_STRENGTH = RATINGS["team_defense_strength"]


def american_to_decimal(o):
    return 1 + (o / 100) if o > 0 else 1 + (100 / abs(o))

def decimal_to_implied_prob(d):
    return 1 / d

def remove_vig(pa, pb):
    total = pa + pb
    return pa / total, pb / total


def std_dev_for_sport(sport_key):
    return STD_DEV_NBA if "basketball" in sport_key else STD_DEV_NFL

def projected_margin(home, away):
    return (POWER_RATINGS.get(home, 0) - POWER_RATINGS.get(away, 0)) + HOME_FIELD_ADVANTAGE

def spread_prob(margin, line, std_dev):
    z = (margin - (-line)) / std_dev
    return norm.cdf(z)


def poisson(lam, k):
    return (math.exp(-lam) * (lam ** k)) / math.factorial(k)

def expected_goals(home, away):
    ha = TEAM_ATTACK_STRENGTH.get(home, 1.0)
    ad = TEAM_DEFENSE_STRENGTH.get(away, 1.0)
    aa = TEAM_ATTACK_STRENGTH.get(away, 1.0)
    hd = TEAM_DEFENSE_STRENGTH.get(home, 1.0)
    return LEAGUE_AVG_HOME_GOALS * ha * ad, LEAGUE_AVG_AWAY_GOALS * aa * hd

def total_under_prob(lam_h, lam_a, line, grid=12):
    cap = math.floor(line)
    return sum(
        poisson(lam_h, h) * poisson(lam_a, a)
        for h in range(grid) for a in range(grid) if h + a <= cap
    )


def fetch_odds(sport_key, market_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": REGIONS,
        "markets": market_key,
        "oddsFormat": ODDS_FORMAT,
    }
    res = requests.get(url, params=params, timeout=15)
    res.raise_for_status()
    return res.json()


def scan_one(sport_key, market_key):
    candidates = []
    games = fetch_odds(sport_key, market_key)
    print(f"[{sport_key} / {market_key}] fetched {len(games)} games")

    std_dev = std_dev_for_sport(sport_key)

    for game in games:
        home, away = game.get("home_team"), game.get("away_team")
        if not home or not away:
            continue
        commence_time = game.get("commence_time")

        for bm in game.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market.get("key") != market_key:
                    continue

                if market_key == "spreads":
                    margin = projected_margin(home, away)
                    ho = next((o for o in market["outcomes"] if o["name"] == home), None)
                    ao = next((o for o in market["outcomes"] if o["name"] == away), None)
                    if not ho or not ao:
                        continue
                    home_line = ho.get("point", 0)
                    h_dec, a_dec = american_to_decimal(ho["price"]), american_to_decimal(ao["price"])
                    fh, fa = remove_vig(decimal_to_implied_prob(h_dec), decimal_to_implied_prob(a_dec))
                    model_h = spread_prob(margin, home_line, std_dev)
                    for prob, mkt_prob, team, side, ln, dec, price in [
                        (model_h, fh, home, "home", home_line, h_dec, ho["price"]),
                        (1 - model_h, fa, away, "away", -home_line, a_dec, ao["price"]),
                    ]:
                        edge = (prob - mkt_prob) * 100
                        candidates.append({
                            "sport": sport_key, "home": home, "away": away, "commence_time": commence_time,
                            "market_label": "Spread", "pick": f"{team} {ln:+g}",
                            "model_prob": prob, "market_prob": mkt_prob, "edge": edge,
                            "odds_display": f"{price:+d} ({bm['title']})", "decimal_odds": dec,
                            "grade_type": "spread", "grade_side": side, "home_line": home_line,
                        })

                elif market_key == "h2h":
                    margin = projected_margin(home, away)
                    ho = next((o for o in market["outcomes"] if o["name"] == home), None)
                    ao = next((o for o in market["outcomes"] if o["name"] == away), None)
                    if not ho or not ao:
                        continue
                    h_dec, a_dec = american_to_decimal(ho["price"]), american_to_decimal(ao["price"])
                    fh, fa = remove_vig(decimal_to_implied_prob(h_dec), decimal_to_implied_prob(a_dec))
                    model_h = spread_prob(margin, 0, std_dev)
                    for prob, mkt_prob, team, side, dec, price in [
                        (model_h, fh, home, "home", h_dec, ho["price"]),
                        (1 - model_h, fa, away, "away", a_dec, ao["price"]),
                    ]:
                        edge = (prob - mkt_prob) * 100
                        candidates.append({
                            "sport": sport_key, "home": home, "away": away, "commence_time": commence_time,
                            "market_label": "Moneyline", "pick": team,
                            "model_prob": prob, "market_prob": mkt_prob, "edge": edge,
                            "odds_display": f"{price:+d} ({bm['title']})", "decimal_odds": dec,
                            "grade_type": "h2h", "grade_side": side, "home_line": 0,
                        })

                elif market_key == "totals":
                    lam_h, lam_a = expected_goals(home, away)
                    oo = next((o for o in market["outcomes"] if o["name"] == "Over"), None)
                    uo = next((o for o in market["outcomes"] if o["name"] == "Under"), None)
                    if not oo or not uo:
                        continue
                    line = oo.get("point", TOTALS_LINE_FALLBACK)
                    o_dec, u_dec = american_to_decimal(oo["price"]), american_to_decimal(uo["price"])
                    fo, fu = remove_vig(decimal_to_implied_prob(o_dec), decimal_to_implied_prob(u_dec))
                    model_under = total_under_prob(lam_h, lam_a, line)
                    for prob, mkt_prob, label, side, dec, price in [
                        (1 - model_under, fo, f"Over {line}", "over", o_dec, oo["price"]),
                        (model_under, fu, f"Under {line}", "under", u_dec, uo["price"]),
                    ]:
                        edge = (prob - mkt_prob) * 100
                        candidates.append({
                            "sport": sport_key, "home": home, "away": away, "commence_time": commence_time,
                            "market_label": "Total Goals", "pick": label,
                            "model_prob": prob, "market_prob": mkt_prob, "edge": edge,
                            "odds_display": f"{price:+d} ({bm['title']})", "decimal_odds": dec,
                            "grade_type": "total", "grade_side": side, "total_line": line,
                        })

                break

    return candidates


def post_top_picks(picks):
    if not picks:
        print("No positive-edge picks found this run — nothing posted.")
        return

    fields = []
    for i, p in enumerate(picks, 1):
        b = p["decimal_odds"] - 1
        full_kelly = max(0.0, (b * p["model_prob"] - (1 - p["model_prob"])) / b) if b > 0 else 0.0
        qk_pct = round(full_kelly * KELLY_FRACTION * 100, 2)

        sport_label = p["sport"].replace("_", " ").title()
        fields.append({
            "name": f"📜 Prophecy #{i} — {sport_label}",
            "value": (
                f"🏟️ **{p['home']} vs {p['away']}**\n"
                f"🔮 Pick: **{p['pick']}** ({p['market_label']})\n"
                f"📈 Odds: {p['odds_display']}\n"
                f"✨ Edge: +{p['edge']:.2f}%  ·  🛡️ Quarter-Kelly: {qk_pct}% of bankroll"
            ),
            "inline": False,
        })

    embed = {
        "title": "📜 Top 3 Prophecies of the Day",
        "color": OSRS_GOLD,
        "thumbnail": {"url": COIN_STACK_THUMB},
        "fields": fields,
        "footer": {"text": f"Model 28 scan · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"},
    }

    res = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    if res.status_code not in (200, 204):
        print(f"⚠️ Discord post failed ({res.status_code}): {res.text[:200]}")
    else:
        print(f"✅ Posted top {len(picks)} pick(s) to Discord.")


def log_picks(picks):
    log = []
    if os.path.exists(PICKS_LOG_FILE):
        try:
            with open(PICKS_LOG_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []

    now = datetime.now(timezone.utc).isoformat()
    for p in picks:
        log.append({
            "id": uuid.uuid4().hex[:12],
            "logged_at": now,
            "sport": p["sport"],
            "home": p["home"],
            "away": p["away"],
            "commence_time": p.get("commence_time"),
            "market_label": p["market_label"],
            "pick_display": p["pick"],
            "grade_type": p["grade_type"],
            "grade_side": p["grade_side"],
            "home_line": p.get("home_line"),
            "total_line": p.get("total_line"),
            "edge": p["edge"],
            "status": "pending",
        })

    with open(PICKS_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)
    print(f"Logged {len(picks)} new pick(s) to {PICKS_LOG_FILE} ({len(log)} total on file).")


def run():
    all_candidates = []
    for entry in SPORTS_TO_SCAN:
        try:
            all_candidates.extend(scan_one(entry["sport_key"], entry["market_key"]))
        except Exception as e:
            print(f"⚠️ Error scanning {entry}: {e}")

    positive = [c for c in all_candidates if c["edge"] > 0]
    positive.sort(key=lambda c: c["edge"], reverse=True)
    top_picks = positive[:TOP_N_TO_POST]

    print(f"\n{len(all_candidates)} total candidates, {len(positive)} positive-edge, posting top {len(top_picks)}")
    post_top_picks(top_picks)
    if top_picks:
        log_picks(top_picks)


if __name__ == "__main__":
    run()
