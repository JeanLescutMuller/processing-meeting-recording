"""Processing a meeting's recording: the pure parts (start/end, crop, bitrate, transcript lines, prompts) on made-up numbers; no ffmpeg, Whisper or Gemini."""
import unittest

import numpy as np

import recording as R


class RecordingTest(unittest.TestCase):
    def test_meeting_bounds(self):
        # 100 s of quiet (one cough at 50 s), 300 s of talk with a few pauses, 50 s of quiet
        a_db = np.full(450, -60.0)
        a_db[50] = -15
        a_db[100:400] = -22
        a_db[[130, 131, 250, 251, 252]] = -58
        start, end, thr = R.meeting_bounds(a_db)
        self.assertEqual((start, end), (100 - R.MARGIN_S, 400 + R.MARGIN_S))
        self.assertTrue(-60 < thr < -22)

    def test_find_crop(self):
        # 1/4 scale screen 200x300: the big tile (rows 40-119, cols 10-149), the self view (rows 140-179, cols 100-149),
        # a blinking spot far away (one 8x8 block), a still document everywhere else
        a = np.zeros((200, 300))
        a[40:120, 10:150] = 0.1
        a[60:100, 40:120] = 0.4                                 # the face moves more than the blurred background
        a[140:180, 100:150] = 0.5
        a[0:8, 248:256] = 1.0
        self.assertEqual(R.find_crop(a), (560, 560, 40, 160))   # (w, h, x, y) in screen pixels: 4x

    def test_mp3_kbps_under_90_mb(self):
        self.assertEqual(R.mp3_kbps(1800), 320)                 # 30 min at 320 kbps = 72 MB
        self.assertEqual(R.mp3_kbps(3548), 196)                 # 59 min: 320 kbps would be 142 MB
        for dur in (600, 3548, 7200):
            self.assertLess(R.mp3_kbps(dur) * 1000 / 8 * dur / 1e6, 90)

    def test_video_kbps_fits(self):
        for dur in (600, 2965, 7200):
            kbps = R.video_kbps(dur, 300)
            self.assertLess((kbps + R.AUDIO_KBPS) * 1000 / 8 * dur * 1.01, 300e6)
        self.assertEqual(R.video_kbps(2965, 300), 649)          # first recording: 645 kbps gave 289 MB

    def test_paragraphs(self):
        l_seg = [{'start': 0.5, 'end': 3, 'text': ' Hello.'}, {'start': 3.2, 'end': 8, 'text': ' How are you?'},
                 {'start': 12, 'end': 15, 'text': ' Fine.'}, {'start': 15.5, 'end': 50, 'text': ' Long answer'},
                 {'start': 50.5, 'end': 52, 'text': ' still going.'}, {'start': 60, 'end': 61, 'text': ' '}]
        self.assertEqual(R.paragraphs(l_seg), ['[00:00:00] Hello. How are you?', '[00:00:12] Fine. Long answer',
                                               '[00:00:50] still going.'])
        self.assertEqual(R.hms(3725), '01:02:05')

    def test_hints_and_prompts(self):
        speakers = 'Jean Lescut-Müller; Anna Meier (Head of Robotics, Example AG)'
        self.assertEqual(R.hints(speakers, 'Sigyx, SLAM'), 'Jean Lescut-Müller, Anna Meier, Sigyx, SLAM')
        self.assertIn('[hh:mm:ss] Name: text. Spell these right: Sigyx.', R.gemini_prompt(speakers, 'Sigyx'))
        p = R.merge_prompt(speakers, '', '[00:00:01] hello', '[00:00:01] Jean: Hello.')
        self.assertTrue(p.endswith('=== B (Gemini) ===\n[00:00:01] Jean: Hello.\n'))


if __name__ == '__main__':
    unittest.main()
