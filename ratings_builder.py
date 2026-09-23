import json
import io
import requests
import pandas as pd

RATINGS_FILE = "ratings.json"

def build_nfl_power_ratings():
    import nfl_data_py as nfl

    seasons = [2024, 2025]
    current_season = max(seasons)
    current_weight = 2.0
    home_field_adjustment = 2.5

    schedules = nfl.import_schedules(seasons)
    played = schedules.dropna(subset=["home_score", "away_score"]).copy()
    print(f"[NFL] Loaded {len(played)} completed games across seasons {seasons}")

    records = []
    for _, row in played.iterrows():
        weight = current_weight if row["season"] == current_season else 1.0
        home_margin = (row["home_score"] - row["away_score"]) - home_field_adjustment
        away_margin = (row["away_score"] - row["home_score"]) + home_field_adjustment
        records.append({"team": row["home_team"], "margin": home_margin, "weight": weight})
        records.append({"team": row["away_team"], "margin": away_margin, "weight": weight})

    if not records:
        print("[NFL] No completed games found — power_ratings left empty.")
        return {}

    df = pd.DataFrame(records)

    def weighted_avg(group):
        return (group["margin"] * group["weight"]).sum() / group["weight"].sum()

    ratings = df.groupby("team").apply(weighted_avg).round(2)
    return {team: float(val) for team, val in ratings.items()}


def build_epl_ratings():
    season_codes = ["2526", "2627"]
    current_season_code = "2627"
    current_weight = 2.0
    league_code = "E0"

    frames = []
    for scode in season_codes:
        url = f"https://www.football-data.co.uk/mmz4281/{scode}/{league_code}.csv"
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200 or len(resp.content) < 100:
            print(f"[EPL] No data for season {scode} — skipping")
            continue
        df = pd.read_csv(io.BytesIO(resp.content), encoding="latin-1")
        df["season_code"] = scode
        frames.append(df)
        print(f"[EPL] Loaded {len(df)} matches for season {scode}")

    if not frames:
        print("[EPL] No data at all — using neutral defaults.")
        return 1.5, 1.2, {}, {}

    matches = pd.concat(frames, ignore_index=True)
    matches = matches.dropna(subset=["FTHG", "FTAG", "HomeTeam", "AwayTeam"])

    league_avg_home_goals = float(matches["FTHG"].mean())
    league_avg_away_goals = float(matches["FTAG"].mean())

    matches["weight"] = matches["season_code"].apply(
        lambda s: current_weight if s == current_season_code else 1.0
    )

    teams = pd.unique(matches[["HomeTeam", "AwayTeam"]].values.ravel())
    attack, defense = {}, {}
    league_avg_overall = (league_avg_home_goals + league_avg_away_goals) / 2

    for team in teams:
        home_games = matches[matches["HomeTeam"] == team]
        away_games = matches[matches["AwayTeam"] == team]

        goals_scored = (
            (home_games["FTHG"] * home_games["weight"]).sum()
            + (away_games["FTAG"] * away_games["weight"]).sum()
        )
        goals_conceded = (
            (home_games["FTAG"] * home_games["weight"]).sum()
            + (away_games["FTHG"] * away_games["weight"]).sum()
        )
        total_weight = home_games["weight"].sum() + away_games["weight"].sum()
        if total_weight == 0:
            continue

        attack[team] = round(float((goals_scored / total_weight) / league_avg_overall), 3)
        defense[team] = round(float((goals_conceded / total_weight) / league_avg_overall), 3)

    return league_avg_home_goals, league_avg_away_goals, attack, defense


def main():
    try:
        power_ratings = build_nfl_power_ratings()
    except Exception as e:
        print(f"⚠️ NFL ratings build failed: {e} — keeping any existing NFL ratings.")
        power_ratings = None

    try:
        lah, laa, attack, defense = build_epl_ratings()
    except Exception as e:
        print(f"⚠️ EPL ratings build failed: {e} — keeping any existing EPL ratings.")
        lah = laa = attack = defense = None

    existing = {}
    try:
        with open(RATINGS_FILE) as f:
            existing = json.load(f)
    except Exception:
        pass

    output = {
        "power_ratings": power_ratings if power_ratings is not None else existing.get("power_ratings", {}),
        "league_avg_home_goals": lah if lah is not None else existing.get("league_avg_home_goals", 1.5),
        "league_avg_away_goals": laa if laa is not None else existing.get("league_avg_away_goals", 1.2),
        "team_attack_strength": attack if attack is not None else existing.get("team_attack_strength", {}),
        "team_defense_strength": defense if defense is not None else existing.get("team_defense_strength", {}),
    }

    with open(RATINGS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {RATINGS_FILE}: {len(output['power_ratings'])} NFL teams, "
          f"{len(output['team_attack_strength'])} EPL teams.")


if __name__ == "__main__":
    main()
