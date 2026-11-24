#!/bin/bash
# Deploys processing-meeting-recording to ~/opt/processing-meeting-recording/ (idempotent; safe to re-run):
#   1. the committed code (git HEAD), never touching state/ (the work folders), logs/, .venv/, skills/
#   2. the skill: skills/<name>/ copied read-only to ~/opt/processing-meeting-recording/skills/, linked from
#      ~/.claude/skills/ and ~/.agents/skills/ (a real folder at the link path is replaced only if identical)
#   3. the command ~/.local/bin/meeting-recording -> bin/meeting-recording
#   4. the .venv (uv sync --locked: exactly uv.lock's versions on .python-version's Python; skill runtime-environments),
#      then the tests, in the deployed copy
#   5. migration: the work folders of the jobs repo's interview-recording (19 Nov 2026) move to state/
# Usage: bash install.sh      Undo: bash uninstall.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HOME/opt/processing-meeting-recording"
UV="$HOME/.local/bin/uv"   # skill runtime-environments

# -- 1. Code ------------------------------------------------------------------------------------------------------
[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ] || echo "WARNING: uncommitted changes are NOT deployed"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git -C "$SRC" archive HEAD | tar -x -C "$TMP"
mkdir -p "$DST/state" "$DST/logs"
rsync -a --delete --exclude-from=- "$TMP/" "$DST/" <<KEEP
/.venv/
/state/
/logs/
/skills/
/DEPLOYED
KEEP

# -- 2. Skills ----------------------------------------------------------------------------------------------------
OPT_SKILLS="$DST/skills"
mkdir -p "$OPT_SKILLS"
for dst in "$OPT_SKILLS"/*/; do      # a skill gone from the repo: its links and copy go too
    [ -d "$dst" ] || continue
    dst="${dst%/}"; name=$(basename "$dst")
    [ -d "$SRC/skills/$name" ] && continue
    for link in "$HOME/.claude/skills/$name" "$HOME/.agents/skills/$name"; do
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$dst" ]; then rm "$link"; fi
    done
    chmod -R u+w "$dst" && rm -r "$dst" && echo "removed old skill $name"
done
for src in "$SRC"/skills/*/; do
    src="${src%/}"; name=$(basename "$src"); dst="$OPT_SKILLS/$name"
    if [ -d "$dst" ]; then chmod -R u+w "$dst" && rm -r "$dst"; fi
    cp -R "$src" "$dst" && chmod -R a-w "$dst"
    for home in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
        link="$home/$name"
        if [ -d "$link" ] && [ ! -L "$link" ]; then
            if ! diff -rq -x '*.bak' "$link" "$src" &>/dev/null; then
                echo "WARN: $link is a real folder that differs from $src - kept, not linked"
                continue
            fi
            rm -r "$link"
        fi
        mkdir -p "$home"
        ln -sfn "$dst" "$link"
    done
done

# -- 3. Command ---------------------------------------------------------------------------------------------------
mkdir -p "$HOME/.local/bin"
ln -sfn "$DST/bin/meeting-recording" "$HOME/.local/bin/meeting-recording"

# -- 4. .venv and tests -------------------------------------------------------------------------------------------
LOG="$DST/logs/install-$(date "+%Y%m%dT%H%M%S").log"
"$UV" sync --locked --no-dev --project "$DST" >>"$LOG" 2>&1 || { echo ".venv FAILED (uv sync --locked): see $LOG"; exit 1; }
echo "running tests (log: $LOG)"
(cd "$DST" && MEETING_RECORDING_STATE="$TMP/state" "$DST/.venv/bin/python" -m unittest discover -s tests -t . >>"$LOG" 2>&1) \
    || { echo "TESTS FAILED: see $LOG"; exit 1; }

# -- 5. Migration: the jobs repo's interview-recording work folders (19 Nov 2026) ----------------------------------
OLD="$HOME/opt/jobs/state/recordings"
if [ -d "$OLD" ]; then
    for d in "$OLD"/*/; do
        [ -d "$d" ] || continue
        name=$(basename "$d")
        if [ -e "$DST/state/$name" ]; then echo "WARN: $DST/state/$name exists: $d left where it is"; continue; fi
        mv "$d" "$DST/state/$name" && echo "moved $name to $DST/state/"
    done
    rmdir "$OLD" 2>/dev/null || true
fi

echo "$(git -C "$SRC" rev-parse --short HEAD) $(date -Iseconds)" > "$DST/DEPLOYED"
echo "deployed $(cat "$DST/DEPLOYED") to $DST; command: meeting-recording"
