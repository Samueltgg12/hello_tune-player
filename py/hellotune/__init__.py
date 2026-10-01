"""
hello_tune-player -- play, explore and export the synthesized boot/shutdown
chimes of Silicon Graphics PROMs, natively re-synthesized in pure Python.

Supported machines (from the IRIX 6.5-era PROM sources in ``ref/``):

* IP20  -- Indigo (R4000), HAL1 audio, Motorola DSP56001 "Hollywood Boot Tune"
* IP26  -- Power Indigo2 (R8000), HAL2 audio, Karplus-Strong chimes
* IP28  -- Indigo2 Impact (R10000), HAL2 audio, Karplus-Strong chimes
* IP32  -- O2 (R5000/R10000), MACE audio + AD1843 codec, Karplus-Strong chimes

The chimes are *resynthesized* (never emulated and never sampled): the exact
algorithms and parameters from the PROM sources are ported to native Python.
"""

__version__ = "1.0.0"

from .tunes import MACHINES, TUNES, Tune, find_tune, tune_catalog  # noqa: F401
from .render import RenderOptions, RenderResult, render_tune       # noqa: F401
