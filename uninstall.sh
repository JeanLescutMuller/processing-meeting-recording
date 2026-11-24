#!/bin/bash
# Undoes install.sh: the skill links and copies, the command link, the deployed code and .venv. Keeps state/ (the work
# folders: analysis, previews, logs) and logs/: delete ~/opt/processing-meeting-recording yourself once you have looked.
set -euo pipefail
DST="$HOME/opt/processing-meeting-recording"
for dst in "$DST"/skills/*/; do
    [ -d "$dst" ] || continue
    dst="${dst%/}"; name=$(basename "$dst")
    for link in "$HOME/.claude/skills/$name" "$HOME/.agents/skills/$name"; do
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$dst" ]; then rm "$link"; fi
    done
    chmod -R u+w "$dst" && rm -r "$dst"
done
link="$HOME/.local/bin/meeting-recording"
if [ -L "$link" ] && [ "$(readlink "$link")" = "$DST/bin/meeting-recording" ]; then rm "$link"; fi
for p in "$DST"/*; do
    case "$(basename "$p")" in state|logs) continue ;; esac
    rm -rf "$p"
done
rm -rf "$DST/.venv"
echo "removed; kept $DST/state and $DST/logs"
