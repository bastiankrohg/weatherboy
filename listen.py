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


NOISE_BLOCKS = 5  # 0.5 s of the line's own hiss, measured before anyone may start talking
last_noise = 0.0  # the background level the last recording measured (RMS), for reporting


def record(mic=None, threshold=0.02, silence=1.2, max_s=30, wait=None, abort=None, level=None):
    """Block until someone talks, then return audio until `silence` seconds of quiet.
    "Talking" is louder than `threshold` and than three times the line's own background hiss, measured over the
    first half second: a handset that hisses above `threshold` would otherwise never seem to go quiet.
    abort() true (the handset hung up) ends it: what was said so far is returned, as hanging up just means
    "that's all", and None only if nobody had started talking. None too if nobody starts within `wait` seconds.
    level: called with each block's RMS, for a meter (the orb)."""
    global last_noise
    pre = collections.deque(maxlen=5)  # 0.5 s pre-roll so the first syllable survives
    hiss = []
    buf, quiet, waited = [], 0.0, 0.0
    recent = collections.deque(maxlen=20)  # the last 2 s of levels, for the log when it stops
    db = lambda r: 20 * np.log10(max(r, 1e-6))

    def stopped(why):  # one line in the log for every recording, so a handset that misbehaves shows how
        lv = sorted(recent)
        print(f"listen: {why} after {waited + len(buf) * BLOCK / RATE:.1f} s; "
              + (f"speech for {len(buf) * BLOCK / RATE:.1f} s, " if buf else "speech never started, ")
              + (f"last 2 s: typical {db(lv[len(lv) // 2]):.0f} dB, loudest {db(lv[-1]):.0f} dB, "
                 f"speech above {db(threshold):.0f} dB" if lv else "no sound read"), flush=True)
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=device(mic), blocksize=BLOCK) as s:
        while True:
            x = s.read(BLOCK)[0][:, 0]
            if abort and abort():
                stopped("hung up")
                return np.concatenate(buf) if buf else None
            rms = float(np.sqrt(np.mean(x * x)))
            recent.append(rms)
            if level:
                level(rms)
            if len(hiss) < NOISE_BLOCKS:  # still learning the background: nobody counts as talking yet
                hiss.append(rms)
                pre.append(x)
                waited += BLOCK / RATE
                if len(hiss) == NOISE_BLOCKS:
                    last_noise = float(np.median(hiss))
                    threshold = max(threshold, 3 * last_noise)
                    print(f"listen: background {db(last_noise):.0f} dB, speech above {db(threshold):.0f} dB", flush=True)
                continue
            loud = rms > threshold
            if not buf:
                waited += BLOCK / RATE
                if wait and waited > wait:
                    stopped("nobody spoke")
                    return None
                pre.append(x)
                if loud:
                    buf = list(pre)
                    print(f"listen: speech started ({db(rms):.0f} dB)", flush=True)
                continue
            buf.append(x)
            quiet = 0.0 if loud else quiet + BLOCK / RATE
            if quiet >= silence or len(buf) * BLOCK / RATE >= max_s:
                stopped("a pause" if quiet >= silence else f"the {max_s} s cap")
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
