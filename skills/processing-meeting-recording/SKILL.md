---
name: processing-meeting-recording
description: Process the recording of any meeting the user had - a job interview, a recruiter call, a doctor, a lawyer, any video call (QuickTime screen recording of Teams, Zoom or Meet) or audio (a phone call, a voice memo) - into a highly compressed video (H.264 under 300 MB, cropped to the faces), an MP3 and a clean final transcript with the speakers named (Whisper on the Mac + Gemini, merged), put in the meeting's folder (an interview: its stage folder in the Drive's Interviewing/). Use when the user asks to process, post-process, compute, cut, crop, compress or transcribe a meeting's or a call's recording, or after a meeting or an interview to "do everything".
---

# Processing a meeting's recording

What the user wants after each meeting (user, 19 Nov 2026): a highly compressed video, and a clear, clean final transcript with the speakers named, in the right folder. One command does the work, `meeting-recording` (repo `~/dev/agents/processing-meeting-recording`); the agent finds the files, the people and the folder, checks one picture, and reports.

```
~/Desktop/Screen Recording <date> at <time>.mov   (or a .mp4, .m4a, .mp3)
   │ 1 find it; the Calendar event at that time: who, what      2 the meeting's folder
   │ 3 analyse (30 s) ─► preview.jpg ── the agent looks; wrong? --start / --end / --crop
   │ 4 make (≈25 min for an hour, background)
   │     cut + crop ─► MP4 ≤ 300 MB ─► MP3 320 kbps ─► Whisper (Mac) ─► Gemini (speakers) ─► merged
   ▼ 5 check, report; the recording stays until the user has validated
<folder>/<YYYY_MM_DD>_<name> - recording.mp4 · recording.mp3 · transcription-whisper.md · transcription-gemini.md · transcript.md
```

| Word | Means |
|---|---|
| **recording** | the user's own file of the meeting: a QuickTime screen recording (`~/Desktop/Screen Recording <YYYY-MM-DD> at <HH.MM.SS>.mov`, ~8 GB an hour) or an audio file; never changed; deleted only by the user, or with their yes once they have validated the outputs |
| **crop** | the part of the screen holding the call's video tiles (the faces) |
| **transcript** | `… - transcript.md`: the final one, clean verbatim (fillers and stutters out, every sentence kept), speakers named; made from the two raw transcripts, `transcription-whisper.md` (Whisper on the Mac, no speakers) and `transcription-gemini.md` (Gemini, speakers) |
| **work folder** | `~/opt/processing-meeting-recording/state/<recording name>/`: analysis.json, preview.jpg, make.log, whisper.json/.log; the big files only until they are in the meeting's folder |

## 1. The recording, and who was there

- The recording: the newest `~/Desktop/Screen Recording *.mov` (`ls -t ~/Desktop/Screen\ Recording*.mov | head`), unless the user names a file. Its date and start time are in its name.
- The Calendar event at that time (skill `google-workspace`): its title, its attendees, its description. From it: `--speakers` (the user first: `Jean Lescut-Müller; Firstname Lastname (role, company)`), `--words` (the company, its products, places, the topics' jargon), and the name of the files. No event: ask the user who it was with.
- The name of the files, after the date, short: `Interview 2 with Anna`, `HR call`, `Call with Marco`, `Doctor - test results`.

## 2. The meeting's folder

| Meeting | Folder |
|---|---|
| a job interview or recruiter call | its stage folder: `~/gdrive/Unemployment_and_Job_Searches/Interviewing/<YYYY_MM_DD>_<Company>/<YYYY_MM_DD>_<n> <stage> (<person>)/`, the one dated that day; missing: `~/dev/professional/jobs/skills/interview-prep-hr-call/scripts/interviewing_folder.py "<application folder>" "<stage folder>"` (repo jobs, its AGENTS.md) |
| anything else (doctor, lawyer, administration, a project) | where skill `data-organization` puts it (the Drive's Project / Section / subsection of the subject) |
| unsure | ask the user |

## 3. Analyse, then look at the preview

```bash
meeting-recording analyse "<recording>"
```

It prints the start, the end and (for a video) the crop, `W:H:X:Y` in screen pixels, and the path of `preview.jpg`. Read the image:

| Row of the preview | Right when |
|---|---|
| start −5 s / +5 s | before: a lobby ("someone will let you in") or no call yet; after: the other person on screen |
| end −5 s / +5 s | before: still in the call; after: the call is over (Teams' "Meetings are just one tool…", the desktop) |
| red box, crop × 3 | it holds every face and nothing else: no document, no browser |

How they are found: the **start and end** are the first and last stretch of speech (10 s with 7 s louder than halfway between the quiet and the median second), ±2 s; the **crop** is the part of the screen that changes from one half-second to the next in 30 moments of the meeting (only the video tiles move), each side moved, pixel by pixel, to the tile's own edge. Wrong: `analyse` again with `--start 250`, `--end 3200` or `--crop 1592:1484:4:848`. Expect it when there was talking or music before the call, a shared screen, or another window playing a video. An audio file: start and end only.

## 4. Make (background)

```bash
meeting-recording make "<recording>" "<folder>" "<name>" \
    --speakers "Jean Lescut-Müller; Anna Meier (Head of Robotics, Example AG)" \
    --words "Example AG, Sigyx, Jetson, SLAM" \
    > ~/opt/processing-meeting-recording/state/<recording name>/run.log 2>&1
```

Always in the background (Bash `run_in_background`), never streaming its output. It does, in order, each output missing from the folder (a re-run after a failure does only the rest; it never overwrites); it stops before a step when the Mac lacks the memory (`resource-status`).

| Output | How | First recording (19 Nov 2026, 49 min) |
|---|---|---|
| `… - recording.mp4` (video only) | the crop, 30 fps, H.264 2-pass, its bitrate from the length so the file stays 3 % under `--max-mb` (300); AAC 128 kbps | 290 MB, 16 min |
| `… - recording.mp3` | the same cut, 320 kbps | 119 MB, 20 s |
| `… - transcription-whisper.md` | Whisper large-v3-turbo on the Mac's GPU, the language detected (or `--language fr`), the speakers' names and `--words` as hints; no speakers | 3 min |
| `… - transcription-gemini.md` | skill `gemini` (free key): a 16 kHz mono copy of the MP3 to Gemini 3.8 Flash, speakers named | 4.4 min |
| `… - transcript.md` | Gemini merges the two: Gemini's turns and speakers, the likelier words where they differ, names spelled right, fillers out, `[?]` where unsure | |

Gemini is fine for every meeting, a doctor's or a lawyer's too (user, 19 Nov 2026). Gemini busy or out of quota: the run stops with Gemini's message; run `make` again later.

## 5. Check and report

- `tail` the run log: each output ✓ or ✗; the MP4 ≤ 300 MB.
- `transcript.md`: read its first and last 15 lines (speakers right? names? the end of the meeting there?).
- Tell the user, in a table: each file, its size and length; the folder's full path in backticks. Then ask whether the recording can go to the Trash: never before they say yes.
