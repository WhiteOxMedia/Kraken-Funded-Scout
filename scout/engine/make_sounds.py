"""Generate alert sounds for the console: ding (good) + alarm (get out).
Plain sine synthesis, 22050 Hz mono 16-bit WAV — small enough to embed."""
import math
import struct
import wave
from pathlib import Path

SR = 22050
OUT = Path(__file__).resolve().parent / "assets"
OUT.mkdir(exist_ok=True)


def tone(freq: float, dur: float, vol: float = 0.4, decay: bool = True) -> list[float]:
    n = int(SR * dur)
    out = []
    for i in range(n):
        t = i / SR
        env = (1.0 - i / n) if decay else 1.0
        atk = min(1.0, i / (0.004 * SR))  # 4ms attack — no click
        out.append(vol * env * atk * math.sin(2 * math.pi * freq * t))
    return out


def silence(dur: float) -> list[float]:
    return [0.0] * int(SR * dur)


def write_wav(path: Path, samples: list[float]):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


# ding: two-note rising chime (good news)
ding = tone(880, 0.22, 0.38) + tone(1318.5, 0.45, 0.34)
write_wav(OUT / "alert_ding.wav", ding)

# alarm: three urgent beeps (GET OUT NOW)
alarm = (tone(660, 0.14, 0.5, decay=False) + silence(0.07) +
         tone(660, 0.14, 0.5, decay=False) + silence(0.07) +
         tone(660, 0.30, 0.5, decay=False))
write_wav(OUT / "alert_alarm.wav", alarm)

# soft tick: tiny blip for the every-minute green light
tick = tone(1046.5, 0.12, 0.16)
write_wav(OUT / "alert_tick.wav", tick)

for f in sorted(OUT.glob("*.wav")):
    print(f.name, f.stat().st_size, "bytes")
