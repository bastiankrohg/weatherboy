#!/bin/zsh
# Pull-based deploy for the old Mac (com.weatherboy.update runs this every 2 minutes): if main on GitHub has
# moved, pull it, sync the dependencies and restart the server. Nothing has to reach in - pushing to main
# is how you deploy. Log: ~/weatherboy-update.log
set -u
cd "${0:A:h}/../.." || exit 1
git fetch -q origin main 2>/dev/null || exit 0  # offline, or GitHub is down: next time
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
echo "$(date '+%F %T') updating $(git rev-parse --short HEAD) -> $(git rev-parse --short origin/main)"
if ! git merge -q --ff-only origin/main; then
  echo "  not a fast-forward (changes made on this Mac?): left as it is"
  exit 0
fi
~/.local/bin/uv sync -q --extra phone && echo "  dependencies synced"
launchctl kickstart -k gui/$(id -u)/com.weatherboy.server && echo "  server restarted"
