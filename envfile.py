"""The git-ignored .env, loaded the moment anything imports this: every module that reads a WEATHERBOY_* setting
when it's imported imports this first, so a setting in .env counts whichever module the program loads first."""
import os
from pathlib import Path


def load_env(path=Path(__file__).with_name(".env")):
    """KEY=value lines from .env into os.environ; real environment variables win."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env()
