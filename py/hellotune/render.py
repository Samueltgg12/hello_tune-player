"""
Render orchestration: engine dispatch, level handling, analysis.

Level pipeline (matching what the PROM's DACs received):

* Karplus-Strong engines produce 32-bit words holding ``(int)sample << 8``.
  The 16-bit value the HAL2 / AD1843 codec took from each word is its top
  half, so the raw mono signal is ``word >> 16`` -- quite quiet by DAC
  standards, which is authentic: the real machines amplified it.
* The IP20 DSP tune sums four sine oscillators in fraction units, i.e.
  floats roughly in [-1, 1).

By default we normalize the render to a target peak so every chime is
comfortably audible on modern hardware.  ``authentic_levels=True`` keeps
the raw codec-aligned levels instead (very quiet!).

The PROM's `volume` NVRAM variable (0 = "stay silent") is honoured: a
volume of 0 refuses to play.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import additive, karplus
from .tunes import MACHINES, Tune, note_name

_INT16_MIN = -32768
_INT16_MAX = 32767


@dataclass
class RenderOptions:
    seed: Optional[int] = None          # None => random "boot" each render
    normalize: bool = True              # normalize to target peak
    peak: float = 0.84                  # target peak amplitude (0..1)
    volume: float = 100.0               # 0..100 percent (0 = silence!)
    authentic_levels: bool = False      # keep raw, very quiet codec levels

    def clone(self, **over):
        d = dict(seed=self.seed, normalize=self.normalize, peak=self.peak,
                 volume=self.volume, authentic_levels=self.authentic_levels)
        d.update(over)
        return RenderOptions(**d)


@dataclass
class RenderResult:
    tune: Tune
    seed: int                          # the seed actually used
    mono: List[float]                  # post-level float samples, [-1, 1)
    rate: int
    channels: int                      # 1 = mono, 2 = identical L/R
    seconds: float
    peak_amplitude: float              # peak of the *final* signal

    @property
    def frames(self) -> int:
        return len(self.mono)

    def int16_samples(self) -> List[int]:
        """Quantize to 16-bit PCM with clipping."""
        out = []
        for x in self.mono:
            v = int(round(x * 32767.0))
            if v > _INT16_MAX:
                v = _INT16_MAX
            elif v < _INT16_MIN:
                v = _INT16_MIN
            out.append(v)
        return out

    def stereo_samples(self) -> List[int]:
        """Interleave to L,R (identical channels, like the PROMs did)."""
        out = []
        for v in self.int16_samples():
            out.append(v)
            out.append(v)
        return out


def random_seed() -> int:
    """A random 32-bit seed (the PROM used uninitialized stack garbage)."""
    return int.from_bytes(os.urandom(4), "big") & 0xFFFFFFFF


def render_tune(tune: Tune,
                options: Optional[RenderOptions] = None,
                progress_cb: Optional[Callable[[float], None]] = None,
                ) -> RenderResult:
    """Render one tune to level-adjusted mono float samples."""
    opts = options or RenderOptions()

    if opts.volume <= 0:
        raise ValueError(
            "volume is 0 -- the PROM would stay silent "
            "(set --volume > 0 to hear the tune)")

    seed = opts.seed if opts.seed is not None else random_seed()

    if tune.engine == "karplus":
        words = karplus.render_karplus(
            tune.plucks, tune.frames, seed,
            maxperiod=tune.maxperiod or None, progress_cb=progress_cb)
        # the 16-bit sample the codec received = top half of each word
        raw = [w >> 16 for w in words]
        peak_raw = max((abs(v) for v in raw), default=0)
        scale_ref = peak_raw / 32768.0 if peak_raw else 0.0
        mono = [v / 32768.0 for v in raw]
    elif tune.engine == "additive":
        samples = additive.render_additive(tune.tab, progress_cb=progress_cb)
        peak_raw = max((abs(v) for v in samples), default=0.0)
        scale_ref = peak_raw if peak_raw else 0.0
        mono = samples  # deterministic engine; `seed` is bookkeeping only
    else:
        raise ValueError(f"unknown engine {tune.engine!r}")

    if scale_ref <= 0.0:
        raise ValueError(f"tune {tune.id} rendered silent; refusing to play")

    # Level handling.
    if opts.normalize and not opts.authentic_levels:
        gain = opts.peak / scale_ref
    else:
        gain = 1.0
    gain *= max(0.0, min(100.0, opts.volume)) / 100.0

    mono = [x * gain for x in mono]

    peak_final = max((abs(x) for x in mono), default=0.0)
    return RenderResult(
        tune=tune, seed=seed, mono=mono, rate=tune.rate,
        channels=tune.channels, seconds=len(mono) / float(tune.rate),
        peak_amplitude=peak_final,
    )


# ---------------------------------------------------------------------------
# Analysis (for `info`, JSON output and the GUI details pane)
# ---------------------------------------------------------------------------

def analyze_tune(tune: Tune) -> Dict:
    """Parameter-level analysis of a tune: notes, frequencies, timings."""
    info: Dict = {
        "id": tune.id,
        "name": tune.name,
        "machine": tune.machine,
        "machine_name": MACHINES[tune.machine].name,
        "kind": tune.kind,
        "engine": tune.engine,
        "rate": tune.rate,
        "channels": tune.channels,
        "duration_seconds": round(tune.seconds, 3),
        "frames": tune.frames,
        "prom_name": tune.prom_name,
        "prom_index": tune.prom_index,
        "nvram_index": tune.nvram_index,
        "buflen": tune.buflen,
        "playlen": tune.playlen,
        "maxperiod": tune.maxperiod,
        "description": tune.desc,
    }

    notes: List[Dict] = []
    if tune.engine == "karplus":
        for i, p in enumerate(tune.plucks):
            period, duration, start, amp, rho, s, s1, ap = p
            freq = tune.rate / float(period)
            notes.append({
                "index": i,
                "period": period,
                "duration": duration,
                "start": start,
                "start_seconds": round(start / tune.rate, 3),
                "frequency_hz": round(freq, 2),
                "note": note_name(freq),
                "amplitude": amp,
                "rho": rho,
                "s": s,
                "s1": s1,
                "apcoeff": ap,
            })
    else:
        from .tunes import IP20_TABS
        t0 = 0
        for i, (freqs, segments) in enumerate(IP20_TABS[tune.tab]):
            dur = sum(seg[1] for seg in segments)
            max_targets = [max(seg[0][k] for seg in segments)
                           for k in range(4)]
            names = [note_name(f) for f, a in zip(freqs, max_targets) if a > 0]
            notes.append({
                "index": i,
                "start": t0,
                "start_seconds": round(t0 / tune.rate, 3),
                "duration": dur,
                "duration_seconds": round(dur / tune.rate, 3),
                "frequencies_hz": [round(f, 2) for f in freqs],
                "notes": names if names else ["(rest)"],
                "segments": [
                    {"targets": list(seg[0]), "duration": seg[1]}
                    for seg in segments],
            })
            t0 += dur
    info["notes"] = notes
    return info


def format_analysis(tune: Tune) -> str:
    """Human-readable dump of a tune's parameters."""
    a = analyze_tune(tune)
    m = MACHINES[tune.machine]
    lines = []
    add = lines.append
    add(f"{tune.name}   [{tune.id}]")
    add(f"  machine      : {m.name} -- {m.cpu}, {m.audio}")
    add(f"  kind         : {tune.kind}"
        + (f"  (PROM .play #{tune.prom_index})" if tune.prom_index is not None else "")
        + (f"  (nvram boottune '{tune.nvram_index}')" if tune.nvram_index else ""))
    add(f"  engine       : {tune.engine}")
    add(f"  sample rate  : {tune.rate} Hz, "
        f"{'stereo (L==R)' if tune.channels == 2 else 'mono'}")
    add(f"  length       : {tune.frames} frames = {tune.seconds:.2f} s")
    if tune.engine == "karplus":
        add(f"  PROM defines : buflen={tune.buflen} playlen={tune.playlen} "
            f"maxperiod={tune.maxperiod}")
        add(f"  plucks       : {len(tune.plucks)}")
        add("")
        add("  #   start(s)  period  freq(Hz)  note       amplitude   rho")
        for n in a["notes"]:
            add(f"  {n['index']:<2}  {n['start_seconds']:>7.2f}  "
                f"{n['period']:>6}  {n['frequency_hz']:>9.2f}  "
                f"{n['note']:<10} {n['amplitude']:>11.3f}  "
                f"{n['rho']:.6f}")
    else:
        add(f"  notes        : {len(a['notes'])} (four sine voices each)")
        add("")
        add("  #   start(s)  dur(s)  voices")
        for n in a["notes"]:
            add(f"  {n['index']:<2}  {n['start_seconds']:>7.2f}  "
                f"{n['duration_seconds']:>5.2f}  {' + '.join(n['notes'])}")
    add("")
    add(f"  {tune.desc}")
    return "\n".join(lines)
