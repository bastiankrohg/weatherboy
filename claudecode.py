"""Questions answered by Claude Code on the flat's own Claude subscription (Pro/Max), instead of the paid API:
`claude -p`, logged in once with `claude` on the machine that runs the server. It uses the plan's usage limits,
shared with whatever else you use Claude for, and costs nothing extra.

    python claudecode.py hvem vant tour de france i 2025

Locked down: only web search and page reading (no shell, no files), none of the machine's own Claude Code
settings, hooks, plugins or MCP servers, and ANTHROPIC_API_KEY taken out of its environment so it can never
bill the API by accident. Our own tools (weather, departures, recipes...) aren't there: the rule router in
nlp.py handles those before a question gets this far.
"""
import envfile  # noqa: F401 - .env first: the settings below are read as this loads
import json
import os
import shutil
import subprocess
from pathlib import Path

CLAUDE = os.environ.get("WEATHERBOY_CLAUDE", "claude")
MODEL = os.environ.get("WEATHERBOY_CLAUDE_MODEL", "sonnet")  # "haiku" is quicker and lighter on the limits
TOOLS = "WebSearch,WebFetch"
HOME = Path(__file__).with_name("data") / "claude-code"  # its sessions live here, away from any project
session = None  # the conversation to continue: follow-ups for as long as agent.py keeps its history


def chat(system, question, new=True, timeout=180):
    """-> the answer. new: start a conversation rather than continue the last one."""
    global session
    exe = shutil.which(CLAUDE)
    if not exe:
        raise RuntimeError("Claude Code er ikke installert her: installer det og logg inn med `claude` én gang.")
    HOME.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-p", question, "--output-format", "json", "--model", MODEL, "--system-prompt", system,
           "--tools", TOOLS, "--allowedTools", TOOLS, "--strict-mcp-config", "--setting-sources", "local"]
    if session and not new:
        cmd += ["--resume", session]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        p = subprocess.run(cmd, cwd=HOME, env=env, capture_output=True, text=True, encoding="utf-8",
                           timeout=max(5, timeout))
    except subprocess.TimeoutExpired:
        raise TimeoutError("Claude Code svarte ikke i tide.") from None
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"Claude Code: {(p.stderr or p.stdout).strip()[:300]}") from None
    if out.get("is_error"):  # not logged in, usage limit reached...: say what it said
        raise RuntimeError(f"Claude Code: {out.get('result') or out.get('subtype')}")
    session = out.get("session_id")
    return (out.get("result") or "").strip()


if __name__ == "__main__":
    import sys
    print(chat("Answer briefly, in the user's language.", " ".join(sys.argv[1:]) or "Hei! Hvem er du?"))
