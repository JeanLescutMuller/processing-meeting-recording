"""Processing a meeting's recording (user, 19 Nov 2026; skill processing-meeting-recording): from a screen recording of a
video call (Teams, Zoom, Meet), or an audio file (a phone call, a voice memo), to a compressed video, an MP3 and a clean
transcript with the speakers named, in the meeting's folder (an interview's stage folder on the Drive, or wherever the
meeting belongs).

  analyse  the meeting's start and end (the volume: the first and last stretch of speech) and, for a video, the crop (the
           video tiles: the only part of the screen that changes from one half-second to the next)
           -> <work>/analysis.json and <work>/preview.jpg, which the agent looks at before `make`
  make     every output missing from the folder, in order (a re-run does only what is missing; nothing is overwritten):
             <date>_<name> - recording.mp4              cut + crop, H.264 2-pass under --max-mb (300 MB)   (video only)
             <date>_<name> - recording.mp3              the same cut, 320 kbps
             <date>_<name> - transcription-whisper.md   Whisper large-v3-turbo (mlx-whisper, on the Mac), no speakers
             <date>_<name> - transcription-gemini.md    Gemini (skill gemini, free key): speakers named
             <date>_<name> - transcript.md              Gemini merges the two: speakers, names spelled right, fillers out

  meeting-recording analyse <recording> [--start S] [--end S] [--crop W:H:X:Y]
  meeting-recording make <recording> "<folder>" "<name>" --speakers "Jean Lescut-Müller; Name Surname (role, company)" [--words "…"]

Reads the recording (never changes or deletes it); writes its work folder ~/opt/processing-meeting-recording/state/<recording
name>/ (analysis.json, preview.jpg, the logs, whisper.json; the big files only until they are copied). Measured on the first
one (19 Nov 2026: 49 min of a 3600x2338 screen, 8.2 GB): analyse 21 s, the encode 16 min (290 MB),
the MP3 20 s (119 MB), Whisper 3 min, Gemini 4.4 min.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

WORK = Path(os.environ.get('MEETING_RECORDING_STATE') or Path.home() / 'opt/processing-meeting-recording/state')
GEMINI = Path.home() / '.claude/skills/gemini/scripts/gemini.py'     # skill gemini (my-setup)
GEMINI_MODEL = 'gemini-3.8-flash'   # the one that named the speakers right (19 Nov 2026; skill gemini's measures)
MARGIN_S = 2           # seconds kept before the first and after the last speech
SPEECH_RUN = 10        # the meeting starts (ends) at the first (last) 10 s with 7 s of speech or more
SAMPLES = 30           # half-second frame pairs looked at for the crop (first recording: both tiles clear, 15 s)
SCALE = 4              # the crop is found at 1/4 resolution
BLOCK = 8              # ... in blocks of 8x8 of those pixels (32 px of the screen)
LIVE_LINE = 0.02       # a line of pixels belongs to a tile when it changed in 2 % of the pairs (outside: 0.00)
FPS = 30
AUDIO_KBPS = 128       # the MP4's AAC
MP3_KBPS = 320
SIZE_MARGIN = 0.97     # 2-pass aimed at 292 MB gave 289.1 MB (19 Nov 2026): aim 3 % under the cap
WHISPER = ['uvx', '--from', 'mlx-whisper==0.4.3', 'mlx_whisper']
WHISPER_MODEL = 'mlx-community/whisper-large-v3-turbo'   # in ~/.cache/huggingface since Nov 2026; 1.6 GB
KINDS = ('recording.mp4', 'recording.mp3', 'transcription-whisper.md', 'transcription-gemini.md', 'transcript.md')


def run(l_cmd, **kw):
    return subprocess.run([str(x) for x in l_cmd], check=True, **kw)


def need(gb):
    """Stop before a heavy step when the Mac lacks the memory (resource-guard's resource-status; absent: go on)."""
    if shutil.which('resource-status') and subprocess.run(['resource-status', '--need-gb', str(gb)]).returncode == 1:
        raise SystemExit(f'not enough free memory for {gb} GB: try again later')


def probe(src):
    """(duration, width, height); width and height None for an audio file."""
    d = json.loads(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,width,height',
                        '-of', 'json', src], capture_output=True).stdout)
    l_types = [s['codec_type'] for s in d['streams']]
    if 'audio' not in l_types:
        raise SystemExit(f'{src}: no sound track (screen recording started without the microphone?)')
    v = d['streams'][l_types.index('video')] if 'video' in l_types else {}
    return float(d['format']['duration']), v.get('width'), v.get('height')


def loudness(src):
    """dB of each second of the recording (8 kHz mono)."""
    raw = run(['ffmpeg', '-nostdin', '-v', 'error', '-i', src, '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
              capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = len(a) // 8000
    return 20 * np.log10(np.sqrt((a[:n * 8000].reshape(n, 8000) ** 2).mean(1)) + 1e-9)


def meeting_bounds(a_db):
    """(start, end, threshold): the first and last speech second of the first and last 10 s holding 7 s of speech, +-2 s.
    Speech = louder than halfway between the quiet (5th percentile) and the median second. First recording: quiet -57 dB,
    median -24 dB, threshold -40 dB -> 247 s, 3215 s (the interviewer appeared at 248 s; Teams' "meeting ended" at 3213 s)."""
    thr = (np.percentile(a_db, 5) + np.median(a_db)) / 2
    a_speech = a_db > thr
    a_ok = np.where(np.convolve(a_speech, np.ones(SPEECH_RUN), 'valid') >= 0.7 * SPEECH_RUN)[0]
    if not len(a_ok):
        raise SystemExit('no stretch of speech found in the recording')
    a_sec = np.where(a_speech)[0]
    first = a_sec[a_sec >= a_ok[0]][0]
    last = a_sec[a_sec < a_ok[-1] + SPEECH_RUN][-1]
    return int(max(0, first - MARGIN_S)), int(min(len(a_db), last + 1 + MARGIN_S)), round(float(thr), 1)


def live_map(src, start, end, w, h):
    """Share of the frame pairs in which each pixel changed within half a second, at 1/4 resolution, from SAMPLES moments
    spread over the meeting."""
    ws, hs = w // SCALE, h // SCALE
    l_live = []
    for t in np.linspace(start + min(30, (end - start) / 4), end - min(30, (end - start) / 4), SAMPLES):
        raw = run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{t:.2f}', '-i', src, '-t', '1.2', '-vf',
                   f'fps=2,scale={ws}:{hs},format=gray', '-frames:v', '2', '-f', 'rawvideo', '-'], capture_output=True).stdout
        if len(raw) < 2 * ws * hs:
            continue
        a = np.frombuffer(raw, np.uint8)[:2 * ws * hs].reshape(2, hs, ws).astype(np.int16)
        l_live.append(np.abs(a[1] - a[0]) > 2)
    return np.mean(l_live, 0)


def find_crop(a_frac):
    """(w, h, x, y) in screen pixels around every video tile. Blocks of 8x8 that changed in 5 % of the pairs or more, in a
    group of 4 blocks or more (drops a blinking caret, a clock, the mouse); then each side of their box is moved to the
    tile's own edge: the last line of pixels that changed in 2 % of the pairs or more, on average."""
    hb, wb = a_frac.shape[0] // BLOCK, a_frac.shape[1] // BLOCK
    a_blk = a_frac[:hb * BLOCK, :wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK).mean((1, 3)) > 0.05
    # groups of touching blocks (8 neighbours), kept when 4 blocks or more
    a_seen = np.zeros_like(a_blk)
    l_keep = []
    for r0, c0 in zip(*np.where(a_blk)):
        if a_seen[r0, c0]:
            continue
        l_group, l_todo = [], [(r0, c0)]
        a_seen[r0, c0] = True
        while l_todo:
            r, c = l_todo.pop()
            l_group.append((r, c))
            for rr in range(max(0, r - 1), min(hb, r + 2)):
                for cc in range(max(0, c - 1), min(wb, c + 2)):
                    if a_blk[rr, cc] and not a_seen[rr, cc]:
                        a_seen[rr, cc] = True
                        l_todo.append((rr, cc))
        if len(l_group) >= 4:
            l_keep += l_group
    if not l_keep:
        raise SystemExit('no video tile found (nothing on screen changes): give --crop W:H:X:Y')
    a_rc = np.array(l_keep)
    y0, x0 = a_rc.min(0) * BLOCK
    y1, x1 = (a_rc.max(0) + 1) * BLOCK                       # exclusive, at 1/4 resolution

    # each side to the tile's own edge, pixel by pixel: inward while its line barely changes, outward while the next line
    # changes (first recording: 0.13 inside the tile, 0.00 one pixel outside)
    def live(a_line):
        return a_line.mean() >= LIVE_LINE
    while y1 - y0 > BLOCK and not live(a_frac[y0, x0:x1]): y0 += 1
    while y1 - y0 > BLOCK and not live(a_frac[y1 - 1, x0:x1]): y1 -= 1
    while x1 - x0 > BLOCK and not live(a_frac[y0:y1, x0]): x0 += 1
    while x1 - x0 > BLOCK and not live(a_frac[y0:y1, x1 - 1]): x1 -= 1
    for _ in range(BLOCK):
        if y0 > 0 and live(a_frac[y0 - 1, x0:x1]): y0 -= 1
        if y1 < a_frac.shape[0] and live(a_frac[y1, x0:x1]): y1 += 1
        if x0 > 0 and live(a_frac[y0:y1, x0 - 1]): x0 -= 1
        if x1 < a_frac.shape[1] and live(a_frac[y0:y1, x1]): x1 += 1
    # to screen pixels, even numbers (H.264), inside the screen
    hs, ws = a_frac.shape[0] * SCALE, a_frac.shape[1] * SCALE
    x, y = x0 * SCALE // 2 * 2, y0 * SCALE // 2 * 2
    w, h = (min(ws, x1 * SCALE) - x) // 2 * 2, (min(hs, y1 * SCALE) - y) // 2 * 2
    return int(w), int(h), int(x), int(y)


def preview(src, d, out):
    """One image to check the analysis by eye: the screen 5 s before and after the start, and before and after the end (the
    crop drawn in red), then the cropped picture at 1/4, 1/2 and 3/4 of the meeting. Video only."""
    w, h, x, y = d['crop']
    tmp = out.parent / 'preview_tiles'
    tmp.mkdir(exist_ok=True)
    l_tiles = []
    for i, (t, label) in enumerate([(d['start'] - 5, 'start -5 s'), (d['start'] + 5, 'start +5 s'),
                                    (d['end'] - 5, 'end -5 s'), (d['end'] + 5, 'end +5 s')]):
        t = min(max(0, t), d['duration'] - 0.5)
        vf = (f'drawbox={x}:{y}:{w}:{h}:color=red:t=12,scale=720:480:force_original_aspect_ratio=decrease,pad=720:480:-1:-1,'
              f"drawtext=text='{label} ({int(t)} s)':x=8:y=8:fontsize=26:fontcolor=yellow:box=1:boxcolor=black")
        l_tiles.append(tmp / f'{i}.png')
        run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{t:.2f}', '-i', src, '-frames:v', '1', '-vf', vf, l_tiles[-1]])
    for i, f in enumerate((0.25, 0.5, 0.75)):
        t = d['start'] + f * (d['end'] - d['start'])
        vf = (f'crop={w}:{h}:{x}:{y},scale=480:400:force_original_aspect_ratio=decrease,pad=480:400:-1:-1,'
              f"drawtext=text='crop {int(t)} s':x=8:y=8:fontsize=22:fontcolor=yellow:box=1:boxcolor=black")
        l_tiles.append(tmp / f'c{i}.png')
        run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{t:.2f}', '-i', src, '-frames:v', '1', '-vf', vf, l_tiles[-1]])
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + sum((['-i', p] for p in l_tiles), []) + [
        '-filter_complex', '[0][1]hstack[a];[2][3]hstack[b];[4][5][6]hstack=3[c];[a][b][c]vstack=3', '-frames:v', '1', out])
    for p in l_tiles:
        p.unlink()
    tmp.rmdir()


def workdir(src):
    work = WORK / re.sub(r'[^\w.-]+', '_', Path(src).stem)
    work.mkdir(parents=True, exist_ok=True)
    return work


def analyse(src, start=None, end=None, crop=None):
    """Writes and returns <work>/analysis.json (an override replaces what was found); for a video, draws <work>/preview.jpg."""
    work = workdir(src)
    duration, w, h = probe(src)
    found_start, found_end, thr = meeting_bounds(loudness(src))
    d = {'source': str(src), 'duration': round(duration, 2), 'screen': [w, h] if w else None, 'threshold_db': thr,
         'start': found_start if start is None else start, 'end': found_end if end is None else end,
         'found': {'start': found_start, 'end': found_end}, 'crop': None}
    if w:
        d['crop'] = [int(v) for v in crop.split(':')] if crop else find_crop(live_map(src, d['start'], d['end'], w, h))
    (work / 'analysis.json').write_text(json.dumps(d, indent=1))
    if w:
        preview(src, d, work / 'preview.jpg')
    return d


def video_kbps(duration, max_mb):
    """H.264 bitrate so that video + AAC + ~1 % of MP4 overhead stay SIZE_MARGIN under max_mb (MB = 1,000,000 bytes)."""
    return int(max_mb * 1e6 * SIZE_MARGIN * 8 / duration / 1000 / 1.01 - AUDIO_KBPS)


def hms(t):
    t = int(t)
    return f'{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}'


def paragraphs(l_seg):
    """Whisper's segments joined into lines of 30 s at most, a new line at a pause of 2 s; each line starts with its time."""
    l_par = []
    for s in l_seg:
        text = s['text'].strip()
        if not text:
            continue
        if l_par and s['start'] - l_par[-1]['end'] < 2 and s['start'] - l_par[-1]['start'] < 30:
            l_par[-1]['text'] += ' ' + text
            l_par[-1]['end'] = s['end']
        else:
            l_par.append({'start': s['start'], 'end': s['end'], 'text': text})
    return [f"[{hms(p['start'])}] {p['text']}" for p in l_par]


def hints(speakers, words):
    """The words to spell right, for Whisper's prompt: the speakers' names (without their roles) and the given words."""
    l_names = [re.sub(r'\s*\(.*?\)', '', s).strip() for s in speakers.split(';') if s.strip()]
    return ', '.join(l_names + ([words] if words else []))


def gemini_prompt(speakers, words):
    return (f'Transcribe this meeting word for word, in the language spoken. Speakers: {speakers}. Name each speaker by '
            f'first name. One line per speaker turn, exactly in this form: [hh:mm:ss] Name: text'
            + (f'. Spell these right: {words}.' if words else '.'))


def merge_prompt(speakers, words, whisper, gemini):
    return (f'Below are two transcripts of the same meeting, made independently from the same audio. Speakers: {speakers}.\n'
            'A (Whisper) has no speaker labels; its words are often right where B is unsure, but it misspells names. '
            'B (Gemini) has the speaker labels and spells names better.\n'
            'Write the final clean transcript: B\'s speaker turns and times, one line per turn, exactly in this form: '
            '[hh:mm:ss] Name: text. Where A and B differ, keep what was most likely said; where they agree, keep their words '
            'even when they make little sense (never your guess of what was meant: write it after them as [meant: …?]). '
            'Clean verbatim: remove the '
            'fillers (uh, um, mhm), stutters and repeated words; keep every sentence, every fact and the speakers\' own '
            'wording; never summarise, never add. Mark [?] after a word you are unsure of.'
            + (f' Spell these right: {words}.' if words else '') +
            f'\n\n=== A (Whisper) ===\n{whisper}\n\n=== B (Gemini) ===\n{gemini}\n')


def body(md):
    """A transcript file without its front matter and title: the timed lines only."""
    return '\n'.join(ln for ln in Path(md).read_text().splitlines() if ln.startswith('['))


def gemini(prompt, out, file=None, log=None):
    """Skill gemini's script; exit 3 (quota) or 4 (key) stop the run with its message."""
    if not GEMINI.exists():
        raise SystemExit(f'no skill gemini ({GEMINI}): deploy my-setup')
    r = subprocess.run([str(GEMINI), 'ask', prompt, '--model', GEMINI_MODEL, '--out', str(out)] + (['--file', str(file)] if file else []),
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if log:
        log.write(r.stderr)
    if r.returncode:
        raise SystemExit(f'gemini failed (exit {r.returncode}): {r.stderr.strip()[-600:]}')


def front(what, source, title):
    return f'---\nwhat: {what}\nsource: {source}\n---\n\n# {title}\n\n'


def whisper(audio, work, prompt, language=None):
    """Whisper large-v3-turbo (mlx-whisper, on the Mac's GPU) -> its timed lines; whisper.json and whisper.log stay in work."""
    need(4)                                                  # the turbo model takes ~3 GB on the GPU
    wav = work / 'whisper.wav'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', audio, '-ac', '1', '-ar', '16000', wav])
    with open(work / 'whisper.log', 'w') as log:
        run(WHISPER + [wav, '--model', WHISPER_MODEL, '--condition-on-previous-text', 'False', '--word-timestamps', 'True',
                       '--hallucination-silence-threshold', '2', '--output-format', 'json', '--output-dir', work,
                       '--output-name', 'whisper'] + (['--language', language] if language else [])
            + (['--initial-prompt', prompt] if prompt else []), stdout=log, stderr=subprocess.STDOUT)
    wav.unlink()
    return paragraphs(json.loads((work / 'whisper.json').read_text())['segments'])


def make(src, folder, name, speakers, words='', language=None, max_mb=300):
    """Every output missing from `folder`, in KINDS' order; a re-run does only what is missing."""
    src, folder = Path(src), Path(folder)
    if not folder.is_dir():
        raise SystemExit(f'no folder {folder}')
    work = workdir(src)
    if not (work / 'analysis.json').exists():
        analyse(src)
    d = json.loads((work / 'analysis.json').read_text())
    # when the meeting started: the recording's start (in QuickTime's name; else when the file was made, minus its length:
    # the file's own time is when the recording stopped) plus the start found
    m = re.search(r'(\d{4}-\d{2}-\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})', src.name)
    t0 = (datetime.strptime(f'{m[1]} {m[2]}:{m[3]}:{m[4]}', '%Y-%m-%d %H:%M:%S') if m else
          datetime.fromtimestamp(src.stat().st_birthtime - d['duration']))
    day = t0 + timedelta(seconds=d['start'])
    d_out = {k: folder / f'{day:%Y_%m_%d}_{name} - {k}' for k in KINDS}
    if not d['crop']:
        d_out.pop('recording.mp4')                           # an audio file: no video
    when = f'{day:%-d %b %Y %H:%M}'
    start, dur = d['start'], d['end'] - d['start']
    l_in = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-ss', start, '-i', src, '-t', dur]
    with open(work / 'make.log', 'a') as log:
        def step(l_cmd):
            log.write(f'\n== {datetime.now():%Y-%m-%d %H:%M:%S} {" ".join(map(str, l_cmd))}\n')
            log.flush()
            run(l_cmd, stdout=log, stderr=subprocess.STDOUT, cwd=work)

        def put(k, tmp):
            # into the folder; the work copy goes once the folder's copy has the same size (the folder is its only home)
            shutil.copyfile(tmp, d_out[k])
            if d_out[k].stat().st_size != tmp.stat().st_size:
                raise SystemExit(f'{d_out[k]}: the copy differs from {tmp}')
            tmp.unlink()
            print(f'{d_out[k]}  ({d_out[k].stat().st_size / 1e6:.1f} MB)', flush=True)

        if 'recording.mp4' in d_out and not d_out['recording.mp4'].exists():
            w, h, x, y = d['crop']
            l_x264 = ['-vf', f'crop={w}:{h}:{x}:{y},fps={FPS}', '-c:v', 'libx264', '-preset', 'slow',
                      '-b:v', f'{video_kbps(dur, max_mb)}k', '-passlogfile', work / 'x264']
            need(2)
            step(l_in + l_x264 + ['-pass', '1', '-an', '-f', 'null', '/dev/null'])
            step(l_in + l_x264 + ['-pass', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', f'{AUDIO_KBPS}k',
                                  '-movflags', '+faststart', work / 'recording.mp4'])
            for p in work.glob('x264-*.log*'):                  # the 2-pass statistics, ours alone
                p.unlink()
            if (work / 'recording.mp4').stat().st_size / 1e6 > max_mb:
                raise SystemExit(f'the MP4 is over {max_mb} MB: {work / "recording.mp4"}')
            put('recording.mp4', work / 'recording.mp4')
        mp3 = d_out['recording.mp3']                            # the transcripts are made from the folder's copy
        if not mp3.exists():
            step(l_in + ['-vn', '-c:a', 'libmp3lame', '-b:a', f'{MP3_KBPS}k', work / 'recording.mp3'])
            put('recording.mp3', work / 'recording.mp3')
        source = d_out['recording.mp3'].name
        if not d_out['transcription-whisper.md'].exists():
            l_lines = whisper(mp3, work, hints(speakers, words), language)
            d_out['transcription-whisper.md'].write_text(front(
                f'Raw transcript of {name}, {when}, by Whisper large-v3-turbo (mlx-whisper 0.4.3, run locally on the Mac, '
                f'{date.today():%-d %b %Y}), no speaker labels; times from the start of the MP3', source,
                f'{name}, {day:%-d %b %Y}: Whisper transcript') + '\n'.join(l_lines) + '\n')
        if not d_out['transcription-gemini.md'].exists():
            small = work / 'gemini_input.mp3'                   # Gemini hears 16 kHz mono anyway: 12 MB instead of 119 MB
            run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', mp3, '-ac', '1', '-ar', '16000', '-b:a', '32k', small])
            gemini(gemini_prompt(speakers, words), work / 'gemini.md', small, log)
            small.unlink()
            d_out['transcription-gemini.md'].write_text(front(
                f'Raw transcript of {name}, {when}, by Gemini {GEMINI_MODEL} (Gemini API, free key, {date.today():%-d %b %Y}),'
                ' speakers named; times from the start of the MP3', source, f'{name}, {day:%-d %b %Y}: Gemini transcript')
                + (work / 'gemini.md').read_text().strip() + '\n')
            (work / 'gemini.md').unlink()
        if not d_out['transcript.md'].exists():
            gemini(merge_prompt(speakers, words, body(d_out['transcription-whisper.md']),
                                body(d_out['transcription-gemini.md'])), work / 'merged.md', log=log)
            d_out['transcript.md'].write_text(front(
                f'Transcript of {name}, {when}: the Whisper and Gemini transcripts merged by Gemini {GEMINI_MODEL} '
                f'({date.today():%-d %b %Y}): speakers named, names spelled right, fillers removed (clean verbatim); [?] = '
                'a word unsure; times from the start of the MP3', source, f'{name}, {day:%-d %b %Y}')
                + (work / 'merged.md').read_text().strip() + '\n')
            (work / 'merged.md').unlink()
    for k, p in d_out.items():
        print(f'{"✓" if p.exists() else "✗"} {p}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('analyse')
    a.add_argument('src')
    a.add_argument('--start', type=int, help='seconds: replaces the start found')
    a.add_argument('--end', type=int, help='seconds: replaces the end found')
    a.add_argument('--crop', help='W:H:X:Y in screen pixels: replaces the crop found')
    m = sub.add_parser('make')
    m.add_argument('src')
    m.add_argument('folder', help="the meeting's folder (an interview: its stage folder on the Drive)")
    m.add_argument('name', help='what the files are named after the date: "Interview 2 with Anna"')
    m.add_argument('--speakers', required=True, help='"Jean Lescut-Müller; Anna Meier (Head of Robotics, Example AG)"')
    m.add_argument('--words', default='', help='other words to spell right: companies, products, places, jargon')
    m.add_argument('--language', help='en, fr, de… (default: Whisper detects it)')
    m.add_argument('--max-mb', type=int, default=300)
    args = ap.parse_args()
    if args.cmd == 'analyse':
        d = analyse(Path(args.src).expanduser(), args.start, args.end, args.crop)
        print(json.dumps({k: d[k] for k in ('start', 'end', 'crop', 'found')}))
        if d['crop']:
            print(workdir(args.src) / 'preview.jpg')
    else:
        make(Path(args.src).expanduser(), Path(args.folder).expanduser(), args.name, args.speakers, args.words,
             args.language, args.max_mb)


if __name__ == '__main__':
    main()
