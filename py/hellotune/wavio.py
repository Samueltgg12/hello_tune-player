"""
WAV writing for hello_tune-player.

A tiny RIFF writer supporting the two formats worth shipping chimes in:

  * 16-bit PCM  -- the classic; what the PROMs' codecs consumed
  * 24-bit PCM  -- a little extra headroom for the Karplus-Strong tails

Both are standard little-endian WAVE_FORMAT_PCM files, mono or identical
stereo (the PROMs duplicated the mono mix to both channels).
"""

from __future__ import annotations

import struct
from typing import List, Sequence

MIN16 = -32768
MAX16 = 32767
MIN24 = -8388608
MAX24 = 8388607


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _frames_from_mono(mono: Sequence[float], channels: int) -> List[float]:
    """Expand mono floats to interleaved channel samples."""
    if channels == 1:
        return list(mono)
    if channels == 2:
        out: List[float] = []
        for v in mono:
            out.append(v)
            out.append(v)
        return out
    raise ValueError(f"unsupported channel count {channels}")


def wav_bytes(mono: Sequence[float], rate: int, channels: int = 1,
              bits: int = 16) -> bytes:
    """Serialize float mono samples ([-1, 1)) to a complete WAV file."""
    if bits not in (16, 24):
        raise ValueError("bits must be 16 or 24")
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")

    inter = _frames_from_mono(mono, channels)
    n_samples = len(inter)

    if bits == 16:
        # pack in blocks for speed
        data = bytearray()
        pack = struct.Struct("<h").pack
        for v in inter:
            data += pack(_clamp(int(round(v * 32767.0)), MIN16, MAX16))
    else:
        data = bytearray()
        for v in inter:
            iv = _clamp(int(round(v * 8388607.0)), MIN24, MAX24)
            # little-endian 24-bit two's complement
            data += (iv & 0xFFFFFF).to_bytes(3, "little")

    block_align = channels * (bits // 8)
    data_bytes = len(data)
    # RIFF header: 4 ("WAVE") + 8+16 (fmt) + 8+data
    riff_size = 4 + 8 + 16 + 8 + data_bytes
    riff_size += riff_size & 1  # pads

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", riff_size, b"WAVE",
        b"fmt ", 16,
        1,                      # WAVE_FORMAT_PCM
        channels, rate,
        rate * block_align,     # byte rate
        block_align, bits,
        b"data", data_bytes,
    )
    return bytes(header) + bytes(data)


def write_wav(path: str, mono: Sequence[float], rate: int, channels: int = 1,
              bits: int = 16) -> int:
    """Write a WAV file; returns number of bytes written."""
    blob = wav_bytes(mono, rate, channels, bits)
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)


def wav_bytes_from_int16(samples: Sequence[int], rate: int,
                         channels: int = 1) -> bytes:
    """Serialize ready 16-bit samples (interleaved if stereo) to WAV."""
    if channels == 2 and len(samples) % 2:
        raise ValueError("stereo sample list must have even length")
    data = struct.pack(f"<{len(samples)}h", *samples)
    block_align = channels * 2
    riff_size = 4 + 8 + 16 + 8 + len(data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", riff_size, b"WAVE",
        b"fmt ", 16,
        1, channels, rate, rate * block_align, block_align, 16,
        b"data", len(data),
    )
    return header + data


def read_wav_info(path: str) -> dict:
    """Minimal WAV header parse-back (used by tests and `--verify`)."""
    with open(path, "rb") as f:
        blob = f.read(64)
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    off = 12
    info: dict = {}
    while off < len(blob) - 8:
        cid = blob[off:off + 4]
        (size,) = struct.unpack("<I", blob[off + 4:off + 8])
        if cid == b"fmt ":
            (fmt, ch, rate, _br, ba, bits) = struct.unpack(
                "<HHIIHH", blob[off + 8:off + 8 + 16])
            info.update(format=fmt, channels=ch, rate=rate,
                        block_align=ba, bits=bits)
        if cid == b"data":
            info["data_bytes"] = size
            break
        off += 8 + size + (size & 1)
    return info
