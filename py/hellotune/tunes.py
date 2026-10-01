"""
Tune catalog for hello_tune-player.

Every parameter below is transcribed *exactly* from the SGI PROM sources in
``ref/`` (IRIX 6.5-era stand tree):

* ``ref/IP20prom/hello_tune.asm``   -- Indigo (R4000), HAL1 / DSP56001
* ``ref/IP26prom/hello_tune.c``      -- Power Indigo2 (R8000), HAL2
* ``ref/IP28prom/hello_tune.c``      -- Indigo2 Impact (R10000), HAL2
* ``ref/IP32prom/fw/hello_tune.c``   -- O2 (MACE audio + AD1843 codec)

Karplus-Strong plucks are 8-tuples in the PROM's own field order:

    (period, duration, start, amplitude, rho, s, s1, apcoeff)

The IP20 "Hollywood Boot Tune" is a native port of the DSP56001 assembly:
four sine wavetable oscillators (45 degree initial phase offsets) with
chained linear attack/sustain/release envelopes, running at 48 kHz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Musical helpers
# ---------------------------------------------------------------------------

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_A4 = 440.0


def note_name(freq: float) -> str:
    """Nearest scientific pitch name (with cents deviation) for a frequency."""
    if freq <= 0:
        return "--"
    semis = 12.0 * math.log2(freq / _A4)
    nearest = round(semis)
    cents = 100.0 * (semis - nearest)
    index = (nearest + 9) % 12          # A is index 9 in _NOTE_NAMES
    octave = 4 + (nearest + 9) // 12
    name = f"{_NOTE_NAMES[index]}{octave}"
    if abs(cents) >= 1.0:
        return f"{name}{cents:+.0f}c"
    return name


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Machine:
    id: str                    # "ip28"
    name: str                  # "Indigo2 Impact (R10000)"
    short: str                 # "Indigo2"
    cpu: str
    audio: str
    rate: int                  # native synthesis sample rate (Hz)
    channels: int              # output channels as played by the PROM (L==R)
    engine: str                # "karplus" | "additive"
    origin: str                # path of the PROM source this was ported from
    blurb: str
    random_boot: bool = False  # PROM picks a random boot tune when nvram unset


@dataclass(frozen=True)
class Tune:
    id: str                    # "ip28/boot-1"
    machine: str               # machine id
    name: str                  # human name
    kind: str                  # boot | shutdown | badgfx | variant
    engine: str
    rate: int
    channels: int
    # engine payloads:
    plucks: Optional[Tuple[Tuple[float, ...], ...]] = None   # karplus
    tab: Optional[str] = None                                  # additive (ip20)
    # PROM metadata:
    prom_name: str = ""       # identifier used in the PROM source
    prom_index: Optional[int] = None   # IP32 ".play <n>" number
    nvram_index: Optional[int] = None  # IP26/IP28 boottune nvram digit
    buflen: int = 0
    maxperiod: int = 0
    playlen: int = 0
    desc: str = ""

    @property
    def frames(self) -> int:
        """Rendered length in frames (one pluck iteration == one frame)."""
        if self.engine == "karplus":
            return max(p[2] + p[1] for p in self.plucks)
        return ip20_tab_frames(self.tab)

    @property
    def seconds(self) -> float:
        return self.frames / float(self.rate)

    @property
    def duration(self) -> str:
        s = self.seconds
        return f"{s:.2f} s" if s < 60 else f"{int(s//60)}:{s % 60:05.2f}"


# ---------------------------------------------------------------------------
# IP20 -- Indigo (R4000), "Hollywood Boot Tune" on the Motorola DSP56001.
#
# Native port of hello_tune.asm:  four wavetable sine oscillators with
# initial phase offsets of 0/45/90/135 degrees, 256-entry sine table with
# linear interpolation, chained linear envelope segments (attack, sustain,
# release) and 48000 Hz sample rate.  Volume constant vol = 1/0.41 from the
# asm is applied to envelope targets, as in the original.
# ---------------------------------------------------------------------------

IP20_RATE = 48000
IP20_VOL = 1.0 / 0.41          # "vol: equ (1/.41)" from hello_tune.asm

# Pitches of notes, verbatim from hello_tune.asm:
_IP20_NZ = 0.0
_IP20_NC = 261.62556530059863466908   # C
_IP20_NE = 329.62755691286992971007   # E
_IP20_NG = 391.99543598174929404191   # G
_IP20_NF = 349.22823143300388441417   # F
_IP20_NFS = 369.99442271163439889650  # F#

# Each note: (freqs[4], segments) where segments are (amp_targets[4], duration)
# in sample order -- attack, sustain, release, exactly as the macros expand.
_IP20_NOTE1 = ((_IP20_NF,) * 4,
               (((0.0, 0.0, 0.0, 0.0), 81),
                ((0.0, 0.0, 0.0, 0.0), 10000),
                ((0.0, 0.0, 0.0, 0.0), 5001)))
_IP20_NOTE2 = ((_IP20_NC, _IP20_NC * 4, _IP20_NZ, _IP20_NZ),
               (((0.2, 0.01, 0.0, 0.0), 100),
                ((0.2, 0.01, 0.0, 0.0), 5000),
                ((0.0, 0.0, 0.0, 0.0), 100)))
_IP20_NOTE3 = ((_IP20_NC, _IP20_NE, _IP20_NZ, _IP20_NZ),
               (((0.1, 0.1, 0.0, 0.0), 100),
                ((0.05, 0.05, 0.0, 0.0), 5000),
                ((0.0, 0.0, 0.0, 0.0), 100)))
_IP20_NOTE4 = ((_IP20_NC, _IP20_NE, _IP20_NG, _IP20_NZ),
               (((0.1, 0.1, 0.1, 0.0), 100),
                ((0.05, 0.05, 0.0, 0.05), 5000),
                ((0.0, 0.0, 0.0, 0.0), 100)))
_IP20_NOTE5 = ((_IP20_NC, _IP20_NE, _IP20_NG, _IP20_NC * 2),
               (((0.1, 0.1, 0.1, 0.1), 100),
                ((0.05, 0.05, 0.05, 0.05), 5000),
                ((0.0, 0.0, 0.0, 0.0), 48000)))
_IP20_NOTE6 = ((_IP20_NFS, _IP20_NFS * 2, _IP20_NFS * 4, _IP20_NFS / 2),
               (((0.1, 0.1, 0.1, 0.1), 100),
                ((0.05, 0.05, 0.05, 0.05), 5000),
                ((0.0, 0.0, 0.0, 0.0), 48000)))

# Tune tables ("tune_type" 0/1/2 in the asm).
IP20_TABS: Dict[str, Tuple[tuple, ...]] = {
    # "Tune Type 0 = Bach fugue #2 5 notes"
    "boot": (_IP20_NOTE1, _IP20_NOTE2, _IP20_NOTE3, _IP20_NOTE4, _IP20_NOTE5),
    # "Tune Type 1 = Bach fugue #2 5 notes, backwards"
    "shutdown": (_IP20_NOTE1, _IP20_NOTE5, _IP20_NOTE4, _IP20_NOTE3, _IP20_NOTE2),
    # "Tune Type 2 = G Flat (Played when graphics fails ie when graphics are flat)"
    "badgfx": (_IP20_NOTE1, _IP20_NOTE6),
}


def ip20_tab_frames(tab: str) -> int:
    """Total length in frames of an IP20 tune table."""
    return sum(seg[1] for note in IP20_TABS[tab] for seg in note[1])


# ---------------------------------------------------------------------------
# IP26 / IP28 / IP32 -- Karplus-Strong "pluck" chimes.
#
# The classic hand-written arpeggio of IP28 uses note macros from the PROM:
#     NOTE_A  = 44100/220, NOTE_C_ = 44100/277, NOTE_E = 44100/330,
#     NOTE_F_ = 44100/370, HI(x) = x/2 (integer division).
# ---------------------------------------------------------------------------

_RATE = 44100
_IP32_RATE = 34000    # the O2's AD1843 DAC, per the PROM's rate register
NOTE_A = _RATE // 220          # 200
NOTE_C_ = _RATE // 277         # 159
NOTE_E = _RATE // 330           # 133
NOTE_F_ = _RATE // 370          # 119


def _hi(x: int) -> int:        # HI(X) = X/2, C integer division
    return x // 2


# --- IP28 boot/stop tune 1: the classic hand-written arpeggios --------------

_STARTTUNE1 = (
    (NOTE_A, 88200, 0, 9500.0, 1.0, 0.5, 0.5, 0.25),
    (NOTE_C_, 88200, 6000, 12500.0, 1.0, 0.5, 0.5, 0.25),
    (NOTE_E, 88200, 12000, 12500.0, 1.0, 0.5, 0.5, 0.25),
    (_hi(NOTE_A), 88200, 18000, 12500.0, 1.0, 0.5, 0.5, 0.50),
    (_hi(NOTE_C_), 88200, 24000, 12500.0, 1.0, 0.5, 0.5, 0.50),
    (_hi(NOTE_E), 88200, 30000, 12500.0, 1.0, 0.5, 0.5, 0.50),
    (_hi(_hi(NOTE_A)), 88200, 36000, 15500.0, 1.0, 0.5, 0.5, 0.75),
)

_STOPTUNE1 = (
    (_hi(_hi(NOTE_A)), 88200, 0, 12500.0, 1.0, 0.5, 0.5, 0.2),
    (_hi(NOTE_F_), 88200, 8000, 12500.0, 1.0, 0.5, 0.5, 0.2),
    (_hi(NOTE_E), 88200, 16000, 12500.0, 1.0, 0.5, 0.5, 0.2),
    (_hi(NOTE_C_), 88200, 24000, 12500.0, 1.0, 0.5, 0.5, 0.2),
    (_hi(NOTE_A), 88200, 32000, 12500.0, 1.0, 0.5, 0.5, 0.2),
)

# --- Shared parameter lists (identical numbers in IP26 and IP28) ------------

_STARTTUNE1A = (
    (188, 88200, 2756, 13567.585938, 0.990306, 0.500000, 0.500000, 0.173776),
    (149, 88200, 8039, 13823.578125, 0.992407, 0.500000, 0.500000, 0.196720),
    (133, 88200, 12173, 11519.648438, 0.993313, 0.500000, 0.500000, 0.553575),
    (99, 88200, 14929, 13823.578125, 0.995269, 0.500000, 0.500000, 0.157895),
    (94, 88200, 19351, 14847.546875, 0.995622, 0.500000, 0.500000, 0.814962),
    (74, 88200, 22968, 13567.585938, 0.996960, 0.500000, 0.500000, 0.261339),
    (66, 88200, 26816, 14335.562500, 0.997613, 0.500000, 0.500000, 0.435053),
    (62, 88200, 30605, 13567.585938, 0.997944, 0.500000, 0.500000, 0.220089),
    (66, 88200, 34855, 14335.562500, 0.997613, 0.500000, 0.500000, 0.435053),
)

_STARTTUNE2 = (
    (188, 88200, 2796, 6655.796875, 0.990306, 0.500000, 0.500000, 0.173776),
    (133, 88200, 8596, 14335.562500, 0.993313, 0.500000, 0.500000, 0.553575),
    (125, 88200, 14510, 13055.601563, 0.993736, 0.500000, 0.500000, 0.124575),
    (99, 88200, 18357, 12543.617188, 0.995269, 0.500000, 0.500000, 0.157895),
    (94, 88200, 23123, 12031.632813, 0.995622, 0.500000, 0.500000, 0.814962),
    (74, 88200, 27889, 13823.578125, 0.996960, 0.500000, 0.500000, 0.261339),
    (62, 88200, 34895, 13823.578125, 0.997944, 0.500000, 0.500000, 0.220089),
)

_STARTTUNE3 = (
    (188, 88200, 2930, 13567.585938, 0.990306, 0.500000, 0.500000, 0.173776),
    (125, 88200, 8673, 15871.515625, 0.993736, 0.500000, 0.500000, 0.124575),
    (74, 88200, 13037, 14335.562500, 0.996960, 0.500000, 0.500000, 0.261339),
    (62, 88200, 16310, 14335.562500, 0.997944, 0.500000, 0.500000, 0.220089),
    (46, 88200, 18721, 11263.656250, 0.999738, 0.500000, 0.500000, 0.110509),
    (37, 88200, 22396, 13567.585938, 1.000000, 0.168371, 0.831629, 0.455139),
)

_STOPTUNE1A = (
    (62, 88200, 1025, 12543.617188, 0.997944, 0.500000, 0.500000, 0.220089),
    (66, 88200, 3953, 14079.570313, 0.997613, 0.500000, 0.500000, 0.435053),
    (74, 88200, 8719, 13567.585938, 0.996960, 0.500000, 0.500000, 0.261339),
    (62, 88200, 15150, 13823.578125, 0.997944, 0.500000, 0.500000, 0.220089),
    (66, 88200, 19457, 14079.570313, 0.997613, 0.500000, 0.500000, 0.435053),
    (74, 88200, 24166, 13823.578125, 0.996960, 0.500000, 0.500000, 0.261339),
    (94, 88200, 28013, 12287.625000, 0.995622, 0.500000, 0.500000, 0.814962),
    (99, 88200, 32779, 12031.632813, 0.995269, 0.500000, 0.500000, 0.157895),
)

_STOPTUNE2 = (
    (62, 88200, 2239, 16383.500000, 0.997944, 0.500000, 0.500000, 0.220089),
    (66, 88200, 7062, 14591.554688, 0.997613, 0.500000, 0.500000, 0.435053),
    (74, 88200, 12517, 12031.632813, 0.996960, 0.500000, 0.500000, 0.261339),
    (94, 88200, 20327, 14591.554688, 0.995622, 0.500000, 0.500000, 0.814962),
    (74, 88200, 32902, 11775.640625, 0.996960, 0.500000, 0.500000, 0.261339),
    (94, 88200, 42951, 11007.664063, 0.995622, 0.500000, 0.500000, 0.814962),
    (99, 88200, 54550, 10751.671875, 0.995269, 0.500000, 0.500000, 0.157895),
)

_STOPTUNE3 = (
    (83, 88200, 2591, 15871.515625, 0.996301, 0.500000, 0.500000, 0.123125),
    (188, 88200, 3223, 15871.515625, 0.990306, 0.500000, 0.500000, 0.173776),
    (74, 88200, 8448, 13567.585938, 0.996960, 0.500000, 0.500000, 0.261339),
    (83, 88200, 11951, 14335.562500, 0.996301, 0.500000, 0.500000, 0.123125),
    (94, 88200, 17349, 13311.593750, 0.995622, 0.500000, 0.500000, 0.814962),
    (125, 88200, 22172, 13567.585938, 0.993736, 0.500000, 0.500000, 0.124575),
)

_BADGFX = tuple(
    (period, 88200, start, 16383.500000, rho, 0.500000, 0.500000, ap)
    for start in (0, 22050, 44100)
    for period, rho, ap in (
        (168, 0.991410, 0.884139),
        (211, 0.989081, 0.067127),
        (158, 0.991921, 0.249304),
        (224, 0.988417, 0.331005),
        (282, 0.985368, 0.007285),
        (252, 0.986976, 0.892327),
    )
)  # "devil chords (three)" -- six detuned strings struck at t=0, 0.5s, 1.0s

# --- IP32 (O2) parameter lists -----------------------------------------------

_IP32_S4_1 = (
    (44, 88200, 0, 14079.570312, 1.000000, 0.378496, 0.621504, 0.577689),
    (55, 88200, 9382, 12287.625000, 0.998624, 0.500000, 0.500000, 0.142429),
    (44, 88200, 18765, 13055.601562, 1.000000, 0.378496, 0.621504, 0.577689),
    (41, 88200, 28148, 11007.664062, 1.000000, 0.271694, 0.728306, 0.070275),
    (37, 88200, 37531, 12543.617188, 1.000000, 0.168371, 0.831629, 0.455139),
)

_IP32_S5_1 = (
    (83, 88200, 0, 11775.640625, 0.996301, 0.500000, 0.500000, 0.123125),
    (66, 88200, 10176, 9215.718750, 0.997613, 0.500000, 0.500000, 0.435053),
    (55, 88200, 20353, 8191.750000, 0.998624, 0.500000, 0.500000, 0.142429),
    (33, 88200, 30530, 12031.632812, 1.000000, 0.111528, 0.888472, 0.497777),
    (41, 88200, 40706, 11263.656250, 1.000000, 0.271694, 0.728306, 0.070275),
    (33, 88200, 50883, 5631.828125, 1.000000, 0.111528, 0.888472, 0.497777),
)

_IP32_S6_1 = (
    (83, 88200, 0, 7679.765625, 0.996301, 0.500000, 0.500000, 0.123125),
    (66, 88200, 9449, 11007.664062, 0.997613, 0.500000, 0.500000, 0.435053),
    (55, 88200, 18899, 9983.695312, 0.998624, 0.500000, 0.500000, 0.142429),
    (33, 88200, 28349, 10239.687500, 1.000000, 0.111528, 0.888472, 0.497777),
    (41, 88200, 37799, 10239.687500, 1.000000, 0.271694, 0.728306, 0.070275),
)

_IP32_S6_3 = (
    (83, 88200, 0, 14591.554688, 0.996301, 0.500000, 0.500000, 0.123125),
    (66, 88200, 9449, 8191.750000, 0.997613, 0.500000, 0.500000, 0.435053),
    (55, 88200, 18899, 13311.593750, 0.998624, 0.500000, 0.500000, 0.142429),
    (33, 88200, 28349, 9215.718750, 1.000000, 0.111528, 0.888472, 0.497777),
    (41, 88200, 37799, 11775.640625, 1.000000, 0.271694, 0.728306, 0.070275),
)

_IP32_T1_1 = (
    (99, 88200, 0, 13311.593750, 0.995269, 0.500000, 0.500000, 0.157895),
    (83, 88200, 16537, 14079.570312, 0.996301, 0.500000, 0.500000, 0.123125),
    (28, 88200, 22050, 10751.671875, 1.000000, 0.062973, 0.937027, 0.882610),
    (33, 88200, 38587, 8959.726562, 1.000000, 0.111528, 0.888472, 0.497777),
)

_IP32_T2_2 = (
    (99, 88200, 0, 15871.515625, 0.995269, 0.500000, 0.500000, 0.157895),
    (88, 88200, 13230, 14847.546875, 0.995965, 0.500000, 0.500000, 0.115853),
    (112, 88200, 26460, 15871.515625, 0.994531, 0.500000, 0.500000, 0.997379),
    (99, 88200, 39689, 13567.585938, 0.995269, 0.500000, 0.500000, 0.157895),
    (83, 88200, 52920, 13311.593750, 0.996301, 0.500000, 0.500000, 0.123125),
    (66, 88200, 52920, 11775.640625, 0.997613, 0.500000, 0.500000, 0.435053),
)

_IP32_T3_2 = (
    (168, 88200, 0, 8703.734375, 0.991410, 0.500000, 0.500000, 0.884139),
    (149, 88200, 11812, 13823.578125, 0.992407, 0.500000, 0.500000, 0.196720),
    (28, 88200, 23624, 10751.671875, 1.000000, 0.062973, 0.937027, 0.882610),
    (83, 88200, 35436, 6911.789062, 0.996301, 0.500000, 0.500000, 0.123125),
    (33, 88200, 47249, 7679.765625, 1.000000, 0.111528, 0.888472, 0.497777),
)

_IP32_T4_1 = (
    (168, 88200, 0, 6143.812500, 0.991410, 0.500000, 0.500000, 0.884139),
    (112, 88200, 6013, 4863.851562, 0.994531, 0.500000, 0.500000, 0.997379),
    (133, 88200, 12026, 5631.828125, 0.993313, 0.500000, 0.500000, 0.553575),
    (112, 88200, 18039, 4351.867188, 0.994531, 0.500000, 0.500000, 0.997379),
    (125, 88200, 24052, 9983.695312, 0.993736, 0.500000, 0.500000, 0.124575),
    (112, 88200, 30065, 7423.773438, 0.994531, 0.500000, 0.500000, 0.997379),
    (168, 88200, 36078, 4095.875000, 0.991410, 0.500000, 0.500000, 0.884139),
)

_IP32_T4_2 = (
    (168, 88200, 0, 6143.812500, 0.991410, 0.500000, 0.500000, 0.884139),
    (112, 88200, 6013, 4863.851562, 0.994531, 0.500000, 0.500000, 0.997379),
    (133, 88200, 12026, 5631.828125, 0.993313, 0.500000, 0.500000, 0.553575),
    (112, 88200, 18039, 4351.867188, 0.994531, 0.500000, 0.500000, 0.997379),
    (125, 88200, 24052, 9983.695312, 0.993736, 0.500000, 0.500000, 0.124575),
    (112, 88200, 30065, 7423.773438, 0.994531, 0.500000, 0.500000, 0.997379),
    (83, 88200, 36078, 9215.718750, 0.996301, 0.500000, 0.500000, 0.123125),
)

_IP32_T4_3 = (
    (168, 88200, 0, 6655.796875, 0.991410, 0.500000, 0.500000, 0.884139),
    (112, 88200, 6013, 5119.843750, 0.994531, 0.500000, 0.500000, 0.997379),
    (133, 88200, 12026, 6655.796875, 0.993313, 0.500000, 0.500000, 0.553575),
    (112, 88200, 18039, 4607.859375, 0.994531, 0.500000, 0.500000, 0.997379),
    (125, 88200, 24052, 7935.757812, 0.993736, 0.500000, 0.500000, 0.124575),
    (112, 88200, 30065, 8447.742188, 0.994531, 0.500000, 0.500000, 0.997379),
    (83, 88200, 36078, 7167.781250, 0.996301, 0.500000, 0.500000, 0.123125),
)

_IP32_T4_4 = (
    (168, 88200, 0, 7167.781250, 0.991410, 0.500000, 0.500000, 0.884139),
    (112, 88200, 6013, 6655.796875, 0.994531, 0.500000, 0.500000, 0.997379),
    (133, 88200, 12026, 6399.804688, 0.993313, 0.500000, 0.500000, 0.553575),
    (112, 88200, 18039, 5375.835938, 0.994531, 0.500000, 0.500000, 0.997379),
    (125, 88200, 24052, 9215.718750, 0.993736, 0.500000, 0.500000, 0.124575),
    (112, 88200, 30065, 13311.593750, 0.994531, 0.500000, 0.500000, 0.997379),
    (83, 88200, 36078, 9215.718750, 0.996301, 0.500000, 0.500000, 0.123125),
)

# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------

MACHINES: Dict[str, Machine] = {
    "ip20": Machine(
        id="ip20", name="Indigo (R4000)", short="Indigo",
        cpu="MIPS R4000", audio="HAL1 (Motorola DSP56001)",
        rate=IP20_RATE, channels=2, engine="additive",
        origin="ref/IP20prom/hello_tune.asm",
        blurb="The 'Hollywood Boot Tune': a five-note C-major fugue synthesized "
              "natively on the machine's DSP56001 -- four sine voices enter one "
              "at a time, like a fugue. Graphics failure plays 'G Flat' "
              "(when graphics are flat).",
    ),
    "ip26": Machine(
        id="ip26", name="Power Indigo2 (R8000)", short="Power Indigo2",
        cpu="MIPS R8000", audio="HAL2",
        rate=_RATE, channels=1, engine="karplus",
        origin="ref/IP26prom/hello_tune.c",
        blurb="Karplus-Strong plucked-string chimes. The PROM picks a random "
              "boot tune when the 'boottune' NVRAM variable is unset.",
        random_boot=True,
    ),
    "ip28": Machine(
        id="ip28", name="Indigo2 Impact (R10000)", short="Indigo2",
        cpu="MIPS R10000", audio="HAL2",
        rate=_RATE, channels=1, engine="karplus",
        origin="ref/IP28prom/hello_tune.c",
        blurb="Home of the classic hand-written A-major arpeggio boot chime "
              "plus three numbered variants, goodbye tunes and the "
              "'devil chords' graphics-failure tune.",
        random_boot=True,
    ),
    "ip32": Machine(
        id="ip32", name="O2 (R5000/R10000)", short="O2",
        cpu="MIPS R5000 / R10000 / RM5200", audio="MACE + AD1843 codec",
        # The PROM writes 34000 to the AD1843 CLK_GEN1_RATE register (the
        # register is literal Hz -- LSB==1Hz per the IRIX kernel's
        # ad1843.h), while the comment in hello_tune.c wrongly claims
        # "set at 44100 sample rate".  The note tables themselves were
        # designed for 44100 Hz (their periods land within cents of a
        # C-major pattern), so real O2s played the chimes about 4.4
        # semitones lower and 30% slower than designed.  We render at
        # the true DAC rate, exactly like the hardware.
        rate=_IP32_RATE, channels=2, engine="karplus",
        origin="ref/IP32prom/fw/hello_tune.c",
        blurb="Eleven chimes selectable from the PROM monitor with "
              "'.play <n>' (0-10): hello tune, goodbye tune, graphics-failure "
              "tune and eight extra variants -- played by the O2's AD1843 "
              "codec at its true 34 kHz DAC rate.",
    ),
}

# ---------------------------------------------------------------------------
# Tunes
# ---------------------------------------------------------------------------

def _k(machine, ident, plucks, *, buflen, maxperiod, playlen, kind, name, desc,
       prom_name=None, prom_index=None, nvram_index=None):
    m = MACHINES[machine]
    return Tune(
        id=f"{machine}/{ident}", machine=machine, name=name, kind=kind,
        engine=m.engine, rate=m.rate, channels=m.channels,
        plucks=pluck_tpl(plucks), prom_name=prom_name or ident,
        prom_index=prom_index, nvram_index=nvram_index,
        buflen=buflen, maxperiod=maxperiod, playlen=playlen, desc=desc,
    )


def pluck_tpl(seq) -> Tuple[Tuple[float, ...], ...]:
    return tuple(tuple(p) for p in seq)


def _a(machine, ident, tab_name, *, kind, name, desc):
    m = MACHINES[machine]
    return Tune(
        id=f"{machine}/{ident}", machine=machine, name=name, kind=kind,
        engine=m.engine, rate=m.rate, channels=m.channels,
        tab=tab_name, prom_name=f"tab{ {'boot':'0','shutdown':'1','badgfx':'2'}[tab_name] }",
        desc=desc,
    )


TUNES: List[Tune] = [
    # ----- IP20: Indigo (R4000) -------------------------------------------
    _a("ip20", "boot", "boot", kind="boot", name="Hollywood Boot Tune (fugue)",
       desc="Tune type 0 -- 'Bach fugue #2, 5 notes'. A C-major fugue on four "
            "DSP oscillators: rest, C4+C6, +E4, +G4, then the full "
            "C4-E4-G4-C5 chord ringing out over a full second."),
    _a("ip20", "shutdown", "shutdown", kind="shutdown", name="Fugue, backwards",
       desc="Tune type 1 -- 'Bach fugue #2 5 notes, backwards'. The full chord "
            "sounds first, then voices fall away one at a time."),
    _a("ip20", "badgfx", "badgfx", kind="badgfx", name="G Flat",
       desc="Tune type 2 -- 'G Flat (Played when graphics fails ie when "
            "graphics are flat)': an F#3/F#4/F#5/F#6 cluster with a long fade."),

    # ----- IP26: Power Indigo2 ----------------------------------------------
    _k("ip26", "boot-1", _STARTTUNE1A, buflen=123055, maxperiod=188, playlen=85000,
       kind="boot", name="Boot tune 1", nvram_index=1,
       desc="Nine ascending Karplus-Strong plucks across two octaves."),
    _k("ip26", "boot-2", _STARTTUNE2, buflen=143095, maxperiod=188, playlen=65000,
       kind="boot", name="Boot tune 2", nvram_index=2,
       desc="A seven-note rising flourish."),
    _k("ip26", "boot-3", _STARTTUNE3, buflen=134596, maxperiod=188, playlen=71000,
       kind="boot", name="Boot tune 3", nvram_index=3,
       desc="Six plucks climbing into the treble register."),
    _k("ip26", "stop-1", _STOPTUNE1A, buflen=130979, maxperiod=99, playlen=80000,
       kind="shutdown", name="Goodbye tune 1", nvram_index=1,
       desc="Eight quick plucks -- a tumbling cascade."),
    _k("ip26", "stop-2", _STOPTUNE2, buflen=142750, maxperiod=99, playlen=90000,
       kind="shutdown", name="Goodbye tune 2", nvram_index=2,
       desc="Seven plucks strung along a longer goodbye."),
    _k("ip26", "stop-3", _STOPTUNE3, buflen=141372, maxperiod=188, playlen=70000,
       kind="shutdown", name="Goodbye tune 3", nvram_index=3,
       desc="Six descending plucks."),
    _k("ip26", "badgfx", _BADGFX, buflen=132300, maxperiod=282, playlen=132300,
       kind="badgfx", name="Devil chords",
       desc="The hand-hacked 'devil chords (three)': six detuned strings "
            "struck simultaneously at t=0, 0.5 s and 1.0 s."),

    # ----- IP28: Indigo2 Impact ---------------------------------------------
    _k("ip28", "boot-1", _STARTTUNE1, buflen=88200 + 36000, maxperiod=200,
       playlen=85000, kind="boot", name="Boot chime (classic arpeggio)",
       nvram_index=1,
       desc="THE Indigo2 boot chime: a hand-written rising A-major arpeggio "
            "(A-C#-E-A-C#-E-A), each note a Karplus-Strong pluck."),
    _k("ip28", "boot-1a", _STARTTUNE1A, buflen=123055, maxperiod=188,
       playlen=85000, kind="boot", name="Boot tune 1a", nvram_index=2,
       desc="The nine-pluck parameter list shipped in later PROMs "
            "(same numbers as Power Indigo2 boot tune 1)."),
    _k("ip28", "boot-2", _STARTTUNE2, buflen=143095, maxperiod=188, playlen=65000,
       kind="boot", name="Boot tune 2", nvram_index=3,
       desc="A seven-note rising flourish."),
    _k("ip28", "boot-3", _STARTTUNE3, buflen=134596, maxperiod=188, playlen=71000,
       kind="boot", name="Boot tune 3", nvram_index=4,
       desc="Six plucks climbing into the treble register."),
    _k("ip28", "stop-1", _STOPTUNE1, buflen=88200 + 32000, maxperiod=100,
       playlen=80000, kind="shutdown", name="Goodbye chime (classic arpeggio)",
       nvram_index=1,
       desc="The classic Indigo2 goodbye: a falling A-major arpeggio "
            "(A-F#-E-C#-A)."),
    _k("ip28", "stop-1a", _STOPTUNE1A, buflen=130979, maxperiod=99,
       playlen=80000, kind="shutdown", name="Goodbye tune 1a", nvram_index=2,
       desc="The eight-pluck tumbling cascade."),
    _k("ip28", "stop-2", _STOPTUNE2, buflen=142750, maxperiod=99, playlen=90000,
       kind="shutdown", name="Goodbye tune 2", nvram_index=3,
       desc="Seven plucks strung along a longer goodbye."),
    _k("ip28", "stop-3", _STOPTUNE3, buflen=141372, maxperiod=188, playlen=70000,
       kind="shutdown", name="Goodbye tune 3", nvram_index=4,
       desc="Six descending plucks."),
    _k("ip28", "badgfx", _BADGFX, buflen=132300, maxperiod=282, playlen=132300,
       kind="badgfx", name="Devil chords",
       desc="The hand-hacked 'devil chords (three)': six detuned strings "
            "struck simultaneously at t=0, 0.5 s and 1.0 s."),

    # ----- IP32: O2 ----------------------------------------------------------
    _k("ip32", "boot", _IP32_T4_4, buflen=124278, maxperiod=168, playlen=124278,
       kind="boot", name="Boot chime (t4_4)", prom_name="t4_4", prom_index=0,
       desc="The O2's default hello tune: seven overlapping plucks, 177 ms "
            "apart. Designed as a C-major pattern at 44.1 kHz, but played by "
            "the O2's 34 kHz DAC a fourth lower, as on the real machine."),
    _k("ip32", "shutdown", _IP32_S6_1, buflen=125999, maxperiod=83,
       playlen=125999, kind="shutdown", name="Goodbye chime (s6_1)",
       prom_name="s6_1", prom_index=1,
       desc="The O2's default goodbye tune: five plucks, 278 ms apart."),
    _k("ip32", "badgfx", _IP32_T2_2, buflen=141120, maxperiod=112,
       playlen=141120, kind="badgfx", name="Graphics-failure chime (t2_2)",
       prom_name="t2_2", prom_index=2,
       desc="Six plucks ending with two struck together -- the O2's "
            "bad-graphics chord."),
    _k("ip32", "s5_1", _IP32_S5_1, buflen=139083, maxperiod=83,
       playlen=139083, kind="variant", name="Chime s5_1",
       prom_name="s5_1", prom_index=3,
       desc="A six-note ascending flourish designed in C major at 44.1 kHz, "
            "sounding a fourth lower at the O2's true 34 kHz DAC rate."),
    _k("ip32", "s4_1", _IP32_S4_1, buflen=125731, maxperiod=55,
       playlen=125731, kind="variant", name="Chime s4_1",
       prom_name="s4_1", prom_index=4,
       desc="Five bright plucks about 276 ms apart."),
    _k("ip32", "s6_3", _IP32_S6_3, buflen=125999, maxperiod=83,
       playlen=125999, kind="variant", name="Chime s6_3",
       prom_name="s6_3", prom_index=5,
       desc="A louder cousin of the goodbye tune."),
    _k("ip32", "t1_1", _IP32_T1_1, buflen=126787, maxperiod=99,
       playlen=126787, kind="variant", name="Chime t1_1",
       prom_name="t1_1", prom_index=6,
       desc="Four plucks with an unusually high one (period 28)."),
    _k("ip32", "t3_2", _IP32_T3_2, buflen=135449, maxperiod=168,
       playlen=135449, kind="variant", name="Chime t3_2",
       prom_name="t3_2", prom_index=7,
       desc="A low-string melody with a bright ping in the middle."),
    _k("ip32", "t4_1", _IP32_T4_1, buflen=124278, maxperiod=168,
       playlen=124278, kind="variant", name="Chime t4_1",
       prom_name="t4_1", prom_index=8,
       desc="Variant 1 of the boot-chime family."),
    _k("ip32", "t4_2", _IP32_T4_2, buflen=124278, maxperiod=168,
       playlen=124278, kind="variant", name="Chime t4_2",
       prom_name="t4_2", prom_index=9,
       desc="Variant 2 of the boot-chime family."),
    _k("ip32", "t4_3", _IP32_T4_3, buflen=124278, maxperiod=168,
       playlen=124278, kind="variant", name="Chime t4_3",
       prom_name="t4_3", prom_index=10,
       desc="Variant 3 of the boot-chime family."),
]

TUNES_BY_ID: Dict[str, Tune] = {t.id: t for t in TUNES}

KIND_LABELS = {
    "boot": "boot (hello)",
    "shutdown": "shutdown (goodbye)",
    "badgfx": "graphics failure",
    "variant": "PROM-selectable variant",
}


def tunes_for(machine: str, kind: Optional[str] = None) -> List[Tune]:
    out = [t for t in TUNES if t.machine == machine]
    if kind:
        out = [t for t in out if t.kind == kind]
    return out


def default_tune(machine: str, kind: str) -> Tune:
    """The tune the PROM actually plays for a given machine + event."""
    cands = tunes_for(machine, kind)
    if not cands:
        raise KeyError(f"machine {machine!r} has no {kind!r} tune")
    # First listed tune of a kind is the machine's default choice.
    return cands[0]


def tune_catalog() -> Dict[str, List[Tune]]:
    return {mid: tunes_for(mid) for mid in MACHINES}


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------

class TuneNotFound(LookupError):
    pass


_KINDWORDS = {"boot": "boot", "hello": "boot", "start": "boot",
              "shutdown": "shutdown", "stop": "shutdown",
              "goodbye": "shutdown", "bye": "shutdown",
              "badgfx": "badgfx", "gfx": "badgfx", "graphics": "badgfx",
              "fail": "badgfx", "failure": "badgfx",
              "variant": "variant", "chime": "variant"}


def _machine_of_token(tok: str):
    if tok in MACHINES:
        return tok
    for m_id, m in MACHINES.items():
        if tok in (m.short.lower(), m.name.lower(), m_id[2:]):
            return m_id
    return None


def _ambiguous(selector: str, cands) -> "TuneNotFound":
    return TuneNotFound(
        f"selector {selector!r} is ambiguous; matches: "
        + ", ".join(t.id for t in cands)
        + " -- add a machine prefix like 'ip28/boot-1'")


def find_tune(selector: str, machine: Optional[str] = None) -> Tune:
    """Resolve a tune selector.

    Accepted forms:
      * full id / unique id suffix      -- "ip28/boot-1", "boot-1", "s5_1"
      * machine + kind                  -- "ip28 boot", "o2 shutdown"
      * machine + kind + nvram/PROM #   -- "ip28 boot 2", "ip32 3"
      * bare kind (default machine)     -- "boot"
      * machine alone (its boot tune)  -- "ip20"
    Ambiguous selectors raise TuneNotFound listing the candidates.
    """
    sel = selector.strip().lower()
    if not sel:
        raise TuneNotFound("empty selector")

    tokens = sel.replace(":", " ").replace("/", " ").split()

    # pull out machine token
    mid = machine.lower() if machine else None
    for tok in list(tokens):
        m = _machine_of_token(tok)
        if m:
            mid = m
            tokens.remove(tok)
            break

    # pull out kind word
    kind = None
    for tok in list(tokens):
        if tok in _KINDWORDS:
            kind = _KINDWORDS[tok]
            tokens.remove(tok)
            break

    # pull out a number (PROM ".play #" or nvram digit)
    num = None
    for tok in list(tokens):
        if tok.isdigit():
            num = int(tok)
            tokens.remove(tok)
            break

    frag = "-".join(tokens)

    # --- name fragment present: id / suffix / prom-name / substring match
    if frag:
        cands = [t for t in TUNES
                 if t.id == frag
                 or t.id.endswith("/" + frag)
                 or t.prom_name.lower() == frag]
        if mid:
            scoped = [t for t in cands if t.machine == mid]
            if scoped:
                cands = scoped
        if len(cands) > 1:
            raise _ambiguous(selector, cands)
        if len(cands) == 1:
            return cands[0]
        # substring search over names
        cands = [t for t in TUNES
                 if frag in t.name.lower() or frag in t.prom_name.lower()
                 or frag in t.id]
        if mid:
            cands = [t for t in cands if t.machine == mid]
        if kind:
            cands = [t for t in cands if t.kind == kind]
        if num is not None:
            hits = [t for t in cands
                    if t.prom_index == num or t.nvram_index == num]
            if hits:
                cands = hits
        if len(cands) > 1:
            raise _ambiguous(selector, cands)
        if len(cands) == 1:
            return cands[0]
        raise TuneNotFound(f"no tune matches {selector!r}")

    # --- no name fragment: machine / kind / number combination
    if mid is None:
        mid = "ip32"
    pool = tunes_for(mid, kind)
    if num is not None:
        for t in pool:
            if t.prom_index == num or t.nvram_index == num:
                return t
        if 0 < num <= len(pool):
            return pool[num - 1]
        raise TuneNotFound(
            f"no tune #{num} for {mid}" + (f" kind {kind}" if kind else ""))
    if pool:
        return pool[0]
    raise TuneNotFound(f"no tunes match {selector!r}")
