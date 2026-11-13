#!/usr/bin/env python3
"""Offline checks for nova_spotify.parse_request: which spoken requests take the
no-model Spotify path, and what query/kind they become. Plays nothing.

    python setup/spotify_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nova_spotify as ns  # noqa: E402

CASES = [
    ("Nova, play Blinding Lights on Spotify", ("Blinding Lights", "auto")),
    ("play blinding lights by the weeknd on spotify", ("blinding lights by the weeknd", "auto")),
    ("Play the album SOS by SZA", ("SOS by SZA", "album")),
    ("Nova play the album Abbey Road on Spotify please", ("Abbey Road", "album")),
    ("can you play the song Tsaka Nalang", ("Tsaka Nalang", "track")),
    ("put on my lofi playlist", ("lofi", "playlist")),
    ("play some Arthur Nery on Spotify", ("Arthur Nery", "auto")),
    ("play SOS album on spotify", ("SOS", "album")),
    ("Play the artist Zack Tabudlo", ("Zack Tabudlo", "artist")),
    ("throw on 1989 by Taylor Swift in Spotify.", ("1989 by Taylor Swift", "auto")),
    # Sir's own words, from the log (2026-11-13).
    ("Can you play an album for me, like a lo-fi album?", ("lo-fi", "album")),
    ("Can you just try again? Could you like play a lo-fi album from Spotify?", ("lo-fi", "album")),
    ("play Like a Prayer on spotify", ("Like a Prayer", "auto")),
    ("play the album For Me by someone", ("For Me by someone", "album")),
    ("play an album for me like a lofi album", ("lofi", "album")),
    ("could you play like some lofi album on spotify", ("lofi", "album")),
    ("play the song For Me on spotify", ("For Me", "track")),
    # Not Spotify requests: must fall through to the usual path.
    ("play Minecraft", None),
    ("play the video", None),
    ("play music", None),
    ("play it on spotify", None),
    ("what is playing on spotify", None),
    ("open spotify", None),
    ("pause", None),
]

bad = 0
for said, want in CASES:
    got = ns.parse_request(said)
    ok = got == want
    bad += not ok
    print(("PASS" if ok else "FAIL"), repr(said), "->", got, "" if ok else f"(wanted {want})")
for q, k, want in [("abbey road album", "auto", ("abbey road", "album")),
                   ("lofi beats", "auto", ("lofi beats", "track")),
                   ("SOS", "album", ("SOS", "album"))]:
    got = ns.guess_kind(q, k)
    bad += got != want
    print(("PASS" if got == want else "FAIL"), "guess_kind", q, k, "->", got)
# Picking a row: the real album beats the ukulele compilation Spotify ranked first.
class B:
    Name = "Play"
rows = [("The Beatles Complete On Ukulele", "Compilation", B), ("Abbey Road (Remastered 2009)", "Album", B),
        ("Abbey Road (Super Deluxe Edition)", "Album", B)]
got = ns._pick(rows, "album", "Abbey Road the Beatles")[0]
bad += got != "Abbey Road (Remastered 2009)"
print(("PASS" if got == "Abbey Road (Remastered 2009)" else "FAIL"), "pick album ->", got)
rows = [("SOS Deluxe: LANA", "Album", B), ("SOS", "Album", B)]
got = ns._pick(rows, "album", "SOS SZA")[0]
bad += got != "SOS"
print(("PASS" if got == "SOS" else "FAIL"), "pick SOS ->", got)

# Two requests at once: the second waits for the lock instead of fighting.
import threading, time
order = []
def hold(tag, secs):
    with ns._OneAtATime():
        order.append(tag + "+"); time.sleep(secs); order.append(tag + "-")
t = threading.Thread(target=hold, args=("a", 1.2)); t.start(); time.sleep(0.2)
hold("b", 0.1); t.join()
ok = order == ["a+", "a-", "b+", "b-"]
bad += not ok
print(("PASS" if ok else "FAIL"), "one at a time ->", order)
# Transport controls (no model); bare "stop"/"continue"/"go back" must NOT count.
for said, want in [("yo can u pause the spotify on my pc", "pause"), ("play it now", "resume"),
                   ("Nova, pause.", "pause"), ("can you stop the music please", "pause"),
                   ("skip this song", "next"), ("next song on spotify", "next"),
                   ("go back a song", "previous"), ("resume", "resume"),
                   ("stop", None), ("continue", None), ("go back", None), ("stop the server", None),
                   ("pause the download", None), ("Stop the Spotify control.", None),
                   ("play the album SOS by SZA", None)]:
    got = ns.parse_control(said)
    bad += got != want
    print(("PASS" if got == want else "FAIL"), "control", repr(said), "->", got)
print("ALL PASS" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
