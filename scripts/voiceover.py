"""Voiced demo, in the docs/DEMO.md beat order: one OpenAI TTS clip per beat, captions, silent and voiced MP4s.

  python3 scripts/voiceover.py --clips   # TTS clips only (cached), then demo/vo/durations.json for the recorder
  node scripts/record_demo.cjs            # records demo/raw-demo.mp4 and demo/beats.json, holding each beat for its clip
  python3 scripts/voiceover.py           # captions + demo/patientzero-demo.mp4 (silent) + demo/patientzero-demo-voiced.mp4

Narration comes from the table in demo/voiceover.md. Clips are cached in demo/vo/ by hash of (model, voice,
instructions, text), so only changed lines cost a call. Needs OPENAI_API_KEY in the environment (never printed).
"""
import hashlib, json, os, re, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
VO = DEMO / "vo"
RAW = DEMO / "raw-demo.mp4"
SILENT = DEMO / "patientzero-demo.mp4"
OUT = DEMO / "patientzero-demo-voiced.mp4"
SRT = DEMO / "patientzero-demo.srt"
MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.environ.get("TTS_VOICE", "onyx")
INSTR = ("Voice: a calm, confident startup founder giving a live product demo. "
         "Pace: brisk and steady, about 160 words per minute, short natural pauses only. "
         "Tone: warm, assured, plain-spoken, never salesy or dramatic.")
LEAD = 0.3
FONTS = "/usr/share/fonts/truetype/sand-box/google/Inter"
STYLE = ("FontName=Inter,FontSize=7.5,PrimaryColour=&H00FFFFFF,OutlineColour=&H30141210,BackColour=&H30141210,"
         "BorderStyle=3,Outline=4,Shadow=0,MarginV=7,Bold=0")


def dur(f):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]))


def lines():
    """beat name -> narration, from the table rows of demo/voiceover.md (| beat | DEMO.md | narration |)."""
    out = {}
    for row in (DEMO / "voiceover.md").read_text().splitlines():
        m = re.match(r"\|\s*([a-z]+)\s*\|\s*\d:\d\d [^|]+\|\s*(.+?)\s*\|\s*$", row)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def tts(text, path):
    body = json.dumps({"model": MODEL, "voice": VOICE, "input": text, "instructions": INSTR, "response_format": "wav"}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", body,
                                 {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    path.write_bytes(urllib.request.urlopen(req, timeout=120).read())


def clip(name, text):
    """Cached TTS clip with leading and trailing silence cut. Returns (path, seconds, made_a_call)."""
    h = hashlib.sha1(f"{MODEL}|{VOICE}|{INSTR}|{text}".encode()).hexdigest()[:12]
    raw = VO / f"{name}-{h}.wav"
    call = not raw.exists()
    if call:
        tts(text, raw)
    trim = VO / f"trim-{raw.name}"
    if not trim.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                        str(trim)], check=True)
    return trim, dur(trim), call


def srt_time(s):
    s = max(0.0, s)
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def cues(start, d, text):
    """One caption per sentence, timed by its share of the line's characters."""
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", text) if p.strip()]
    total = sum(len(p) for p in parts)
    t, out = start, []
    for p in parts:
        span = d * len(p) / total
        out.append((t, t + span, p.replace("Falkor DB", "FalkorDB").replace("checkout API", "checkout-api")))
        t += span
    return out


def main():
    VO.mkdir(exist_ok=True)
    text = lines()
    calls, durations = 0, {}
    clips = {}
    for name, t in text.items():
        f, d, c = clip(name, t)
        calls += c
        clips[name] = (f, d)
        durations[name] = round(d, 3)
    (VO / "durations.json").write_text(json.dumps(durations, indent=1))
    print("clips: " + ", ".join(f"{k} {v:.1f}s" for k, v in durations.items()) + f"  (sum {sum(durations.values()):.1f}s)")
    print(f"new TTS calls this run: {calls}")
    if "--clips" in sys.argv:
        return

    beats = json.loads((DEMO / "beats.json").read_text())
    total = dur(RAW)
    starts = {b["name"]: b["t"] for b in beats}
    missing = [n for n in text if n not in starts]
    if missing:
        sys.exit(f"beats.json has no beat for: {', '.join(missing)}")
    order = sorted(text, key=lambda n: starts[n])
    placed, srt, bad = [], [], []
    for i, name in enumerate(order):
        f, d = clips[name]
        start = starts[name] + LEAD
        end = starts[order[i + 1]] if i + 1 < len(order) else total
        if start + d > end + 0.05:
            bad.append(f"{name}: clip {d:.2f}s from {start:.2f}s overruns the next beat at {end:.2f}s")
        placed.append((start, f))
        srt += cues(start, d, text[name])
        print(f"{name:10s} beat {starts[name]:6.2f}s  voice {start:6.2f} to {start + d:6.2f}s  next {end:6.2f}s")
    if bad:
        print("OVERRUNS:\n  " + "\n  ".join(bad)); sys.exit(2)
    SRT.write_text("\n".join(f"{k + 1}\n{srt_time(a)} --> {srt_time(b)}\n{c}\n" for k, (a, b, c) in enumerate(srt)))

    # Silent video with captions burned in (the page is 1920x996, the strip under it carries the captions).
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(RAW), "-vf",
                    f"pad=1920:1080:0:0:color=0xFAFAF9,subtitles={SRT}:fontsdir={FONTS}:force_style='{STYLE}'",
                    "-c:v", "libx264", "-preset", "slow", "-crf", os.environ.get("CRF", "24"), "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", "-an", str(SILENT)], check=True)
    inputs, filt = [], []
    for k, (start, f) in enumerate(placed):
        inputs += ["-i", str(f)]
        ms = int(round(start * 1000))
        filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,adelay={ms}|{ms},apad[a{k}]")
    n = len(placed)
    filt.append("".join(f"[a{k}]" for k in range(n)) + f"amix=inputs={n}:normalize=0:duration=longest,atrim=0:{total:.3f},"
                "aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[vo]")
    mix = VO / "narration.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[vo]", "-ar", "48000", str(mix)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(SILENT), "-i", str(mix), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT)], check=True)
    for p in (SILENT, OUT):
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1e6:.1f} MB, {dur(p):.1f} s)")


if __name__ == "__main__":
    main()
