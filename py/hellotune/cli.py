"""
hello_tune-player command line interface.

Examples:
    hellotune list
    hellotune play ip28/boot-1            # the classic Indigo2 arpeggio
    hellotune play ip32 0                 # O2 PROM ".play 0"
    hellotune play o2 shutdown
    hellotune play random                 # like an unset nvram boottune
    hellotune play boot --seed 42         # reproducible "boot"
    hellotune export ip32/boot --stereo -o o2.wav
    hellotune export-all --dir chimes/
    hellotune boot-sim -m ip20            # power on ... power off
    hellotune info ip26/badgfx
    hellotune gui
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import List, Optional, Sequence

from . import __version__
from .players import (Playback, PlaybackError, available_backends,
                      temp_wav_path)
from .render import RenderOptions, analyze_tune, format_analysis, render_tune
from .tunes import (KIND_LABELS, MACHINES, TUNES, Tune, TuneNotFound,
                    default_tune, find_tune, tunes_for)
from .wavio import write_wav

PROG = "hellotune"

DEFAULT_MACHINE = "ip32"  # when nothing else is implied


# ---------------------------------------------------------------------------
# selector resolution
# ---------------------------------------------------------------------------

_KINDS = ("boot", "shutdown", "badgfx", "variant")


def resolve_tune(selector: Optional[str], machine: Optional[str],
                 kind: Optional[str]) -> Tune:
    """Resolve a CLI selector, including 'random'."""
    machine = machine or DEFAULT_MACHINE
    if selector and selector.strip().lower() == "random":
        pool = tunes_for(machine, kind) if kind else tunes_for(machine)
        if not pool:
            raise TuneNotFound(
                f"machine {machine!r} has no tunes"
                + (f" of kind {kind!r}" if kind else ""))
        return random.choice(pool)
    if kind and not selector:
        return default_tune(machine, kind)
    if not selector:
        return default_tune(machine, "boot")
    return find_tune(selector, machine)


# ---------------------------------------------------------------------------
# shared option plumbing
# ---------------------------------------------------------------------------

def add_render_opts(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("rendering")
    g.add_argument("--seed", type=int, default=None, metavar="N",
                   help="fix the noise-seed for a reproducible 'boot' "
                        "(default: random, like the PROM's uninitialized "
                        "stack variable). Ignored for the deterministic "
                        "IP20 tune.")
    g.add_argument("--no-normalize", action="store_true",
                   help="keep raw codec levels (quiet!) instead of "
                        "normalizing to --peak")
    g.add_argument("--peak-db", type=float, default=-1.5, metavar="DB",
                   help="normalization target peak in dBFS (default -1.5)")
    g.add_argument("--volume", type=float, default=100.0, metavar="PCT",
                   help="playback volume 0..100%% (0 = the PROM stays "
                        "silent, authentically)")


def render_options(args) -> RenderOptions:
    peak_db = getattr(args, "peak_db", -1.5)
    return RenderOptions(
        seed=getattr(args, "seed", None),
        normalize=not getattr(args, "no_normalize", False),
        peak=10.0 ** (peak_db / 20.0),
        volume=getattr(args, "volume", 100.0),
    )


def render_or_silence(tune: Tune, opts: RenderOptions, quiet: bool = False):
    """Render, honouring the PROM's volume==0 means silence."""
    if opts.volume <= 0:
        if not quiet:
            print(f"{PROG}: volume is 0 -- the PROM would stay silent. "
                  "Nothing played.")
        return None
    return render_tune(tune, opts)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_list(args) -> int:
    if args.json:
        data = []
        for mid in MACHINES:
            m = MACHINES[mid]
            data.append({
                "machine": mid, "name": m.name, "cpu": m.cpu,
                "audio": m.audio, "rate": m.rate, "engine": m.engine,
                "tunes": [
                    {"id": t.id, "name": t.name, "kind": t.kind,
                     "prom_index": t.prom_index, "nvram_index": t.nvram_index,
                     "seconds": round(t.seconds, 2)}
                    for t in tunes_for(mid)
                ],
            })
        json.dump(data, sys.stdout, indent=2)
        print()
        return 0

    for mid, m in MACHINES.items():
        print(f"{m.name}  [{mid}]  --  {m.cpu}, {m.audio}")
        for t in tunes_for(mid):
            tags = KIND_LABELS[t.kind]
            extra = []
            if t.prom_index is not None:
                extra.append(f".play #{t.prom_index}")
            if t.nvram_index is not None:
                extra.append(f"nvram '{t.nvram_index}'")
            extra_s = f" ({', '.join(extra)})" if extra else ""
            print(f"    {t.id:<16} {t.duration:>8}  {tags}{extra_s}")
        print()
    print("play any tune with:  hellotune play <id>   (see hellotune play --help)")
    return 0


def cmd_info(args) -> int:
    try:
        tune = resolve_tune(args.tune, args.machine, args.kind)
    except (TuneNotFound, KeyError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(analyze_tune(tune), indent=2))
    else:
        print(format_analysis(tune))
    return 0


def _play_result(res, args, backend: Optional[str] = None) -> None:
    """Write a temp WAV and play it, blocking until done (or Ctrl-C)."""
    pb = Playback()
    path = temp_wav_path()
    try:
        write_wav(path, res.mono, res.rate, res.channels, bits=16)
        if getattr(args, "player", None):
            pb.play_custom(args.player, path)
        else:
            pb.play_file(path, backend=backend)
        try:
            while pb.is_playing():
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            pb.stop()
    finally:
        pb.stop()
        try:
            os.unlink(path)
        except OSError:
            pass


def cmd_play(args) -> int:
    try:
        tune = resolve_tune(args.tune, args.machine, args.kind)
    except (TuneNotFound, KeyError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2

    opts = render_options(args)
    try:
        res = render_or_silence(tune, opts)
    except ValueError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    if res is None:
        return 0

    m = MACHINES[tune.machine]
    print(f"Playing {tune.name}  [{tune.id}]  {m.name}")
    if not args.quiet:
        print(f"  {res.seconds:.2f} s, {res.rate} Hz, "
              f"{'stereo' if res.channels == 2 else 'mono'}"
              f", seed {res.seed}"
              + (" (deterministic)" if tune.engine == "additive" else ""))

    loops = max(1, args.loop)
    try:
        for i in range(loops):
            if i > 0:
                if not args.quiet:
                    print(f"  loop {i + 1}/{loops}")
                # fresh randomness per loop unless seeded
                if opts.seed is None and tune.engine == "karplus":
                    res = render_or_silence(tune, opts)
                    if res is None:
                        return 0
            _play_result(res, args)
    except PlaybackError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    return 0


def _export_one(tune: Tune, opts: RenderOptions, path: str, bits: int,
               channels: Optional[int]) -> int:
    if opts.volume <= 0:
        print(f"{PROG}: volume is 0 -- {tune.id} would be silent; skipped.")
        return 0
    res = render_tune(tune, opts)
    ch = channels or res.channels
    n = write_wav(path, res.mono, res.rate, ch, bits=bits)
    print(f"wrote {path}  ({res.seconds:.2f} s, {res.rate} Hz, "
          f"{'stereo' if ch == 2 else 'mono'}, {bits}-bit, "
          f"{n} bytes, seed {res.seed})")
    return 0


def _default_filename(tune: Tune, bits: int) -> str:
    safe = tune.id.replace("/", "-")
    return f"{safe}-{bits}bit.wav"


def cmd_export(args) -> int:
    try:
        tune = resolve_tune(args.tune, args.machine, args.kind)
    except (TuneNotFound, KeyError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2
    path = args.output or _default_filename(tune, args.bits)
    if args.directory:
        os.makedirs(args.directory, exist_ok=True)
        path = os.path.join(args.directory, os.path.basename(path))
    try:
        return _export_one(tune, render_options(args), path, args.bits,
                           args.channels)
    except ValueError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1


def cmd_export_all(args) -> int:
    outdir = args.directory or "."
    os.makedirs(outdir, exist_ok=True)
    pool = [t for t in TUNES if not args.machine or t.machine == args.machine]
    rc = 0
    for i, tune in enumerate(pool, 1):
        print(f"[{i}/{len(pool)}] {tune.id}")
        try:
            rc |= _export_one(tune, render_options(args),
                              os.path.join(outdir, _default_filename(tune, args.bits)),
                              args.bits, args.channels)
        except ValueError as e:
            print(f"{PROG}: {e}", file=sys.stderr)
            rc = 1
    print(f"exported {len(pool)} tunes to {outdir}")
    return rc


def cmd_boot_sim(args) -> int:
    machine = args.machine or DEFAULT_MACHINE
    m = MACHINES[machine]
    opts = render_options(args)

    try:
        boot = default_tune(machine, "boot")
        stop = default_tune(machine, "shutdown")
    except KeyError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2

    print(f"{m.name} boot/shutdown simulation "
          f"(uptime {args.uptime:.0f} s, {args.loops} cycle(s))")
    for cycle in range(1, args.loops + 1):
        if args.loops > 1:
            print(f"-- cycle {cycle}/{args.loops} --")
        try:
            print("powering on ...")
            res = render_or_silence(boot, opts)
            if res:
                print(f"  hello : {boot.name} [{boot.id}] "
                      f"(seed {res.seed})")
                _play_result(res, args)
            time.sleep(args.uptime)
            print("IRIX boots, you log in, you get some work done ...")
            time.sleep(args.uptime)
            print("powering off ...")
            res = render_or_silence(stop, opts)
            if res:
                print(f"  goodbye: {stop.name} [{stop.id}]")
                _play_result(res, args)
            print("halted.")
            if cycle < args.loops:
                time.sleep(1.5)
        except KeyboardInterrupt:
            print("\naborted.")
            return 0
        except PlaybackError as e:
            print(f"{PROG}: {e}", file=sys.stderr)
            return 1
    return 0


def cmd_players(args) -> int:
    avail = available_backends()
    if not avail or avail == ["custom"]:
        print("No playback backends found. Install one of: pw-play, "
              "paplay, aplay, ffplay, play (SoX), cvlc -- or use "
              "--player 'your-player {}' to point at any command.")
        return 1
    print("Available playback backends (best first):")
    for name in avail:
        print(f"  {name}")
    print("Use --player 'CMD {}' to force a specific command.")
    return 0


def cmd_gui(args) -> int:
    from .gui import run_gui
    return run_gui()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="hello_tune-player -- natively resynthesize, play and "
                    "export the boot/shutdown chimes of SGI PROMs "
                    "(Indigo R4000, Power Indigo2, Indigo2 Impact, O2).",
        epilog="Selectors: a tune id ('ip28/boot-1'), a machine + kind "
               "('o2 shutdown'), a PROM number ('ip32 5'), 'random', or "
               "nothing for the default boot chime.  Run 'hellotune list' "
               "to see everything.")
    ap.add_argument("--version", action="version",
                    version=f"{PROG} {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    def common(p, kinds=True, machines=True):
        p.add_argument("tune", nargs="?", default=None,
                       help="tune selector (id, machine+kind, PROM number, "
                            "'random')")
        if machines:
            p.add_argument("-m", "--machine", default=None,
                           choices=list(MACHINES),
                           help="machine context (default: "
                                f"{DEFAULT_MACHINE})")
        if kinds:
            p.add_argument("--kind", default=None, choices=list(_KINDS),
                           help="restrict to boot/shutdown/badgfx/variant")

    # list
    p = sub.add_parser("list", help="list machines and tunes")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_list)

    # info
    p = sub.add_parser("info", help="show tune parameters and note analysis")
    common(p)
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_info)

    # play
    p = sub.add_parser("play", help="synthesize and play a chime")
    common(p)
    add_render_opts(p)
    p.add_argument("--player", default=None, metavar="CMD",
                   help="custom player command; '{}' is the wav file")
    p.add_argument("--loop", type=int, default=1, metavar="N",
                   help="play N times (default 1)")
    p.add_argument("--quiet", action="store_true",
                   help="only print the header line")
    p.set_defaults(func=cmd_play)

    # export
    p = sub.add_parser("export", help="synthesize and write a WAV file")
    common(p)
    add_render_opts(p)
    p.add_argument("-o", "--output", default=None, metavar="PATH",
                   help="output file (default: <machine>-<tune>-<bits>bit.wav)")
    p.add_argument("--directory", default=None, metavar="DIR",
                   help="put the file in this directory")
    p.add_argument("--bits", type=int, default=16, choices=(16, 24),
                   help="sample width (default 16)")
    p.add_argument("--channels", default=None, type=int, choices=(1, 2),
                   help="override channel count (default: native)")
    p.set_defaults(func=cmd_export)

    # export-all
    p = sub.add_parser("export-all",
                       help="export every tune (optionally one machine)")
    add_render_opts(p)
    p.add_argument("-m", "--machine", default=None, choices=list(MACHINES))
    p.add_argument("--directory", default=None, metavar="DIR",
                   help="output directory (default: current)")
    p.add_argument("--bits", type=int, default=16, choices=(16, 24))
    p.add_argument("--channels", default=None, type=int, choices=(1, 2))
    p.set_defaults(func=cmd_export_all)

    # boot-sim
    p = sub.add_parser("boot-sim",
                       help="full boot -> uptime -> shutdown cycle")
    p.add_argument("-m", "--machine", default=None, choices=list(MACHINES))
    add_render_opts(p)
    p.add_argument("--player", default=None, metavar="CMD")
    p.add_argument("--uptime", type=float, default=3.0, metavar="SECS",
                   help="seconds between chime stages (default 3)")
    p.add_argument("--loops", type=int, default=1, metavar="N",
                   help="number of power cycles (default 1)")
    p.set_defaults(func=cmd_boot_sim)

    # players
    p = sub.add_parser("players", help="list available playback backends")
    p.set_defaults(func=cmd_players)

    # gui
    p = sub.add_parser("gui", help="launch the tkinter GUI")
    p.set_defaults(func=cmd_gui)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        ap.print_help()
        print("\nTry:  hellotune play ip28/boot-1    "
              "(the classic Indigo2 boot chime)")
        print("      hellotune gui                 "
              "(full graphical player)")
        return 0

    try:
        return args.func(args)
    except TuneNotFound as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2
    except PlaybackError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
