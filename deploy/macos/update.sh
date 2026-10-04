#!/bin/zsh
# Pull-based deploy for the old Mac (com.weatherboy.update runs this every 2 minutes): if main on GitHub has
# moved, pull it, sync the dependencies and restart the server; if a service file in deploy/macos changed,
# install it and reload that service. Nothing has to reach in - pushing to main is how you deploy.
# Log: ~/weatherboy-update.log
#
# All of it sits in a function: zsh then reads the whole script before running any of it, so a pull that
# rewrites this file can't change what the running copy does halfway through.
set -u
here="${0:A:h}"  # out here: inside a function, $0 is the function's name

update() {
  cd "$here/../.." || return 1
  local pulled=0 reload=()
  if git fetch -q origin main 2>/dev/null && [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "$(date '+%F %T') updating $(git rev-parse --short HEAD) -> $(git rev-parse --short origin/main)"
    if git merge -q --ff-only origin/main; then
      pulled=1
    else
      echo "  not a fast-forward (changes made on this Mac?): left as it is"
    fi
  fi
  for a in server tunnel; do  # a service whose file in the repo changed: installed and reloaded
    local f=com.weatherboy.$a.plist
    if ! cmp -s "deploy/macos/$f" "$HOME/Library/LaunchAgents/$f"; then
      cp "deploy/macos/$f" "$HOME/Library/LaunchAgents/$f" && reload+=($a)
    fi
  done
  [ $pulled = 0 ] && [ ${#reload} = 0 ] && return 0
  ~/.local/bin/uv sync -q --extra voice --extra phone && echo "  dependencies synced"
  for a in $reload; do
    launchctl bootout "gui/$(id -u)/com.weatherboy.$a" 2>/dev/null
    sleep 1
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.weatherboy.$a.plist" && echo "  $a: new service file loaded"
  done
  if [ $pulled = 1 ] && [[ ${reload[(Ie)server]} = 0 ]]; then
    launchctl kickstart -k "gui/$(id -u)/com.weatherboy.server" && echo "  server restarted"
  fi
}

update "$@"
exit
