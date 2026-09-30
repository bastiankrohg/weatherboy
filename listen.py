"""Handset → text. `python listen.py [mic]` shows live input level, for tuning --threshold."""
import collections
import sys

import numpy as np
import sounddevice as sd

RATE, BLOCK = 16000, 1600  # whisper wants 16 kHz mono; 100 ms blocks
# ponytail: asks PortAudio for 16 kHz directly. MME/CoreAudio/ALSA "default" resample for you;
# if a raw ALSA hw device refuses, open at its native rate and np.interp down.


def device(mic):
    return int(mic) if mic and mic.isdigit() else mic  # index, or a name substring like "USB"


def record(mic=None, threshold=0.02, silence=1.2, max_s=30, wait=None, abort=None):
    """Block until RMS > threshold, then return audio until `silence` seconds of quiet.
    Returns None if nobody starts talking within `wait` seconds, or as soon as abort() is true (hung up)."""
    pre = collections.deque(maxlen=5)  # 0.5 s pre-roll so the first syllable survives
    buf, quiet, waited = [], 0.0, 0.0
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=device(mic), blocksize=BLOCK) as s:
        while True:
            x = s.read(BLOCK)[0][:, 0]
            if abort and abort():
                return None
            loud = np.sqrt(np.mean(x * x)) > threshold
            if not buf:
                waited += BLOCK / RATE
                if wait and waited > wait:
                    return None
                pre.append(x)
                if loud:
                    buf = list(pre)
                continue
            buf.append(x)
            quiet = 0.0 if loud else quiet + BLOCK / RATE
            if quiet >= silence or len(buf) * BLOCK / RATE >= max_s:
                return np.concatenate(buf)


NORDIC = ("no", "nn", "da", "sv")


def load(model):
    from faster_whisper import WhisperModel
    if model.startswith("NbAiLab/"):  # National Library of Norway; CTranslate2 weights live in ct2/
        from huggingface_hub import snapshot_download
        model = snapshot_download(model, allow_patterns=["ct2/*"]) + "/ct2"
    return WhisperModel(model, device="cpu", compute_type="int8")


def transcriber(no_model="NbAiLab/nb-whisper-small", en_model="small", lang="auto"):
    """Returns stt(audio) -> (text, "no" | "en").

    Norwegian goes to NB-Whisper, which is fine-tuned on Norwegian (but writes English speech as Norwegian text).
    English goes to stock Whisper. With lang="auto", stock tiny picks between the two per utterance (~0.3 s):
    only Norwegian vs English is considered, and anything it isn't sure of counts as Norwegian, because lone
    Norwegian words otherwise get detected as Chinese or Polish.
    """
    no_m = load(no_model) if lang in ("auto", "no") else None
    en_m = load(en_model) if lang in ("auto", "en") else None
    detector = load("tiny") if lang == "auto" else None

    def stt(audio):
        lg = lang
        if lg == "auto":
            p = dict(detector.detect_language(audio, vad_filter=True)[2])
            lg = "en" if p.get("en", 0) > max(0.5, sum(p.get(k, 0) for k in NORDIC)) else "no"
        segs = (no_m if lg == "no" else en_m).transcribe(audio, language=lg, vad_filter=True)[0]
        return " ".join(s.text.strip() for s in segs), lg
    return stt


if __name__ == "__main__":
    mic = sys.argv[1] if len(sys.argv) > 1 else None
    print(sd.query_devices(device(mic), "input")["name"], "- ctrl+c to stop")
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=device(mic), blocksize=BLOCK) as s:
        while True:
            rms = float(np.sqrt(np.mean(s.read(BLOCK)[0] ** 2)))
            print(f"\r{rms:.4f} {'#' * int(rms * 500):<60}", end="", flush=True)
