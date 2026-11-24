# processing-meeting-recording — agent guide

What the user wants after each meeting (user, 19 Nov 2026): a highly compressed video and a clean final transcript with the speakers named, in the meeting's folder. One command, `meeting-recording` (`recording.py`); one skill, `skills/processing-meeting-recording/SKILL.md` (how an agent uses it, and where each meeting's files go). Development home: Mac (U19, the user, 2026-11-22: the GPU). Moved here from the repo jobs (`interviewing/recording.py`, skill `interview-recording`) on 19 Nov 2026, when the user made it global.

| Path | What |
|---|---|
| `recording.py` | `analyse` (start, end, crop → `analysis.json`, `preview.jpg`) and `make` (each missing output, in order) |
| `bin/meeting-recording` | the command: `recording.py` with the deployed `.venv`; linked from `~/.local/bin` |
| `skills/processing-meeting-recording/` | the skill, deployed read-only and linked for every agent |
| `tests/` | the pure parts on made-up numbers: no ffmpeg, Whisper or Gemini |
| `~/opt/processing-meeting-recording/state/<recording>/` | the work folders (never in git, never touched by a deploy) |

Rules:

- The user's recording is read, never changed; deleted only by the user, or with their yes after they validated the outputs.
- `make` never overwrites a file in the meeting's folder; a big file leaves the work folder once its copy in the folder has the same size.
- Every constant says the measure behind it (the first recording, 19 Nov 2026); a new one too.
- Gemini only through skill `gemini` (my-setup, `~/.claude/skills/gemini/scripts/gemini.py`); Whisper through `uvx --from mlx-whisper==0.4.3`; ffmpeg from Homebrew.
- Test: `python3 -m unittest discover -s tests -t .` (or the deployed `.venv`). Deploy: commit, then `bash install.sh` (code from git HEAD, the skill, the command, the `.venv`, the tests). Undo: `bash uninstall.sh`.

## Vocabulary

| Word | Means | Never say |
|---|---|---|
| **recording** | the user's own file of the meeting (QuickTime screen recording `.mov`, or audio); the input | source video |
| **meeting's folder** | where the outputs go: an interview's stage folder in the Drive's `Interviewing/`, else where skill `data-organization` puts the subject | destination, output dir |
| **crop** | the part of the screen holding the call's video tiles (the faces) | ROI |
| **transcript** | `<date>_<name> - transcript.md`: the final one, clean verbatim, speakers named, merged from the two raw transcripts (`transcription-whisper.md`, `transcription-gemini.md`) | corrected transcript |
| **work folder** | `~/opt/processing-meeting-recording/state/<recording name>/` | scratch, tmp |
| **preview** | `preview.jpg` in the work folder: the frames around the start and end and the crop, for the agent to check | |
