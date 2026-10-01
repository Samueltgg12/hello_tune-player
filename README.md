# hello_tune-player

**Play, resynthesize and export the boot, shutdown and graphics-failure
chimes of Silicon Graphics workstations -- natively, in pure Python.**

These are the *synthesized* chimes from the SGI PROMs of the IRIX 6.5-era
source tree -- not recordings, not ADPCM samples, not emulation.  The
original synthesis algorithms and parameters were ported line-by-line to
Python, so every chime is regenerated live on your machine, exactly the way
the PROM regenerated it on theirs.

Supported machines (all data from `ref/`):

| Board | Machine | Audio | Engine |
|-------|---------|-------|--------|
| IP20 | **Indigo (R4000)** | HAL1 -- Motorola DSP56001 | 4-voice sine fugue, 48 kHz |
| IP26 | **Power Indigo2 (R8000)** | HAL2 | Karplus-Strong plucked string, 44.1 kHz |
| IP28 | **Indigo2 Impact (R10000)** | HAL2 | Karplus-Strong plucked string, 44.1 kHz |
| IP32 | **O2 (R5000/R10000)** | MACE + AD1843 codec | Karplus-Strong plucked string, 34 kHz stereo (the O2's true DAC rate) |

30 tunes in total, including:

* the classic hand-written **A-major arpeggio** boot/goodbye chimes of the
  Indigo2's (Power And Impact.),
* the O2's eleven `.play`-selectable chimes,
* the IP26/IP28 **"devil chords"** graphics-failure tune,
* the Indigo (R4000) **"Hollywood Boot Tune"** -- the asm calls it
  *"Bach fugue #2, 5 notes"* -- and its *"G Flat"* graphics-failure pun
  ("played when graphics fails, ie when graphics are flat").

Zero dependencies: Python 3.9+ standard library only.  Tkinter for the GUI
(`python3-tk`).

---

## Quick start

```sh
# from the project root (no install needed)
./hellotune list                       # everything, grouped by machine
./hellotune play ip28/boot-1           # THE classic Indigo2 Impact boot chime
./hellotune play ip32 0                # the O2 PROM's ".play 0"
./hellotune play o2 shutdown
./hellotune play random                # like an unset nvram boottune
./hellotune play ip20                  # the Indigo fugue

./hellotune gui                        # full graphical player
./hellotune-gui                        # ...same thing, one keystroke shorter
```

Or install it properly:

```sh
pip install .          # provides `hellotune` and `hellotune-gui`
```

## The CLI

```
hellotune list [--json]                     every machine + tune
hellotune info <selector> [--json]          parameter & note analysis
hellotune play <selector> [options]         synthesize + play
hellotune export <selector> -o out.wav      synthesize + write WAV
hellotune export-all [--dir DIR]            all 30 tunes as WAVs
hellotune boot-sim [-m ip20] [--uptime 3]   power on ... power off
hellotune players                           list playback backends
hellotune gui                               the GUI
```

**Selectors** are forgiving:

| Selector | Resolves to |
|----------|------------|
| `ip28/boot-1` | exact id |
| `boot-1a`, `s5_1` | unique id suffix / PROM name (ambiguous ones tell you) |
| `ip28 boot`, `o2 shutdown` | a machine's default tune of a kind |
| `ip32 5` | O2 `.play #5` |
| `ip28 boot 2` | nvram boottune digit `2` |
| `random` | random pick (PROM-authentic when boottune was unset) |
| *(nothing)* | the default boot chime |

**Rendering options** (apply to `play`, `export`, `export-all`, `boot-sim`):

```
--seed N          fix the noise seed for a reproducible "boot"
--volume 0..100   0 is authentic: the PROM stays silent
--no-normalize    keep raw codec levels (very quiet!)
--peak-db DB      normalization target (default -1.5 dBFS)
--player "CMD {}" custom player command
```

**WAV export options:** `-o file`, `--dir DIR`, `--bits 16|24`,
`--channels 1|2` (default: the machine's native channel layout).

## The GUI

```sh
./hellotune gui
```

* searchable catalog of all 30 tunes with kind filters and colour coding
* live waveform preview, synthesis progress, animated playhead
* volume, normalization, peak-dB, WAV bit-depth and playback-backend
  controls
* seed field with a re-roll button -- every real boot sounded slightly
  different, and yours can too
* Export WAV / Export all dialogs, and a **Boot sim** button that runs the
  power-on chime, an "uptime", and the shutdown chime
* settings are remembered between runs (`~/.config/hellotune/settings.json`)

Keyboard: `Space` play/stop · `Esc` stop · `R` re-roll seed · `E` export ·
`B` boot sim · arrows navigate · `F1` about.

---

## How the chimes actually work

### Karplus-Strong (IP26 / IP28 / IP32)

The PROM's `hello_tune.c` synthesizes each note with the Karplus-Strong
plucked-string algorithm: a delay line filled with noise is fed back
through a one-pole lowpass and an all-pass filter.  Each note is one
`pluckparams_t` row:

```c
{ period, duration, start, amplitude, rho, s, s1, apcoeff }
```

`hello_tune-player` ports the loop verbatim, including:

* the PROM's hand-rolled LCG `state = ((state*1103515245 + 12345) >> 1) - 1`
  with 32-bit wrap-around,
* the `(int)sample << 8` fixed-point truncation into 32-bit words,
* zero-mean, peak-normalized noise excitation,
* the exact filter coefficients of every one of the 30 parameter lists.

The best part: `randfill()` seeds that LCG from an **uninitialized stack
variable** (the source literally comments *"get random value to start with
no init!"*), which is why no two real boots ever sounded identical.  We
honor that: by default every render gets a fresh random seed (`--seed N`
reproduces a specific "boot").  The IP20 fugue is fully deterministic, so
seeds don't apply there.

### The DSP fugue (IP20)

`ref/IP20prom/hello_tune.asm` is a Motorola DSP56001 program (downloaded to
the HAL1's DSP at boot).  It's a four-voice additive synth: a 256-entry sine
wavetable with linear interpolation, four oscillators starting 45° apart,
and chained linear attack/sustain/release envelopes at 48 kHz -- all ported
natively (no DSP emulation).  The boot tune stacks voices like a fugue
(C4+C6, +E4, +G4, then the full C-major chord ringing out for a second);
the shutdown tune runs it backwards; graphics failure plays a *"G Flat"*
F# cluster.

### Notes on faithfulness

* **The O2 plays at 34 kHz, really.** The PROM writes `34000` into the
  AD1843's `CLK_GEN1_RATE` register while a comment claims *"set at 44100
  sample rate"* -- the comment is wrong. The IRIX kernel's own `ad1843.h`
  documents the rate registers as literal Hertz (`LSB==1HZ`, with
  `MIN_SAMPLE_RATE 0x0fa0` = 4000 Hz), and spectral analysis of reference
  captures confirms it: the O2's DAC ran at 34000 Hz. The note tables were
  designed at 44.1 kHz (their periods land within cents of a C-major
  pattern), so every real O2 played its chimes about **4.4 semitones lower
  and 30% slower** than the designer drew them -- and that slightly dark,
  laid-back sound *is* the O2 chime people remember. hello_tune-player
  renders the O2 tunes at 34000 Hz natively, so you hear (and export)
  exactly what the hardware played, pitch and duration included.
* The IP26/IP28 tunes are synthesized at their designed 44100 Hz: the
  hand-written Indigo2 arpeggios are the well-known A-major boot /
  goodbye chimes at concert pitch.
* The IP32 `hello_tune.c` writes two 32-bit words per pluck iteration
  (identical L/R for its stereo codec), while its `buflen` accounting
  stayed in single-word units -- a harmless overrun into scratch memory on
  real hardware.  We render the *intended* tune: one frame per pluck
  iteration, starts in frames, full ring-out (`buflen == last start +
  duration` for every IP32 list, which is how we confirmed this reading).
* The IP26/IP28 lists mostly agree with `buflen == last start + duration`
  but a few are padded allocations; we always render the full musical
  length.
* The PROM's `volume` NVRAM setting of 0 meant "don't play anything at
  all".  Ours does too.

## Legal / credits

The chime data and algorithms originate in SGI's proprietary PROM sources
(`hello_tune.c` / `hello_tune.asm`, "Hollywood Boot Tune").  This project is
a fan-made nostalgia piece for hardware that left the market decades ago.
No SGI code is included here -- only the numeric parameters and a native
reimplementation of the (classic, published) Karplus-Strong algorithm.

Karplus, K. & Strong, A. (1983), "Digital Synthesis of Plucked String and
Drum Timbres", *Computer Music Journal* 7(2).
