"""Wishes for new buttons as GitHub issues, so the admin hears about them where they already look. Closing one as
completed approves the button, closing it as not planned turns it down; the page's own buttons close it too.

Needs WEATHERBOY_GITHUB_TOKEN in .env: a fine-grained token with read and write access to Issues on just this
repo (WEATHERBOY_GITHUB_REPO, default below). Without it there are no issues, and approval is on the page only.
NB the repo is public: an issue shows the wish and its prompt to anyone."""
import os

import requests

API = "https://api.github.com/repos/"


def enabled():
    return bool(os.environ.get("WEATHERBOY_GITHUB_TOKEN"))


def _gh(method, path="", **kw):
    repo = os.environ.get("WEATHERBOY_GITHUB_REPO", "bastiankrohg/weatherboy")
    r = requests.request(method, API + repo + "/issues" + path, timeout=10, **kw, headers={
        "Authorization": f"Bearer {os.environ['WEATHERBOY_GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    r.raise_for_status()
    return r.json()


def open_issue(preset, wish):
    """-> (number, url) of a new issue for a wished-for button."""
    body = (f"Ønsket på nettsiden: «{wish}»\n\nKnapp: **{preset['name']}** (ikon: {preset['icon']})\n\n"
            f"Prompt Claude får hver gang den trykkes:\n\n```\n{preset['prompt']}\n```\n\n"
            "**Lukk som _completed_ for å godkjenne, _not planned_ for å avvise.** "
            "Eller godkjenn på nettsiden som admin.")
    issue = _gh("POST", json={"title": f"Ønsket knapp: {preset['name']}", "body": body})
    return issue["number"], issue["html_url"]


def verdict(number):
    """-> "approved" or "rejected" once the admin has closed the issue, else None."""
    issue = _gh("GET", f"/{number}")
    if issue["state"] != "closed":
        return None
    return "approved" if issue.get("state_reason") == "completed" else "rejected"


def close(number, approved):
    """The page decided: say so on the issue and close it the matching way."""
    _gh("POST", f"/{number}/comments", json={"body": "Godkjent på nettsiden." if approved else "Avvist på nettsiden."})
    _gh("PATCH", f"/{number}", json={"state": "closed", "state_reason": "completed" if approved else "not_planned"})
