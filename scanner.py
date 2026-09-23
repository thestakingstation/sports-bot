name: Sports Value-Bet Scanner

on:
  schedule:
    # Runs daily at 13:00 UTC. Adjust the cron time to whenever you
    # want your Discord picks posted — cron is in UTC, not your local time.
    - cron: "0 13 * * *"
  workflow_dispatch:
    # Lets you trigger a run manually from GitHub's Actions tab too.

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run scanner
        env:
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: python scanner.py
