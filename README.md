# Great Rulers & Generals — Daily Shorts

Fully automated pipeline: picks the next quote from a historical ruler or
general, narrates it aloud, animates it over a verified public-domain
portrait with word-synced captions and a royalty-free music bed, and uploads
it to YouTube as a Short. Runs 3x/day via GitHub Actions — no manual work
once set up.

## How it works

- `data/quotes.json` — curated, source-checked quotes. `data/state.json`
  tracks rotation so it cycles through without repeats.
- `scripts/generate_short.py` — renders one vertical MP4: fetches/caches a
  portrait, narrates via `edge-tts` (free, no API key), builds animated
  captions timed to the actual narration, mixes in music, renders with
  ffmpeg.
- `scripts/upload_youtube.py` — uploads the rendered MP4 to YouTube.
- `.github/workflows/daily-shorts.yml` — runs the above 3x/day for free on
  GitHub's hosted runners.

## One-time setup

1. **Google Cloud OAuth** (required once, must be done by the channel owner):
   - Go to [Google Cloud Console](https://console.cloud.google.com/), create a project.
   - APIs & Services > Library > enable "YouTube Data API v3".
   - APIs & Services > OAuth consent screen > External > fill in basic info
     > add your own Google account as a test user (or publish the app to
     avoid the 7-day test-token expiry).
   - APIs & Services > Credentials > Create Credentials > OAuth client ID >
     Application type: Desktop app. Download the JSON, save it as
     `client_secret.json` in this project's root.
2. **Mint the refresh token** (run locally, once):
   ```bash
   pip install -r requirements.txt
   python scripts/auth_setup.py
   ```
   This opens a browser — log into the Google account that owns the target
   YouTube channel and click Allow. Saves `token.json`.
3. **Push secrets to GitHub** (never commit these files):
   ```bash
   gh secret set YT_CLIENT_SECRET_JSON < client_secret.json
   gh secret set YT_TOKEN_JSON < token.json
   ```
4. **Test it**: Actions tab > "Daily Shorts" > Run workflow, then check the
   channel for the upload.

## Adding more quotes later

Append entries to `data/quotes.json` (same shape as existing ones). Verify
the quote's sourcing and, if it's a new person, sanity-check the portrait
`generate_short.py` picks (Wikipedia's lead image) before trusting it long
term — see `MANUAL_FILE_OVERRIDES` in the script for how to correct a bad
pick.
