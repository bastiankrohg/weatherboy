"""Free answers from a model on our own machines: Ollama on the work desktop over Tailscale, or on this machine.
Anything that speaks the OpenAI chat API with tool calls works the same (llama.cpp's server, LM Studio, vLLM,
an agent with an OpenAI-compatible endpoint), so this is plain HTTP rather than one vendor's SDK."""
import envfile  # noqa: F401 - .env loaded before the settings below are read
import json
import os
import re
import time

import requests

import progress

# tried in order, per question: the desktop may be off or asleep. Each is a base URL ending in /v1.
SERVERS = [u.strip() for u in os.environ.get("WEATHERBOY_LOCAL_URLS",
                                             "http://robotlab:11434/v1,http://localhost:11434/v1").split(",") if u.strip()]
MODEL = os.environ.get("WEATHERBOY_LOCAL_MODEL", "qwen3:8b")  # needs tool calling; best of robotlab's in tests
# for a server behind gate.py (published through a tunnel): sent as a bearer token, the OpenAI way
KEY = os.environ.get("WEATHERBOY_LOCAL_KEY", "")


def headers():
    return {"Authorization": f"Bearer {KEY}"} if KEY else {}
LOCAL = ("\nYou run on a small local model, so don't trust your memory for facts, news, results or anything "
         "that changes: call web_search first (and web_fetch to read a result), then answer from what you found. "
         "Never write that you will search; just call the tool. Keep tool calls few. Write correct, natural "
         "language, in the language you were told to reply in, and nothing else; plain words over clever ones.")


def server():
    """The first server that answers, or None."""
    for url in SERVERS:
        try:
            requests.get(url.rstrip("/") + "/models", headers=headers(), timeout=5).raise_for_status()
            return url
        except requests.RequestException:
            pass
    return None


def _turn(url, body, deadline):
    """One model turn, streamed: the answer arrives in pieces as it's written. A tunnel gives up on a request
    that stays silent for 100 s (Cloudflare's limit), and a long answer like a recipe takes longer than that
    to write; a stream keeps talking. -> the whole message, tool calls and all, as the non-streamed API gives it."""
    content, calls = [], {}
    gap = 180 if not deadline else max(1.0, min(180, deadline - time.monotonic()))  # the longest wait for a piece
    try:
        with requests.post(url.rstrip("/") + "/chat/completions", headers=headers(), json=body | {"stream": True},
                           stream=True, timeout=(10, gap)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if deadline and time.monotonic() > deadline:
                    raise TimeoutError(progress.TOO_SLOW)
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
                content.append(delta.get("content") or "")
                for tc in delta.get("tool_calls") or []:  # tool calls may come in pieces too, by index
                    c = calls.setdefault(tc.get("index", len(calls)),
                                         {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    c["id"] = tc.get("id") or c["id"]
                    f = tc.get("function") or {}
                    c["function"]["name"] += f.get("name") or ""
                    args = f.get("arguments")
                    c["function"]["arguments"] = (json.dumps(args) if isinstance(args, dict)
                                                  else c["function"]["arguments"] + (args or ""))
    except requests.Timeout:
        raise TimeoutError(progress.TOO_SLOW) from None
    msg = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg


def chat(system, messages, tools, model=MODEL, deadline=None):
    """The tool loop against an OpenAI-compatible endpoint. `messages` (role/content dicts) is appended to;
    `tools` are the same function tools Claude gets (name, description, input_schema, call)."""
    url = server()
    if not url:
        raise RuntimeError("Den lokale modellen svarer ikke (prøvde " + ", ".join(SERVERS) + "). "
                           "Velg Claude i modellvelgeren, eller start Ollama.")
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                               "parameters": t.input_schema}} for t in tools]
    by_name = {t.name: t for t in tools}
    for _ in range(8):  # a few rounds of tool calls, then it has to answer
        if deadline and time.monotonic() > deadline:
            raise TimeoutError(progress.TOO_SLOW)
        msg = _turn(url, {"model": model, "tools": specs,
                          "messages": [{"role": "system", "content": system + LOCAL}] + messages}, deadline)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = msg.get("tool_calls") or []
        if not calls:  # reasoning models (qwen3 …) may wrap their thinking in <think> tags: never print that
            return re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
        for c in calls:
            try:
                args = c["function"]["arguments"]
                args = args if isinstance(args, dict) else json.loads(args or "{}")
                progress.step(progress.describe(c["function"]["name"], args))
                result = by_name[c["function"]["name"]].call(args)
            except Exception as e:  # a wrong tool name or bad arguments: tell the model, let it try again
                result = f"Error: {type(e).__name__}: {e}"
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": str(result)})
        progress.step("Venter på modellen")
    return "Beklager, jeg kom ikke i mål med det."
