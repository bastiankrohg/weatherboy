"""Handset earpiece: Piper TTS (offline, Norwegian + English) and Norwegian phone tones."""
import re
import time

import numpy as np
import sounddevice as sd

from listen import device

RATE = 22050  # Piper "medium" voices; tones use it too
VOICES = {"no": "no_NO-talesyntese-medium", "en": "en_GB-alan-medium"}
NO_WORDS = set("og er det ikke jeg du på en et til med som har vil kan blir av om hva når neste".split())
EN_WORDS = set("the and is it you to of a in that for with on this what when next are".split())
_voices = {}


def tone(on, off=0.0, reps=1, hz=425):  # 425 Hz is the Norwegian network tone
    t = np.arange(int(RATE * on)) / RATE
    return np.tile(np.concatenate([0.3 * np.sin(2 * np.pi * hz * t), np.zeros(int(RATE * off))]), reps)


DIAL = tone(1.2)              # dial tone when the handset is lifted (continuous in the network)
BLIP = tone(0.08)             # "heard you"
RINGBACK = tone(1.0, 1.5, 2)  # ringing: 1 s on (4 s off in the network, shortened here)
BUSY = tone(0.5, 0.5, 3)      # busy / hung up / something broke


def lang_of(text):
    words = re.findall(r"\w+", text.lower())
    return "no" if sum(w in NO_WORDS for w in words) >= sum(w in EN_WORDS for w in words) else "en"


def voice(lang):
    if lang not in _voices:
        from huggingface_hub import hf_hub_download
        from piper import PiperVoice
        name = VOICES[lang]
        region, speaker, quality = name.split("-")
        path = f"{region.split('_')[0]}/{region}/{speaker}/{quality}/{name}.onnx"
        hf_hub_download("rhasspy/piper-voices", path + ".json")  # lands next to the .onnx, where Piper looks
        _voices[lang] = PiperVoice.load(hf_hub_download("rhasspy/piper-voices", path))
    return _voices[lang]


def synth(text):
    return np.concatenate([c.audio_float_array for c in voice(lang_of(text)).synthesize(text)])


def play(audio, out=None, volume=1.0, abort=None, level=None):
    """volume: calibration knob for the handset earpiece (it's far more sensitive than a headphone).
    abort: checked every 50 ms; true stops playback (the handset was hung up mid-sentence).
    level: called every 50 ms with the RMS of what's playing right then, for a meter (the orb)."""
    sd.play(np.clip(audio * volume, -1, 1).astype(np.float32), RATE, device=device(out))
    start = time.monotonic()
    while sd.get_stream().active:
        if abort and abort():
            sd.stop()
            return
        if level:
            at = int((time.monotonic() - start) * RATE)
            x = audio[at:at + RATE // 20]
            level(float(np.sqrt(np.mean(x * x))) if len(x) else 0.0)
        sd.sleep(50)


def say(text, out=None, volume=1.0, abort=None, level=None):
    play(synth(text), out, volume, abort, level)


if __name__ == "__main__":
    import sys
    say(" ".join(sys.argv[1:]) or "Hei, det er Weatherboy. Hva lurer du på?")
