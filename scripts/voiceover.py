"""Voiced demo: per-beat OpenAI TTS clips placed at the beat timestamps of demo/patientzero-demo.mp4.

Reads the narration from demo/voiceover.md (the table rows), demo/beats.json for beat starts.
Clips are cached in demo/vo/ by hash of (model, voice, instructions, text), so only changed lines cost a call.
A clip that overruns its window is sped up at most 1.08x; past that the script fails and the line must be rewritten.
Output: demo/patientzero-demo-voiced.mp4 (video stream copied, AAC 192k, loudness -16 LUFS).
Run: python3 scripts/voiceover.py   (needs OPENAI_API_KEY in the environment; never printed)
"""
import hashlib, json, os, re, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
VO = DEMO / "vo"
VIDEO = DEMO / "patientzero-demo.mp4"
OUT = DEMO / "patientzero-demo-voiced.mp4"
MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.environ.get("TTS_VOICE", "onyx")
INSTR = ("Voice: a calm, confident startup founder giving a live product demo. "
         "Pace: brisk and steady, about 150 words per minute, short natural pauses only. "
         "Tone: warm, assured, plain-spoken, never salesy or dramatic.")
LEAD, GAP, MAX_TEMPO = 0.25, 0.15, 1.08


def dur(f):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]))


def lines():
    """beat name -> (narration text, extra start offset), from the markdown table in demo/voiceover.md.
    A Start cell like "0:09.3 +1.0" delays that line 1.0 s into its beat (the previous line may run into the beat)."""
    out = {}
    for row in (DEMO / "voiceover.md").read_text().splitlines():
        m = re.match(r"\|\s*[\d:.]+\s*(?:\+([\d.]+)\s*)?\|\s*([a-z ]+?)\s*\|\s*[\d.]+ s\s*\|\s*(.+?)\s*\|\s*$", row)
        if m:
            out[m.group(2)] = (m.group(3), float(m.group(1) or 0))
    return out


def tts(text, path):
    body = json.dumps({"model": MODEL, "voice": VOICE, "input": text, "instructions": INSTR, "response_format": "wav"}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", body,
                                 {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    path.write_bytes(urllib.request.urlopen(req, timeout=120).read())


def main():
    VO.mkdir(exist_ok=True)
    beats = json.loads((DEMO / "beats.json").read_text())
    total = dur(VIDEO)
    text = lines()
    starts = [b["t"] for b in beats] + [total]
    calls, clips, report, bad = 0, [], [], []
    for i, b in enumerate(beats):
        if b["name"] not in text:
            continue
        t, _ = text[b["name"]]
        h = hashlib.sha1(f"{MODEL}|{VOICE}|{INSTR}|{t}".encode()).hexdigest()[:12]
        raw = VO / f"{b['name'].replace(' ', '-')}-{h}.wav"
        if not raw.exists():
            tts(t, raw); calls += 1
        trim = VO / f"trim-{raw.name}"   # cut leading/trailing silence
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                        str(trim)], check=True)
        start = b["t"] + (0.5 if i == 0 else LEAD) + text[b["name"]][1]
        nxt = beats[i + 1]["name"] if i + 1 < len(beats) else None
        end = starts[i + 1] + (text[nxt][1] if nxt in text else 0)
        window = end - (GAP if nxt else 0.05) - start
        d = dur(trim)
        tempo = max(1.0, d / window)
        if tempo > MAX_TEMPO:
            bad.append(f"{b['name']}: {d:.2f}s in a {window:.2f}s window (needs {tempo:.2f}x): rewrite shorter")
        clips.append((start, trim, min(tempo, MAX_TEMPO)))
        report.append(f"{b['name']:17s} at {start:6.2f}s  clip {d:5.2f}s / window {window:5.2f}s  tempo {tempo:.2f}")
    print("\n".join(report)); print(f"new TTS calls this run: {calls}")
    if bad:
        print("OVERRUNS:\n  " + "\n  ".join(bad)); sys.exit(2)

    inputs, filt = [], []
    for k, (start, f, tempo) in enumerate(clips):
        inputs += ["-i", str(f)]
        ms = int(round(start * 1000))
        filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,atempo={tempo:.3f},adelay={ms}|{ms},apad[a{k}]")
    n = len(clips)
    filt.append("".join(f"[a{k}]" for k in range(n)) + f"amix=inputs={n}:normalize=0:duration=longest,atrim=0:{total:.3f},"
                "aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[vo]")
    mix = VO / "narration.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[vo]", "-ar", "48000", str(mix)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(VIDEO), "-i", str(mix), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT)], check=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB, {dur(OUT):.1f} s)")


if __name__ == "__main__":
    main()
