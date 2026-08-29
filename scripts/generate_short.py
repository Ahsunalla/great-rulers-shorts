#!/usr/bin/env python3
"""Generates one vertical YouTube Short: a historical quote, narrated aloud,
over a Ken-Burns-animated public-domain portrait with word-synced captions
and a royalty-free music bed."""

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import edge_tts
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
PORTRAITS_DIR = ASSETS_DIR / "portraits"
MUSIC_DIR = ASSETS_DIR / "music"
FONTS_DIR = ASSETS_DIR / "fonts"

VOICE = "en-US-GuyNeural"
FPS = 30
WIDTH, HEIGHT = 1080, 1920
PRE_ROLL = 1.3
POST_ROLL = 2.6
MUSIC_VOLUME = 0.14

MOODS = ["triumphant", "battle", "somber"]

# Wikipedia article titles that differ from how the person is credited in quotes.json.
WIKI_TITLE_OVERRIDES = {
    "Hannibal Barca": "Hannibal",
    "Napoleon Bonaparte": "Napoleon",
}

# Manual corrections when a person's Wikipedia lead image turns out to be
# wrong, ambiguous, or not actually freely licensed (verified by hand -
# e.g. Winston Churchill's lead image is a still-copyrighted Karsh photo
# mislabeled "public domain" on Commons). Exact Commons File: title.
MANUAL_FILE_OVERRIDES = {
    "Winston Churchill": "File:Winston Churchill in his uniform as an Oxfordshire Hussar c1910.jpg",
}


def find_ffmpeg():
    candidates = [
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        shutil.which("ffmpeg"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return "ffmpeg"


FFMPEG = find_ffmpeg()


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def find_ffprobe():
    ffmpeg_path = Path(FFMPEG)
    sibling = ffmpeg_path.parent / "ffprobe"
    if ffmpeg_path.parent != Path(".") and sibling.exists():
        return str(sibling)
    found = shutil.which("ffprobe")
    return found or "ffprobe"


FFPROBE = find_ffprobe()


def ffprobe_duration(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def http_get_with_retry(url, headers, params=None, retries=6, timeout=20, want_image=False):
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=headers)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and (not want_image or ctype.startswith("image/")):
                return resp
            last_exc = RuntimeError(f"Unexpected response {resp.status_code} ({ctype}) from {url}")
        except requests.RequestException as exc:
            last_exc = exc
        time.sleep(4 * (attempt + 1))
    raise last_exc or RuntimeError(f"Failed to fetch {url}")


def fetch_wikipedia_lead_image_url(person, headers):
    title = WIKI_TITLE_OVERRIDES.get(person, person)
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}"
    try:
        resp = http_get_with_retry(url, headers, timeout=15)
    except (requests.RequestException, RuntimeError):
        return None
    data = resp.json()
    thumb = data.get("thumbnail") or data.get("originalimage")
    if not thumb or not thumb.get("source"):
        return None
    img_url = thumb["source"]
    m = re.search(r"/(\d+)px-", img_url)
    if m:
        img_url = img_url.replace(f"/{m.group(1)}px-", "/1200px-")
    return img_url


def fetch_manual_override_url(person, headers):
    file_title = MANUAL_FILE_OVERRIDES.get(person)
    if not file_title:
        return None
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query", "titles": file_title, "format": "json",
        "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 1200,
    }
    resp = http_get_with_retry(api, headers, params=params)
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if url:
            return url
    return None


def fetch_portrait(person, query):
    slug = slugify(person)
    for ext in (".jpg", ".png", ".jpeg"):
        cached = PORTRAITS_DIR / f"{slug}{ext}"
        if cached.exists():
            return cached
    PORTRAITS_DIR.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "great-rulers-shorts/1.0 (personal, non-commercial automation project; contact via GitHub)"
    }

    def save(img_resp, url):
        ctype = img_resp.headers.get("content-type", "")
        ext = ".png" if "png" in ctype else ".jpg"
        path = PORTRAITS_DIR / f"{slug}{ext}"
        path.write_bytes(img_resp.content)
        return path

    manual_url = fetch_manual_override_url(person, headers)
    if manual_url:
        img_resp = http_get_with_retry(manual_url, headers, timeout=30, want_image=True)
        return save(img_resp, manual_url)

    wiki_url = fetch_wikipedia_lead_image_url(person, headers)
    if wiki_url:
        try:
            img_resp = http_get_with_retry(wiki_url, headers, timeout=30, want_image=True)
            return save(img_resp, wiki_url)
        except (requests.RequestException, RuntimeError):
            pass

    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6,
        "gsrlimit": 10,
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "iiurlwidth": 1200,
    }
    resp = http_get_with_retry(api, headers, params=params)
    pages = resp.json().get("query", {}).get("pages", {})
    best = None
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        width, height = info.get("width", 0), info.get("height", 0)
        page_title = page.get("title", "")
        if not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        if width < 500 or height < 500:
            continue
        if re.search(r"\bor\b", page_title, re.IGNORECASE):
            continue  # e.g. "Portrait of Trajan or Julius Caesar" - disputed/ambiguous attribution
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        area = width * height
        if best is None or area > best[0]:
            best = (area, url)
    if best is None:
        raise RuntimeError(f"No suitable Wikimedia Commons image found for query: {query!r}")
    img_resp = http_get_with_retry(best[1], headers, timeout=30, want_image=True)
    return save(img_resp, best[1])


async def synthesize(text, out_path):
    communicate = edge_tts.Communicate(text, VOICE, boundary="WordBoundary")
    submaker = edge_tts.SubMaker()
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)
    return submaker.cues


def wrap_lines(cues, max_chars=24, max_words=6):
    lines, current, current_len = [], [], 0
    for cue in cues:
        word = cue.content
        wlen = len(word) + 1
        if current and (current_len + wlen > max_chars or len(current) >= max_words):
            lines.append(current)
            current, current_len = [], 0
        current.append(cue)
        current_len += wlen
    if current:
        lines.append(current)
    return lines


def fmt_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Quote,Anton,66,&H00FFFFFF,&H00808080,&H00101010,&H40000000,0,0,0,0,100,100,1,0,3,4,0,5,80,80,0,1
Style: Card,Oswald,54,&H00FFFFFF,&H00FFFFFF,&H00101010,&H40000000,0,0,0,0,100,100,0,0,3,3,0,5,80,80,0,1
Style: CTA,Oswald,38,&H00E8E8E8,&H00E8E8E8,&H00101010,&H50000000,0,0,0,0,100,100,0,0,3,2,0,2,80,80,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def escape_ass_text(text):
    return text.replace("{", "").replace("}", "")


def build_ass(person, title, lines, pre_roll, total_duration, path):
    events = []
    events.append(
        f"Dialogue: 0,{fmt_time(0)},{fmt_time(pre_roll)},Card,,0,0,0,,"
        f"{{\\fad(200,150)}}{escape_ass_text(person)}\\N{{\\fs36}}{escape_ass_text(title)}"
    )

    line_windows = []
    for li, line in enumerate(lines):
        line_start = pre_roll + line[0].start.total_seconds()
        if li == len(lines) - 1:
            line_end = pre_roll + line[-1].end.total_seconds() + 1.4
        else:
            next_start = pre_roll + lines[li + 1][0].start.total_seconds()
            line_end = max(pre_roll + line[-1].end.total_seconds() + 0.2, next_start)
        line_windows.append((line_start, line_end))

    for (line_start, line_end), line in zip(line_windows, lines):
        parts = []
        for wi, cue in enumerate(line):
            w_start = pre_roll + cue.start.total_seconds()
            seg_end = (pre_roll + line[wi + 1].start.total_seconds()) if wi < len(line) - 1 else line_end
            dur_cs = max(1, round((seg_end - w_start) * 100))
            parts.append(f"{{\\k{dur_cs}}}{escape_ass_text(cue.content)}")
        text = " ".join(parts)
        events.append(f"Dialogue: 0,{fmt_time(line_start)},{fmt_time(line_end)},Quote,,0,0,0,,{text}")

    outro_start = line_windows[-1][1] + 0.3
    events.append(
        f"Dialogue: 0,{fmt_time(outro_start)},{fmt_time(total_duration)},Card,,0,0,0,,"
        f"{{\\fad(150,0)}}{escape_ass_text(person)}\\N{{\\fs36}}{escape_ass_text(title)}"
    )
    events.append(
        f"Dialogue: 0,{fmt_time(outro_start)},{fmt_time(total_duration)},CTA,,0,0,0,,"
        f"{{\\fad(300,0)}}Follow for daily history"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("\n".join(events) + "\n")


def pick_music(mood, state):
    music_meta = load_json(MUSIC_DIR / "music.json")
    pool = [t for t in music_meta["tracks"] if t["mood"] == mood] or music_meta["tracks"]
    counters = state.setdefault("mood_counters", {})
    idx = counters.get(mood, 0)
    track = pool[idx % len(pool)]
    counters[mood] = idx + 1
    return MUSIC_DIR / track["file"], music_meta["attribution"], track["title"]


def escape_filter_path(path):
    p = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return p


def render_video(portrait_path, narration_path, music_path, ass_path, total_duration, out_path):
    frames = max(1, round(total_duration * FPS))
    pre_ms = round(PRE_ROLL * 1000)
    fade_out_start = max(0.0, total_duration - 1.5)

    vf = (
        f"scale=2160:3840:force_original_aspect_ratio=increase,"
        f"crop=2160:3840,"
        f"zoompan=z='min(zoom+0.0008,1.4)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"format=yuv420p,eq=brightness=-0.02:saturation=1.08,"
        f"subtitles=filename='{escape_filter_path(ass_path)}':fontsdir='{escape_filter_path(FONTS_DIR)}'"
    )
    af_narr = f"[1:a]adelay={pre_ms}:all=1,apad=whole_dur={total_duration}[narr]"
    af_music = (
        f"[2:a]atrim=0:{total_duration},volume={MUSIC_VOLUME},"
        f"afade=t=in:st=0:d=1.0,afade=t=out:st={fade_out_start}:d=1.5[music]"
    )
    af_mix = "[narr][music]amix=inputs=2:duration=longest:normalize=0[aout]"

    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(portrait_path),
        "-i", str(narration_path),
        "-i", str(music_path),
        "-filter_complex", f"[0:v]{vf}[vout];{af_narr};{af_music};{af_mix}",
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(total_duration),
        "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def build_metadata(quote, music_title, attribution):
    person = quote["person"]
    hook = quote["quote"]
    title = f"{person} — \"{hook[:60]}{'…' if len(hook) > 60 else ''}\" #Shorts"
    if len(title) > 100:
        title = f"{person} #Shorts #History"
    description = (
        f'"{hook}"\n— {person}, {quote["title"]}\n\n'
        f"#Shorts #History #Quotes #{slugify(person).title().replace('_', '')}\n\n"
        f"Music: {music_title} — {attribution}"
    )
    tags = ["history", "quotes", "shorts", person, "great generals", "great rulers"]
    return {"title": title, "description": description, "tags": tags}


async def main_async(args):
    quotes = load_json(DATA_DIR / "quotes.json")
    state_path = DATA_DIR / "state.json"
    state = load_json(state_path)

    idx = args.index if args.index is not None else state["next_quote_index"] % len(quotes)
    quote = quotes[idx % len(quotes)]
    print(f"[generate] Quote {quote['id']}: {quote['person']} — {quote['quote'][:50]}...")

    portrait_path = fetch_portrait(quote["person"], quote["commons_query"])
    print(f"[generate] Portrait: {portrait_path}")

    run_dir = ROOT / "output" / f"{quote['id']}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    narration_path = run_dir / "narration.mp3"
    ass_path = run_dir / "captions.ass"
    video_path = run_dir / "short.mp4"

    cues = await synthesize(quote["quote"], narration_path)
    if not cues:
        raise RuntimeError("edge-tts returned no word boundaries — narration may have failed")
    narration_duration = ffprobe_duration(narration_path)
    print(f"[generate] Narration: {narration_duration:.1f}s, {len(cues)} words")

    total_duration = PRE_ROLL + narration_duration + POST_ROLL
    lines = wrap_lines(cues)
    build_ass(quote["person"], quote["title"], lines, PRE_ROLL, total_duration, ass_path)

    music_path, attribution, music_title = pick_music(quote["mood"], state)
    print(f"[generate] Music: {music_title} ({quote['mood']})")

    render_video(portrait_path, narration_path, music_path, ass_path, total_duration, video_path)
    print(f"[generate] Video: {video_path} ({total_duration:.1f}s)")

    metadata = build_metadata(quote, music_title, attribution)
    save_json(run_dir / "metadata.json", metadata)

    if not args.dry_run:
        state["next_quote_index"] = (idx + 1) % len(quotes)
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        state["total_runs"] = state.get("total_runs", 0) + 1
        save_json(state_path, state)

    print(f"[generate] Done: {run_dir}")
    print(run_dir)
    return run_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=None, help="Force a specific quote index (0-based), ignoring rotation state")
    parser.add_argument("--dry-run", action="store_true", help="Do not advance/save rotation state")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
