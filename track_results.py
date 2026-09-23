import os
import json
import requests
from collections import defaultdict
from datetime import datetime, timezone

THE_ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "YOUR_THE_ODDS_API_KEY_HERE")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")

PICKS_LOG_FILE = "picks_log.json"
OSRS_GOLD = 0xC2A649
COIN_STACK_THUMB = "https://oldschool.runescape.wiki/images/Coins_10000.png"

SCORES_DAYS_FROM = 3


def fetch_scores(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
    params = {"apiKey": THE_ODDS_API_KEY, "daysFrom": SCORES_DAYS_FROM}
    res = requests.get(url, params=params, timeout=15)
    res.raise_for_status()
    return res.json()


def find_completed_match(scores, home, away):
    for g in scores:
        if not g.get("completed"):
            continue
        if g.get("home_team") == home and g.get("away_team") == away:
            score_list = g.get("scores")
            if not score_list:
                continue
            home_score = next((float(s["score"]) for s in score_list if s["name"] == home), None)
            away_score = next((float(s["score"]) for s in score_list if s["name"] == away), None)
            if home_score is not None and away_score is not None:
                return home_score, away_score
    return None


def grade_pick(pick, home_score, away_score):
    gtype = pick["grade_type"]

    if gtype == "spread":
        home_line = pick.get("home_line", 0)
        actual_home_margin = home_score - away_score
        if actual_home_margin == -home_line:
            return "push"
        home_covers = actual_home_margin > -home_line
        side_won = home_covers if pick["grade_side"] == "home" else (not home_covers)
        return "won" if side_won else "lost"

    if gtype == "h2h":
        if home_score == away_score:
            return "push"
        home_won = home_score > away_score
        side_won = home_won if pick["grade_side"] == "home" else (not home_won)
        return "won" if side_won else "lost"

    if gtype == "total":
        line = pick.get("total_line", 0)
        total = home_score + away_score
        if total == line:
            return "push"
        over_won = total > line
        side_won = over_won if pick["grade_side"] == "over" else (not over_won)
        return "won" if side_won else "lost"

    return "push"


def post_accuracy_report(log):
    graded = [p for p in log if p["status"] in ("won", "lost")]
    if not graded:
        print("No graded picks yet — skipping Discord accuracy post.")
        return

    wins = sum(1 for p in graded if p["status"] == "won")
    losses = sum(1 for p in graded if p["status"] == "lost")
    overall_pct = round(100 * wins / (wins + losses), 1) if (wins + losses) else 0.0

    per_sport = defaultdict(lambda: {"w": 0, "l": 0})
    for p in graded:
        per_sport[p["sport"]]["w" if p["status"] == "won" else "l"] += 1

    sport_fields = []
    for sport, rec in per_sport.items():
        total = rec["w"] + rec["l"]
        pct = round(100 * rec["w"] / total, 1) if total else 0.0
        sport_fields.append({
            "name": sport.replace("_", " ").title(),
            "value": f"{rec['w']}W - {rec['l']}L — {pct}%",
            "inline": True,
        })

    embed = {
        "title": "📊 Model 28 Proxy — Track Record",
        "color": OSRS_GOLD,
        "thumbnail": {"url": COIN_STACK_THUMB},
        "fields": [
            {"name": "Overall Record", "value": f"**{wins}W - {losses}L — {overall_pct}%**", "inline": False},
            *sport_fields,
        ],
        "footer": {"text": f"{len(graded)} picks graded to date · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"},
    }

    res = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    if res.status_code not in (200, 204):
        print(f"⚠️ Discord post failed ({res.status_code}): {res.text[:200]}")
    else:
        print("✅ Posted accuracy report to Discord.")


def main():
    if not os.path.exists(PICKS_LOG_FILE):
        print(f"No {PICKS_LOG_FILE} yet — nothing to grade.")
        return

    with open(PICKS_LOG_FILE) as f:
        log = json.load(f)

    pending = [p for p in log if p["status"] == "pending"]
    if not pending:
        print("No pending picks to grade.")
        post_accuracy_report(log)
        return

    sports_needed = {p["sport"] for p in pending}
    scores_by_sport = {}
    for sport_key in sports_needed:
        try:
            scores_by_sport[sport_key] = fetch_scores(sport_key)
            print(f"[{sport_key}] fetched scores for grading")
        except Exception as e:
            print(f"⚠️ Could not fetch scores for {sport_key}: {e}")
            scores_by_sport[sport_key] = []

    graded_count = 0
    for pick in log:
        if pick["status"] != "pending":
            continue
        scores = scores_by_sport.get(pick["sport"], [])
        match = find_completed_match(scores, pick["home"], pick["away"])
        if match is None:
            continue
        home_score, away_score = match
        result = grade_pick(pick, home_score, away_score)
        pick["status"] = result
        pick["graded_at"] = datetime.now(timezone.utc).isoformat()
        pick["final_score"] = f"{home_score:g}-{away_score:g}"
        graded_count += 1

    with open(PICKS_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

    print(f"\nGraded {graded_count} pick(s) this run.")
    post_accuracy_report(log)


if __name__ == "__main__":
    main()
