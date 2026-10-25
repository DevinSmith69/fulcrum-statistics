"""Render the demo video's voiceover with Azure AI Speech (one WAV per script beat).

    uv run --env-file .env python scripts/video/narrate.py [--voice en-GB-RyanNeural] [--out video/narration]

Uses the same Speech resource and Entra auth as the app (`fulcrum.api.speech`). Each beat is rendered as 48 kHz
16-bit mono PCM and its length is printed next to the storyboard slot it has to fit. The text is the script in
the video doc; SSML adds pauses and spells out "xG" and "azd" the way they should be heard.
"""

from __future__ import annotations

import argparse
import asyncio
import wave
from pathlib import Path

import httpx

from fulcrum.api.speech import SpeechSynth

OUTPUT_FORMAT = "riff-48khz-16bit-mono-pcm"

# (file stem, slot in seconds, SSML body). <break/> = a beat for the picture to land.
BEATS: list[tuple[str, float, str]] = [
    (
        "01_hook",
        12,
        "A goal goes in, and the win probability leaps. <break time='350ms'/>"
        "But why did the match turn <emphasis level='moderate'>here</emphasis>? <break time='300ms'/>"
        "Fulcrum answers that live, for every moment that matters.",
    ),
    (
        "02_how_it_works",
        15,
        "A synthetic match streams through Azure Event Hubs to an engine on Container Apps "
        "that computes <sub alias='x G'>xG</sub>, pressure and live win probability, "
        "and flags the moments that change the game. <break time='300ms'/>"
        "Agents on Microsoft Foundry, orchestrated with Agent Framework, explain them.",
    ),
    (
        "03_trust",
        18,
        "A big chance. <break time='400ms'/>"
        "About four seconds later, the card explains it: the chance quality, and the swing in win probability. "
        "<break time='400ms'/>Open the fact trail. <break time='250ms'/>"
        "An analyst agent wrote it, an editor rewrote it, "
        "and a fact-checker matched every number to the engine's facts.",
    ),
    (
        "04a_personalise",
        13,
        "The same moment, two viewers. <break time='300ms'/>"
        "On the left, an analyst in English. <break time='250ms'/>"
        "On the right, a casual fan in Spanish: rewritten, translated on demand, and checked again.",
    ),
    ("04c_personas", 4, "Four personas, five languages, any club, any player."),
    (
        "05_broadcast_producer",
        17,
        "For broadcasters, every insight is also an overlay cue, with template, position, priority and timing, "
        "that a graphics partner can render without our interface. <break time='350ms'/>"
        "And a producer can ask for a graphic in plain English. <break time='200ms'/>"
        "It's fact-checked before it airs.",
    ),
    (
        "06_full_time",
        13,
        "At full time: who really held momentum, <break time='150ms'/>a fact-checked recap for your persona, "
        "share posts, <break time='150ms'/>and a highlights reel cut just for you.",
    ),
    (
        "07_close",
        17,
        "Across two full matches on Foundry, fifty-nine of sixty insights passed the fact-check, "
        "and the last fell back to verified copy. <break time='250ms'/>"
        "Groundedness: four point three out of five. <break time='300ms'/>"
        "One <sub alias='A Z D up'>azd up</sub> deploys it. <break time='500ms'/>"
        "Fulcrum: <break time='200ms'/>the AI explains the numbers. <break time='250ms'/>It never invents them.",
    ),
    (  # alternative close: matches the live Quality shot (27 of 27 passed on the first draft)
        "07_close_live",
        17,
        "Live on Azure, all twenty-seven insights passed the fact-check on the first draft. <break time='250ms'/>"
        "In our Foundry evaluations, groundedness scored four point three out of five. <break time='300ms'/>"
        "One <sub alias='A Z D up'>azd up</sub> deploys it. <break time='450ms'/>"
        "Fulcrum: <break time='200ms'/>the AI explains the numbers. <break time='250ms'/>It never invents them.",
    ),
]


def ssml(body: str, voice: str, rate: str) -> str:
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='en-GB'>"
        f"<voice name='{voice}'><prosody rate='{rate}'>{body}</prosody></voice></speak>"
    )


def seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--voice", default="en-GB-RyanNeural")
    ap.add_argument("--rate", default="+0%", help="SSML prosody rate, e.g. +5%%")
    ap.add_argument("--out", default="video/narration")
    ap.add_argument("--only", nargs="*", help="file stems to render (default: all)")
    args = ap.parse_args()

    synth = SpeechSynth.from_env()
    if synth is None:
        raise SystemExit("Azure Speech is not configured (AZURE_SPEECH_ENDPOINT/REGION + key or resource id)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 (a one-off CLI)
    total = 0.0
    async with httpx.AsyncClient(timeout=30) as client:
        for stem, slot, body in BEATS:
            if args.only and stem not in args.only:
                continue
            headers = await synth._headers()
            headers["X-Microsoft-OutputFormat"] = OUTPUT_FORMAT
            r = await client.post(synth.config.url, content=ssml(body, args.voice, args.rate).encode(), headers=headers)
            r.raise_for_status()
            path = out / f"{stem}.wav"
            path.write_bytes(r.content)
            took = seconds(path)
            total += took
            flag = "OK " if took <= slot else "LONG"
            print(f"{flag} {stem:24} {took:5.1f} s  (slot {slot:4.1f} s)")
    await synth.close()
    print(f"total speech {total:.1f} s")


if __name__ == "__main__":
    asyncio.run(main())
