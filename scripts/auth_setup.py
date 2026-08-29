#!/usr/bin/env python3
"""ONE-TIME local setup: mints a YouTube Data API refresh token.

Run this once per machine/repo after creating an OAuth Client ID (Desktop app)
in Google Cloud Console and downloading its client_secret.json into this
project's root. It opens a browser for you to log into the Google account
that owns the target YouTube channel and click Allow, then saves token.json.
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET_PATH = ROOT / "client_secret.json"
TOKEN_PATH = ROOT / "token.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    if not CLIENT_SECRET_PATH.exists():
        print(f"Missing {CLIENT_SECRET_PATH}. Download it from Google Cloud Console "
              f"(APIs & Services > Credentials > your OAuth Client ID > Download JSON) "
              f"and save it there first.", file=sys.stderr)
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent")

    TOKEN_PATH.write_text(credentials.to_json())
    print(f"Saved refresh token to {TOKEN_PATH}")
    print("Keep this file secret - it grants upload access to the channel.")
    print("Next: push it (and client_secret.json) to GitHub Secrets, not to the repo.")


if __name__ == "__main__":
    main()
