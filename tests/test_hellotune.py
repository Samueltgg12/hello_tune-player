"""
Tests for hello_tune-player (run with: python3 -m unittest discover -s tests)

Covers the faithful engine details: LCG behaviour, randfill statistics,
pluck accumulation, catalog integrity, both engines, WAV writing, selector
resolution and note-name analysis.
"""

import os
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))

from hellotune import (MACHINES, TUNES, RenderOptions, find_tune,
                       render_tune)
from hellotune.additive import render_additive
from hellotune.karplus import (lcg_step, pluck, randfill, render_karplus)
from hellotune.tunes import (IP20_TABS, ip20_tab_frames, note_name,
                             TuneNotFound)
from hellotune.wavio import read_wav_info, wav_bytes, write_wav


class LcgTests(unittest.TestCase):
    def test_step_is_deterministic_and_wraps_int32(self):
        # hand-computed chain from state 1
        s = 1
        for _ in range(3):
            s = lcg_step(s)
        self.assertEqual(s, lcg_step(lcg_step(lcg_step(1))))

    def test_all_seeds_stay_in_int32_range(self):
        for seed in (0, 1, 0x7FFFFFFF, 0x80000000, 0xDEADBEEF, 0xFFFFFFFF):
            v = lcg_step(seed)
            self.assertGreaterEqual(v, -(2 ** 31))
            self.assertLessEqual(v, 2 ** 31 - 1)

    def test_noise_period(self):
        # a 32-bit LCG-with-shift can't have a longer period than 2^32;
        # just make sure it doesn't fall into a fixed point quickly
        s = 12345
        seen = set()
        for _ in range(10000):
            s = lcg_step(s)
            if s in seen:
                break
            seen.add(s)
        self.assertGreater(len(seen), 1000)


class RandfillTests(unittest.TestCase):
    def test_zero_mean_and_peak_normalized(self):
        n, amp = 83, 13567.585938
        buf = [0.0] * n
        randfill(buf, n, amp, [1])
        avg = sum(buf) / n
        self.assertLess(abs(avg), amp * 1e-6)
        self.assertAlmostEqual(max(abs(v) for v in buf), amp, delta=amp * 1e-9)

    def test_state_carries_across_calls(self):
        # different continuation -> different noise (mirrors reused stack slot)
        n = 40
        a = [0.0] * n
        state = [42]
        randfill(a, n, 100.0, state)
        b = [0.0] * n
        randfill(b, n, 100.0, state)
        self.assertNotEqual(a, b)


class PluckTests(unittest.TestCase):
    def _one_pluck(self, seed=7):
        params = (200, 1000, 0, 9500.0, 1.0, 0.5, 0.5, 0.25)
        buf = [0] * 1000
        pluck(params, buf, [0.0] * 200, [seed])
        return buf

    def test_accumulates_every_sample(self):
        buf = self._one_pluck()
        self.assertTrue(all(v != 0 for v in buf))

    def test_sample_is_shifted_by_8(self):
        buf = self._one_pluck()
        for v in buf:
            self.assertEqual(v % 256, 0, "words must be sample<<8")

    def test_seed_changes_output(self):
        self.assertNotEqual(self._one_pluck(seed=1), self._one_pluck(seed=2))

    def test_no_write_outside_note_span(self):
        params = (50, 100, 10, 100.0, 1.0, 0.5, 0.5, 0.25)
        buf = [0] * 200
        pluck(params, buf, [0.0] * 50, [3])
        self.assertTrue(all(v == 0 for v in buf[:10]))
        self.assertTrue(all(v == 0 for v in buf[110:]))


class CatalogTests(unittest.TestCase):
    def test_catalog_shape(self):
        self.assertEqual(len(TUNES), 30)
        self.assertEqual(len(MACHINES), 4)

    def test_buflen_covers_the_tune(self):
        # IP32 buflens are exact (last start + duration); the IP26/IP28
        # PROMs sometimes rounded allocations up.
        for t in TUNES:
            if t.engine != "karplus":
                continue
            last = max(p[2] + p[1] for p in t.plucks)
            self.assertGreaterEqual(t.buflen, last,
                                    f"{t.id}: buflen too small")
            self.assertEqual(t.frames, last)
            if t.machine == "ip32" or t.id in (
                    "ip28/boot-1", "ip28/stop-1", "ip26/stop-2",
                    "ip26/badgfx"):
                self.assertEqual(t.buflen, last,
                                 f"{t.id}: expected exact buflen")

    def test_pluck_field_count(self):
        for t in TUNES:
            if t.engine == "karplus":
                for p in t.plucks:
                    self.assertEqual(len(p), 8)
                    self.assertEqual(p[1], 88200)  # every note rings 2 s

    def test_ip32_prom_indices_unique(self):
        idx = [t.prom_index for t in TUNES if t.machine == "ip32"]
        self.assertEqual(sorted(i for i in idx if i is not None),
                         list(range(11)))

    def test_shared_tunes_match_between_ip26_and_ip28(self):
        pairs = [("ip26/boot-1", "ip28/boot-1a"),
                 ("ip26/boot-2", "ip28/boot-2"),
                 ("ip26/boot-3", "ip28/boot-3"),
                 ("ip26/stop-1", "ip28/stop-1a"),
                 ("ip26/stop-2", "ip28/stop-2"),
                 ("ip26/stop-3", "ip28/stop-3"),
                 ("ip26/badgfx", "ip28/badgfx")]
        for a_id, b_id in pairs:
            a = find_tune(a_id)
            b = find_tune(b_id)
            self.assertEqual(a.plucks, b.plucks, f"{a_id} vs {b_id}")

    def test_find_tune_forms(self):
        self.assertEqual(find_tune("ip28/boot-1").id, "ip28/boot-1")
        self.assertEqual(find_tune("ip26/boot-2").id, "ip26/boot-2")
        self.assertEqual(find_tune("ip28 boot").id, "ip28/boot-1")
        self.assertEqual(find_tune("o2 shutdown").id, "ip32/shutdown")
        self.assertEqual(find_tune("ip32 5").id, "ip32/s6_3")
        self.assertEqual(find_tune("ip32 10").id, "ip32/t4_3")
        self.assertEqual(find_tune("ip20").id, "ip20/boot")
        self.assertEqual(find_tune("boot").id, "ip32/boot")  # default machine
        self.assertEqual(find_tune("ip26 badgfx").id, "ip26/badgfx")
        self.assertEqual(find_tune("ip28 boot 2").id, "ip28/boot-1a")
        self.assertEqual(find_tune("s5_1").id, "ip32/s5_1")

    def test_find_tune_failures(self):
        with self.assertRaises(TuneNotFound):
            find_tune("ip32 99")
        with self.assertRaises(TuneNotFound):
            find_tune("ip28 does-not-exist")
        # ambiguous without a machine prefix
        with self.assertRaises(TuneNotFound) as cm:
            find_tune("boot-1")
        self.assertIn("ip26/boot-1", str(cm.exception))
        self.assertIn("ip28/boot-1", str(cm.exception))


class KarplusRenderTests(unittest.TestCase):
    def test_render_is_reproducible_with_seed(self):
        t = find_tune("ip28/boot-1")
        a = render_tune(t, RenderOptions(seed=42))
        b = render_tune(t, RenderOptions(seed=42))
        self.assertEqual(a.mono, b.mono)
        self.assertEqual(a.frames, 124200)
        self.assertEqual(a.seconds, 124200 / 44100)

    def test_seed_variation(self):
        t = find_tune("ip28/boot-1")
        a = render_tune(t, RenderOptions(seed=42))
        c = render_tune(t, RenderOptions(seed=43))
        self.assertNotEqual(a.mono, c.mono)

    def test_peak_normalization(self):
        t = find_tune("ip32/boot")
        r = render_tune(t, RenderOptions(seed=9, peak=0.5))
        self.assertLessEqual(r.peak_amplitude, 0.5 + 1e-6)
        self.assertGreater(r.peak_amplitude, 0.4)

    def test_authentic_levels_are_the_raw_dac_values(self):
        t = find_tune("ip26/boot-1")
        r = render_tune(t, RenderOptions(seed=9, authentic_levels=True))
        # raw 16-bit words were tiny; normalized OFF means keep them
        words = render_karplus(t.plucks, t.frames, 9, t.maxperiod)
        self.assertEqual(r.frames, len(words))

    def test_volume_zero_refuses(self):
        with self.assertRaises(ValueError):
            render_tune(find_tune("ip32/boot"), RenderOptions(volume=0))

    def test_o2_renders_at_true_34khz_dac_rate(self):
        # The PROM writes 34000 to the AD1843 rate register (literal Hz,
        # LSB==1Hz per the IRIX kernel's ad1843.h); the "44100" comment in
        # hello_tune.c is wrong.  Pitch and duration must match the hardware.
        t = find_tune("ip32/boot")
        self.assertEqual(t.rate, 34000)
        r = render_tune(t, RenderOptions(seed=9))
        self.assertEqual(r.rate, 34000)
        self.assertAlmostEqual(r.seconds, 124278 / 34000, places=3)
        # IP26/IP28 stay at the design rate of 44100.
        self.assertEqual(find_tune("ip28/boot-1").rate, 44100)
        self.assertEqual(find_tune("ip26/boot-1").rate, 44100)

    def test_every_tune_renders(self):
        for t in TUNES:
            r = render_tune(t, RenderOptions(seed=123))
            self.assertGreater(r.peak_amplitude, 0.05, t.id)
            self.assertLessEqual(r.peak_amplitude, 1.0 + 1e-9, t.id)


class AdditiveRenderTests(unittest.TestCase):
    def test_tab_lengths_match_macro_math(self):
        self.assertEqual(ip20_tab_frames("boot"), 15082 + 5200 * 3 + 53100)
        self.assertEqual(ip20_tab_frames("shutdown"), ip20_tab_frames("boot"))
        self.assertEqual(ip20_tab_frames("badgfx"), 15082 + 53100)

    def test_render_is_deterministic(self):
        self.assertEqual(render_additive("boot"), render_additive("boot"))

    def test_rest_is_silent_then_chime(self):
        out = render_additive("boot")
        silence = out[:12000]
        self.assertTrue(all(abs(v) == 0.0 for v in silence))
        onset = out[15082:17082]
        self.assertGreater(max(abs(v) for v in onset), 0.1)

    def test_ringout_fades(self):
        out = render_additive("boot")
        tail = out[-48000:]
        self.assertGreater(max(abs(v) for v in tail[:2000]),
                           max(abs(v) for v in tail[-2000:]) * 8)

    def test_gflat_uses_fsharp_cluster(self):
        out = render_additive("badgfx")
        self.assertEqual(len(out), ip20_tab_frames("badgfx"))


class WavTests(unittest.TestCase):
    def _render(self):
        t = find_tune("ip26/boot-1")
        return t, render_tune(t, RenderOptions(seed=4))

    def test_wav16_mono_conformant(self):
        t, r = self._render()
        blob = wav_bytes(r.mono, r.rate, 1, 16)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.wav")
            with open(p, "wb") as f:
                f.write(blob)
            w = wave.open(p)
            self.assertEqual((w.getnchannels(), w.getframerate(),
                              w.getsampwidth(), w.getnframes()),
                             (1, 44100, 2, r.frames))
            w.close()
            info = read_wav_info(p)
            self.assertEqual(info["bits"], 16)

    def test_wav24_and_stereo(self):
        t, r = self._render()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t24.wav")
            n = write_wav(p, r.mono, r.rate, 2, 24)
            self.assertGreater(n, 44100 * 6 * 2)
            info = read_wav_info(p)
            self.assertEqual((info["bits"], info["channels"]), (24, 2))
            self.assertEqual(info["data_bytes"], r.frames * 6)
            w = wave.open(p)
            self.assertEqual(w.getsampwidth(), 3)
            w.close()

    def test_odd_sizes_pad_and_riff_sizes(self):
        blob = wav_bytes([0.0, 0.5, -0.5], 44100, 1, 16)
        riff_size, = struct.unpack("<I", blob[4:8])
        self.assertEqual(riff_size + 8, len(blob))


class NoteNameTests(unittest.TestCase):
    def test_basic_names(self):
        self.assertEqual(note_name(440.0), "A4")
        self.assertEqual(note_name(261.6256), "C4")
        self.assertEqual(note_name(0.0), "--")

    def test_cents_deviation_marks(self):
        self.assertIn("c", note_name(445.0))   # ~+20c from A4


if __name__ == "__main__":
    unittest.main(verbosity=2)
