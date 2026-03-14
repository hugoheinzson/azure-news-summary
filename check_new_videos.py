#!/usr/bin/env python3
"""
Checks the Azure AI Foundry YouTube playlist for new videos,
gets their transcripts, summarizes them with Claude, and sends a German
summary via Gmail.

Required environment variables (set as GitHub Secrets):
  ANTHROPIC_API_KEY   - Anthropic Claude API key
  GMAIL_ADDRESS       - Gmail address to send from
  GMAIL_APP_PASSWORD  - Gmail App Password (not your regular password)
  RECIPIENT_EMAIL     - Recipient email(s), comma-separated
"""

import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import feedparser
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

PLAYLIST_ID = "PLlVtbbG169nEv7jSfOVmQGRp9wAoAM0Ks"
PLAYLIST_URL = f"https://youtube.com/playlist?list={PLAYLIST_ID}"
RSS_FEED_URL = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}"
SEEN_VIDEOS_FILE = Path("seen_videos.json")
MAX_TRANSCRIPT_CHARS = 15_000


def load_seen_videos() -> dict:
    if SEEN_VIDEOS_FILE.exists():
        return json.loads(SEEN_VIDEOS_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen_videos(seen: dict) -> None:
    SEEN_VIDEOS_FILE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def fetch_playlist_videos() -> list[dict]:
    """Fetches the latest videos from the playlist RSS feed (no API key needed)."""
    feed = feedparser.parse(RSS_FEED_URL)
    if feed.bozo:
        print(f"Warnung: RSS-Feed konnte nicht vollständig geparst werden: {feed.bozo_exception}", file=sys.stderr)

    videos = []
    for entry in feed.entries:
        # entry.id format: "yt:video:VIDEO_ID"
        video_id = entry.id.split(":")[-1]
        videos.append(
            {
                "id": video_id,
                "title": entry.get("title", "Unbekannter Titel"),
                "published": entry.get("published", ""),
                "link": entry.get("link", f"https://www.youtube.com/watch?v={video_id}"),
            }
        )
    print(f"{len(videos)} Videos im Playlist-Feed gefunden.")
    return videos


def get_transcript(video_id: str) -> str | None:
    """Tries to get the transcript for a video (English or German, manual or auto-generated)."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # Prefer manually created transcripts, fall back to auto-generated
        try:
            transcript = transcript_list.find_manually_created_transcript(["en", "de"])
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(["en", "de"])
        entries = transcript.fetch()
        return " ".join(e["text"] for e in entries)
    except TranscriptsDisabled:
        print(f"  Transkripte für Video {video_id} deaktiviert.")
        return None
    except NoTranscriptFound:
        print(f"  Kein englisches/deutsches Transkript für Video {video_id} gefunden.")
        return None
    except Exception as e:
        print(f"  Fehler beim Abrufen des Transkripts für {video_id}: {e}", file=sys.stderr)
        return None


def summarize_transcript(title: str, transcript: str) -> str:
    """Sends the transcript to Claude and returns a German summary."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    truncated = transcript[:MAX_TRANSCRIPT_CHARS]
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        print(f"  Transkript auf {MAX_TRANSCRIPT_CHARS} Zeichen gekürzt.")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": (
                    "Du bist ein technischer Redakteur für ein Azure/AI-Team.\n"
                    "Erstelle eine prägnante deutsche Zusammenfassung des folgenden "
                    "YouTube-Videos über Azure AI Foundry.\n\n"
                    f"Video-Titel: {title}\n\n"
                    f"Transkript:\n{truncated}\n\n"
                    "Bitte strukturiere die Zusammenfassung exakt so:\n\n"
                    "**Kurzzusammenfassung**\n"
                    "(2–3 Sätze zum Kern des Videos)\n\n"
                    "**Wichtigste Neuigkeiten / Features**\n"
                    "(Stichpunkte mit den zentralen Aussagen)\n\n"
                    "**Relevanz für unser Team**\n"
                    "(1–2 Sätze, was das konkret für uns bedeutet)\n"
                ),
            }
        ],
    )
    return message.content[0].text


def send_email(video: dict, summary: str) -> None:
    """Sends the summary as an HTML email via Gmail SMTP."""
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    recipients_raw = os.environ["RECIPIENT_EMAIL"]
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    subject = f"Azure AI Foundry Update: {video['title']}"

    plain_body = (
        f"Neues Video in der Azure AI Foundry Playlist\n\n"
        f"Titel: {video['title']}\n"
        f"Link:  {video['link']}\n"
        f"Veröffentlicht: {video['published']}\n\n"
        f"{summary}\n\n"
        f"---\nAutomatisch generiert · {PLAYLIST_URL}"
    )

    summary_html = summary.replace("\n\n", "</p><p>").replace("\n", "<br>")
    summary_html = summary_html.replace("**", "<strong>", 1)
    # Simple bold replacement for markdown **text**
    import re
    summary_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", summary_html)

    html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; color: #333;">
  <h2 style="color: #0078d4;">📺 Neues Azure AI Foundry Video</h2>
  <p>
    <strong>Titel:</strong> <a href="{video['link']}" style="color: #0078d4;">{video['title']}</a><br>
    <strong>Veröffentlicht:</strong> {video['published']}
  </p>
  <hr style="border: none; border-top: 1px solid #ddd;">
  <p>{summary_html}</p>
  <hr style="border: none; border-top: 1px solid #ddd;">
  <p style="font-size: 0.8em; color: #999;">
    Automatisch generiert · <a href="{PLAYLIST_URL}">Zur Playlist</a>
  </p>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, recipients, msg.as_string())

    print(f"  Mail gesendet an: {', '.join(recipients)}")


def main() -> None:
    seen = load_seen_videos()
    videos = fetch_playlist_videos()

    # Nur das aktuellste Video (RSS-Feed liefert neuestes zuerst)
    video = videos[0]

    if video["id"] in seen:
        print(f"Aktuellstes Video bereits verarbeitet: {video['title']}")
        return

    print(f"Neues aktuellstes Video gefunden: {video['title']} ({video['id']})")

    transcript = get_transcript(video["id"])

    if not transcript:
        print("  Übersprungen (kein Transkript verfügbar).")
        seen[video["id"]] = {"title": video["title"], "status": "no_transcript"}
        save_seen_videos(seen)
        return

    print(f"  Transkript erhalten ({len(transcript)} Zeichen). Erstelle Summary …")
    summary = summarize_transcript(video["title"], transcript)

    send_email(video, summary)

    seen[video["id"]] = {"title": video["title"], "status": "sent"}
    save_seen_videos(seen)
    print("\nseen_videos.json aktualisiert.")


if __name__ == "__main__":
    main()
