#!/usr/bin/env python3
"""Uploads a rendered short (from generate_short.py's output directory) to
YouTube as a public Short, using the refresh token minted by auth_setup.py."""

import argparse
import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATH = ROOT / "token.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CATEGORY_ID_PEOPLE_BLOGS = "22"  # People & Blogs; reasonable default for quote/history shorts


def load_credentials():
    if not TOKEN_PATH.exists():
        raise SystemExit(f"Missing {TOKEN_PATH}. Run scripts/auth_setup.py once first.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    return creds


def upload(run_dir: Path):
    video_path = run_dir / "short.mp4"
    metadata_path = run_dir / "metadata.json"
    if not video_path.exists():
        raise SystemExit(f"No video found at {video_path}")
    metadata = json.loads(metadata_path.read_text())

    creds = load_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": CATEGORY_ID_PEOPLE_BLOGS,
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"Uploaded: https://youtube.com/shorts/{video_id}")
    return video_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Output directory produced by generate_short.py")
    args = parser.parse_args()
    upload(Path(args.run_dir))


if __name__ == "__main__":
    main()
