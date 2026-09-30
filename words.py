"""Word (or character) of the day in a chosen language: one cheap structured call a day, cached, no repeats."""
import json
import threading
from datetime import date
from pathlib import Path

FILE = Path(__file__).with_name("data") / "dagens_ord.json"
MODEL = "claude-haiku-4-5"  # one small call a day: the cheapest model is plenty
REMEMBER = 120             # recent words per language, sent along so they don't come back
_lock = threading.Lock()

# id: (label on the page and the receipt, what to ask for)
LANGS = {
    "ko": ("Koreansk (A2)", "one useful everyday Korean word for a learner at CEFR A2. word in hangeul; reading in "
           "Revised Romanization; a short natural A2 example sentence in hangeul, example_reading its romanization"),
    "fr": ("Fransk (avansert)", "one advanced French word (C1-C2), elegant, literary or precise, the kind a fluent "
           "speaker rarely uses; note: a concise definition in French; reading and example_reading empty"),
    "it": ("Italiensk (B1)", "one useful Italian word at B1 level; reading and example_reading empty"),
    "es": ("Spansk (A1-A2)", "one basic, very common Spanish word (A1-A2); reading and example_reading empty"),
    "pt": ("Portugisisk (A1-A2)", "one basic, very common Portuguese word (A1-A2); mention in note if Brazil and "
           "Portugal differ; reading and example_reading empty"),
    "zh": ("Kinesisk tegn (HSK 1-3)", "one Chinese character (simplified) at HSK 1-3 level. word: the single "
           "character; reading: pinyin with tone marks; kind: 'character'; example: a common word using it, "
           "example_reading its pinyin; note: a short memory hint from the character's parts"),
    "ja": ("Japansk kanji (N5-N4)", "one Japanese kanji at JLPT N5-N4 level. word: the single kanji; reading: "
           "'on: … · kun: …'; kind: 'kanji'; example: a common word using it, example_reading in hiragana with "
           "romaji; note: a short memory hint from the kanji's parts"),
}

SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in
                   ("word", "reading", "kind", "meaning", "example", "example_reading", "example_meaning", "note")},
    "required": ["word", "reading", "kind", "meaning", "example", "example_reading", "example_meaning", "note"],
    "additionalProperties": False,
}


def _load():
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {"today": {}, "history": {}}


def _save(data):
    FILE.parent.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def today(lang):
    """Today's entry for `lang`: from the cache, or one new call. Reprints and previews cost nothing."""
    if lang not in LANGS:
        raise ValueError(f"Unknown language {lang!r}")
    with _lock:
        data = _load()
        cached = data["today"].get(lang)
        if cached and cached["date"] == date.today().isoformat():
            return cached["entry"]
        seen = data["history"].get(lang, [])
        entry = generate(lang, seen)
        data["today"][lang] = {"date": date.today().isoformat(), "entry": entry}
        data["history"][lang] = (seen + [entry["word"]])[-REMEMBER:]
        _save(data)
        return entry


def generate(lang, seen):
    import agent  # the shared client and cost tracking
    if not (agent.client.api_key or agent.client.auth_token):
        raise RuntimeError("Mangler API-nøkkel: legg ANTHROPIC_API_KEY=... i .env ved siden av agent.py.")
    ask = (f"Word of the day for a thermal receipt on the wall: {LANGS[lang][1]}. English for meaning and "
           f"example_meaning; kind: part of speech (short); note: one useful extra for a learner (a related word, a usage or grammar tip) or empty, never a remark about the word being useful or common. Keep every field "
           f"short enough for an 80 mm receipt. Pick something different from these recent ones: "
           f"{', '.join(seen) or 'none yet'}.")
    msg = agent.client.messages.create(model=MODEL, max_tokens=800, messages=[{"role": "user", "content": ask}],
                                       output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    agent.spent += agent.cost(MODEL, msg.usage)
    return json.loads(next(b.text for b in msg.content if b.type == "text"))


def label(lang):
    return f"{'Dagens tegn' if lang in ('zh', 'ja') else 'Dagens ord'} · {LANGS[lang][0]}"
