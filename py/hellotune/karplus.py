"""
Karplus-Strong "pluck" synthesis engine -- a faithful native Python port of
``play_hello_tune()`` / ``maketune()`` / ``pluck()`` / ``randfill()`` from the
SGI PROM sources (ref/IP26prom, ref/IP28prom, ref/IP32prom hello_tune.c).

How faithful?
  * the exact one-pole lowpass + all-pass loop of the original:
        res1 = rho * (s1*sample + s*lastsample)
        res2 = ap*res1 + lastres1 - ap*res2
  * the exact ``(int)sample << 8`` fixed-point truncation into 32-bit words
  * the exact linear-congruential noise generator with 32-bit wrap-around:
        state = ((state * 1103515245 + 12345) >> 1) - 1
  * delay buffers are zero-mean, peak-normalized to the note amplitude, as
    in ``randfill()``.

The one thing the PROM could never do twice is sound the same: ``randfill()``
seeds its LCG from an *uninitialized stack variable* ("get random value to
start with no init!"), so every real boot sounded slightly different.  We model
that with a per-render seed (random by default, or fixed with --seed for
reproducible "boots").  The LCG state carries across notes, just like the
left-over stack slot would on the real hardware.

Output: a list of 32-bit sample *words*, exactly as the PROM accumulated them
(``sample << 8``); the 16-bit value the codec actually received is the word's
top half (see render.py).
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

_MASK32 = 0xFFFFFFFF
_FSCALE = 2147483648.0  # 2^31: (float)0x7fffffff rounds to 2^31 in C float32


def lcg_step(state: int) -> int:
    """One step of the PROM's myrand() macro, with C int32 wrap-around.

    C:  state = ((state * 1103515245 + 12345) >> 1) - 1
    (signed 32-bit multiply wraps; >> is an arithmetic shift on MIPS)
    """
    state = (state * 1103515245 + 12345) & _MASK32
    if state >= 0x80000000:          # interpret as signed int32
        state -= 0x100000000
    return (state >> 1) - 1          # Python >> on ints is arithmetic


def randfill(buf: List[float], n: int, amp: float, state: List[int]) -> None:
    """Port of randfill(): n noise samples in (-1,1), zero-mean, peak = amp.

    ``state`` is a one-element list so the LCG state carries across calls
    (mirroring the reused uninitialized stack slot of the original).
    """
    for i in range(n):
        state[0] = lcg_step(state[0])
        buf[i] = 2.0 * ((1.0 / _FSCALE) * state[0] - 0.5)

    # zero-mean the noise, then peak-normalize to amp
    total = 0.0
    for i in range(n):
        total += buf[i]
    avg = total / n

    maxabs = 0.0
    for i in range(n):
        buf[i] -= avg
        v = buf[i] if buf[i] >= 0.0 else -buf[i]
        if v > maxabs:
            maxabs = v

    if maxabs <= 0.0:               # degenerate but defensive
        return
    scale = amp / maxabs
    for i in range(n):
        buf[i] *= scale


def pluck(params: Sequence[float], buf: List[int],
          delay: List[float], state: List[int]) -> None:
    """Port of pluck(): accumulate one Karplus-Strong note into buf.

    params = (period, duration, start, amplitude, rho, s, s1, apcoeff)
    buf    = list of 32-bit words (one per frame), += accumulation
    delay  = shared delay-line scratch (maxperiod long)
    """
    period = int(params[0])
    duration = int(params[1])
    start = int(params[2])
    amplitude = float(params[3])
    rho = float(params[4])
    s = float(params[5])
    s1 = float(params[6])
    apcoeff = float(params[7])

    randfill(delay, period, amplitude, state)

    idx = start
    b = 0
    bend = period - 1
    lastsample = 0.0
    lastres1 = 0.0
    res2 = 0.0

    for _ in range(duration):
        sample = delay[b]
        res1 = rho * (s1 * sample + s * lastsample)
        res2 = apcoeff * res1 + lastres1 - apcoeff * res2

        # the PROM writes the truncated sample, shifted into the top of a
        # 32-bit word -- int() truncates toward zero exactly like C
        buf[idx] += int(sample) << 8

        lastsample = sample
        lastres1 = res1
        delay[b] = res2
        b += 1
        if b > bend:
            b = 0
        idx += 1


def render_karplus(plucks: Sequence[Sequence[float]],
                   nframes: int,
                   seed: int,
                   maxperiod: Optional[int] = None,
                   progress_cb: Optional[Callable[[float], None]] = None,
                   ) -> List[int]:
    """Render a whole tune; returns the accumulated 32-bit word buffer.

    Mirrors maketune(): one shared delay-line buffer, notes accumulated on
    top of each other at their start offsets.
    """
    if maxperiod is None:
        maxperiod = max(int(p[0]) for p in plucks)

    buf = [0] * nframes
    delay = [0.0] * maxperiod
    state = [seed & _MASK32]

    total = len(plucks)
    for i, p in enumerate(plucks):
        pluck(p, buf, delay, state)
        if progress_cb is not None:
            progress_cb((i + 1) / total)

    return buf
