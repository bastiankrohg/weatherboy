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


class Stamped:
    """stdout with the time in front of every line, for a service writing to a log file: so a printer that
    stopped answering can be lined up with what else happened. A terminal gets the plain lines."""
    def __init__(self, out):
        self.out, self.fresh = out, True

    def write(self, text):
        from datetime import datetime
        for piece in text.splitlines(keepends=True):
            if self.fresh and piece.strip():
                self.out.write(f"{datetime.now():%d.%m %H:%M:%S} ")
            self.out.write(piece)
            self.fresh = piece.endswith("\n")
        return len(text)

    def __getattr__(self, name):
        return getattr(self.out, name)


def stamp_log():
    import sys
    if not sys.stdout.isatty():
        sys.stdout = Stamped(sys.stdout)
