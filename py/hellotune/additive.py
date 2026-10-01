"""
Native port of the Indigo (R4000) "Hollywood Boot Tune" -- the four-voice
sine synthesizer that runs on the machine's Motorola DSP56001 (HAL1 audio),
as found in ref/IP20prom/hello_tune.asm.

No DSP emulation: the *algorithm* is ported directly.

From the assembly:
  * 48000 Hz sample rate ("freq" macro: freq*256.0/48000.0)
  * four wavetable oscillators sharing a 256-entry sine table (the 56001's
    on-chip ROM table) read with linear interpolation
  * the four oscillators start at table offsets $100/$120/$140/$160 --
    i.e. 0, 45, 90 and 135 degrees
  * phase accumulators advance freq*256/48000 table entries per sample,
    with the integer part selecting the table slot and the fractional part
    the interpolation weight (the famous `lsr a` in the asm is just the
    DSP's MSW fraction-convention shift, so the weight is the true
    fraction)
  * each note is three linear envelope segments (attack / sustain /
    release); segment targets chain from the previous segment's target
    ("ramp"/"endpt" macros) with the asm's fixed gain vol = 1/0.41
  * left and right channels carry the same mix (tx1/tx2 write a1 twice)

Tune tables ("tune_type" in the asm):
  boot      -- "Bach fugue #2 5 notes": rest, C4+C6, +E4, +G4, C4-E4-G4-C5
  shutdown  -- "...backwards": full chord first, voices drop out one by one
  badgfx    -- "G Flat (Played when graphics fails ie when graphics are
                flat)": F#3/F#4/F#5/F#6 cluster
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

from .tunes import IP20_TABS, IP20_VOL

RATE = 48000
_TABLE_SIZE = 256

# The 256-entry sine table, values in [-1, 1), like the DSP ROM table.
_SINE = [math.sin(2.0 * math.pi * i / _TABLE_SIZE) for i in range(_TABLE_SIZE)]


def render_additive(tab: str,
                    progress_cb: Optional[Callable[[float], None]] = None,
                    ) -> List[float]:
    """Render one IP20 tune table to mono float samples (~[-1, 1)).

    ``progress_cb`` receives 0..1 as notes complete.
    """
    notes = IP20_TABS[tab]
    total_frames = sum(seg[1] for note in notes for seg in note[1])

    # Oscillator state: phase in table-entry units (one full cycle = 256).
    # Initial offsets 0/32/64/96 entries == 0/45/90/135 degrees, exactly as
    # r0..r3 are initialized in the asm.
    ph0, ph1, ph2, ph3 = 0.0, 32.0, 64.0, 96.0
    am0 = am1 = am2 = am3 = 0.0        # envelope amplitudes (fraction units)

    out: List[float] = []
    append = out.append
    sine = _SINE
    vol = IP20_VOL

    done_frames = 0
    for note_index, (freqs, segments) in enumerate(notes):
        # Frequencies take effect for the whole note (attack segment onward),
        # exactly as the DSP loads inc0..inc3 once per note.  Table entries
        # advanced per sample = freq / rate cycles * 256 entries.
        in0 = freqs[0] * _TABLE_SIZE / RATE
        in1 = freqs[1] * _TABLE_SIZE / RATE
        in2 = freqs[2] * _TABLE_SIZE / RATE
        in3 = freqs[3] * _TABLE_SIZE / RATE

        for targets, dur in segments:
            t0, t1, t2, t3 = (targets[0] * vol, targets[1] * vol,
                             targets[2] * vol, targets[3] * vol)
            # Linear ramp from the current amplitude to the target, as the
            # "ramp" macro computes: inc = (amp*vol - end)/dur
            ia0 = (t0 - am0) / dur
            ia1 = (t1 - am1) / dur
            ia2 = (t2 - am2) / dur
            ia3 = (t3 - am3) / dur

            for _ in range(dur):
                am0 += ia0
                am1 += ia1
                am2 += ia2
                am3 += ia3

                ph0 += in0
                ph1 += in1
                ph2 += in2
                ph3 += in3

                # wrap phases (the DSP does modulo-256 pointer arithmetic)
                if ph0 >= 256.0:
                    ph0 -= 256.0
                if ph1 >= 256.0:
                    ph1 -= 256.0
                if ph2 >= 256.0:
                    ph2 -= 256.0
                if ph3 >= 256.0:
                    ph3 -= 256.0

                total = 0.0

                k = int(ph0)
                f = ph0 - k
                s0 = sine[k]
                s1 = sine[k + 1 if k < 255 else 0]
                total += am0 * (s0 + f * (s1 - s0))

                k = int(ph1)
                f = ph1 - k
                s0 = sine[k]
                s1 = sine[k + 1 if k < 255 else 0]
                total += am1 * (s0 + f * (s1 - s0))

                k = int(ph2)
                f = ph2 - k
                s0 = sine[k]
                s1 = sine[k + 1 if k < 255 else 0]
                total += am2 * (s0 + f * (s1 - s0))

                k = int(ph3)
                f = ph3 - k
                s0 = sine[k]
                s1 = sine[k + 1 if k < 255 else 0]
                total += am3 * (s0 + f * (s1 - s0))

                append(total)

            # Snap to the exact target (the DSP stores the end values as the
            # next ramp's starting point), avoiding float drift.
            am0, am1, am2, am3 = t0, t1, t2, t3

            done_frames += dur

        if progress_cb is not None:
            progress_cb(done_frames / total_frames)

    return out
