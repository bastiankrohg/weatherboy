"""The recipe collection: markdown files in their own git repo, which the agent can list, read and add drafts to."""
import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("WEATHERBOY_RECIPES", Path(__file__).parent / "recipes")).resolve()
DRAFTS = "drafts"
last = None  # the recipe file the agent read or drafted during the current question; "skriv ut" prints it


def _name(p):
    return p.relative_to(ROOT).with_suffix("").as_posix()


def list_recipes() -> str:
    """List every recipe in the user's own collection as name | title | tags | draft. Always check here first."""
    # ponytail: returns the whole index and lets Claude match "pannekaker" to "Pancakes". Fine for hundreds
    # of recipes; add a search when it's thousands.
    rows = []
    for p in sorted(ROOT.rglob("*.md")):
        if p.name.lower() == "readme.md" or p.name.startswith("_"):  # _mal.md is the template
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        title = next((l[2:].strip() for l in lines if l.startswith("# ")), p.stem)
        tags = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("tags:")), "")
        rows.append(f"{_name(p)} | {title} | {tags}" + (" | draft" if p.parent.name == DRAFTS else ""))
    return "\n".join(rows) or "The collection is empty."


def _path(name):
    p = (ROOT / f"{name}.md").resolve()
    if not p.is_relative_to(ROOT) or not p.is_file():  # names come from the model or the page: stay inside
        raise ValueError(f"No recipe called {name!r}. Use a name from list_recipes.")
    return p


def read_recipe(name: str) -> str:
    """Full text of one recipe from the user's collection.

    Args:
        name: The name exactly as list_recipes shows it, e.g. "pannekaker" or "drafts/lasagne".
    """
    global last
    last = _path(name)
    return last.read_text(encoding="utf-8")


def update(name, markdown):
    """Save an edited recipe over its file (from the page's text editor; git has the history)."""
    if len(markdown) > 20_000:
        raise ValueError("That's too long for a recipe.")
    _path(name).write_text(markdown.strip() + "\n", encoding="utf-8")


def save_draft(title: str, markdown: str, source_url: str) -> str:
    """Save a recipe found online as a draft in the user's collection, for them to try and edit later.
    Only for dishes that aren't in the collection already.

    Args:
        title: Recipe title in the user's language.
        markdown: The whole recipe in the house format, starting with "# <title>".
        source_url: The page the recipe came from.
    """
    global last
    text = markdown.strip()
    if not re.search(r"^kilde:", text, re.M | re.I):  # the source always goes in the front matter
        text = (text.replace("---\n", f"---\nkilde: {source_url}\n", 1) if text.startswith("---")
                else f"---\nkilde: {source_url}\n---\n\n{text}")
    last = _write(title, text, ROOT / DRAFTS)
    return f"Saved as {_name(last)}"


def add(markdown):
    """A recipe the user wrote themselves (web page): straight into the collection, named after its # title."""
    title = next((l[2:].strip() for l in markdown.splitlines() if l.startswith("# ")), "")
    if not title:
        raise ValueError('Oppskriften trenger en tittel: en linje som starter med "# ".')
    return _name(_write(title, markdown.strip(), ROOT))


def _write(title, text, folder):
    if len(text) > 20_000:
        raise ValueError("That's too long for a recipe.")
    slug = re.sub(r"[^a-z0-9æøå]+", "-", title.lower()).strip("-")[:60] or "oppskrift"
    folder.mkdir(parents=True, exist_ok=True)
    p, n = folder / f"{slug}.md", 2
    while p.exists():  # never overwrite
        p, n = folder / f"{slug}-{n}.md", n + 1
    p.write_text(text + "\n", encoding="utf-8")
    return p
