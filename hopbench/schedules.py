"""Hop schedules: the pluggable interface and the seven built-in generic schemes.

Interface (the only thing HopBench needs from your schedule)::

    class MySchedule(Schedule):
        def __init__(self, M=13, seed=0): ...
        def next_channel(self, slot: int) -> int:   # channel in 0..M-1 for that slot
            ...

``seed`` stands for the *secret* of one deployment (a key, a seed, an initial state).
The benchmark builds many independent instances with different seeds and grades the
population, because a schedule is only as good as the attacker's uncertainty about it.

Threat model used by the suite (Kerckhoffs): the attacker knows the algorithm and
parameters, sees every past hop perfectly, and does NOT know the seed / key.

Credit: the LFSR-16 tap set and the HMAC counter-mode construction follow the earlier
FHSS-Link sim/schedule.py (read only, not imported). The mapping to channels differs
(see each class) and everything else here is new.
"""
from __future__ import annotations

import hashlib
import hmac
import random
import struct

import numpy as np

M64 = (1 << 64) - 1


def derive(seed, label: str, nbytes: int = 32) -> bytes:
    """Deterministically expand (seed, label) into bytes. Used to turn a small integer
    trial seed into per-scheme secrets. Not part of any scheme's strength."""
    out, ctr = b"", 0
    while len(out) < nbytes:
        out += hashlib.sha256(f"hopbench|{label}|{seed}|{ctr}".encode()).digest()
        ctr += 1
    return out[:nbytes]


# ----------------------------------------------------------------------------- interface
class Schedule:
    """Base class. Subclasses implement next_channel(slot)."""

    name = "base"
    description = ""

    def __init__(self, M: int = 13, seed=0):
        self.M = int(M)
        self.seed = seed

    def next_channel(self, slot: int) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def hops(self, n: int, start: int = 0) -> np.ndarray:
        """Convenience: n consecutive hops starting at slot `start`."""
        return np.fromiter((self.next_channel(start + i) for i in range(n)), dtype=np.int64, count=n)


class StreamSchedule(Schedule):
    """State-machine schedules (LFSR, LCG, xorshift). Random access by lazy caching.

    Convention shared with the attackers and the JavaScript game:
        x_0 = initial state (the secret), hop_t = out(x_t), x_{t+1} = step(x_t).
    """

    def _init_state(self, seed) -> int:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def step(x: int) -> int:  # pragma: no cover
        raise NotImplementedError

    def out(self, x: int) -> int:  # pragma: no cover
        raise NotImplementedError

    def __init__(self, M: int = 13, seed=0):
        super().__init__(M, seed)
        self._x = self._init_state(seed)
        self._cache: list[int] = []

    def next_channel(self, slot: int) -> int:
        if slot < 0:
            raise ValueError("slot must be >= 0")
        while len(self._cache) <= slot:
            self._cache.append(self.out(self._x))
            self._x = self.step(self._x)
        return self._cache[slot]


# ----------------------------------------------------------------------------- 1. LFSR-16
def lfsr16_step(s: int) -> int:
    bit = ((s >> 0) ^ (s >> 2) ^ (s >> 3) ^ (s >> 5)) & 1  # taps 16,14,13,11 (maximal length)
    return (s >> 1) | (bit << 15)


class LFSR16(StreamSchedule):
    """16-bit Fibonacci LFSR (period 65535); hop = state mod M. Secret = 16-bit state."""
    name = "LFSR-16"
    description = "16-bit maximal LFSR, hop = state mod M"
    step = staticmethod(lfsr16_step)

    def _init_state(self, seed):
        return int.from_bytes(derive(seed, "lfsr16", 2), "big") % 65535 + 1

    def out(self, x):
        return x % self.M


# ----------------------------------------------------------------------------- 2. LCG
LCG_A, LCG_C, LCG_MASK = 1140671485 & 0xFFFFFF, 12820163, 0xFFFFFF


def lcg24_step(x: int) -> int:
    return (LCG_A * x + LCG_C) & LCG_MASK


class LCG24(StreamSchedule):
    """24-bit linear congruential generator (full period 2^24); hop = (x >> 8) mod M,
    i.e. the high 16 bits are used (the usual advice) but the state is only 24 bits."""
    name = "LCG-24"
    description = "24-bit LCG, hop = (x>>8) mod M"
    step = staticmethod(lcg24_step)

    def _init_state(self, seed):
        return int.from_bytes(derive(seed, "lcg24", 3), "big")

    def out(self, x):
        return (x >> 8) % self.M


# ----------------------------------------------------------------------------- 3. xorshift
def xorshift16_step(x: int) -> int:
    x ^= (x << 7) & 0xFFFF
    x ^= x >> 9
    x ^= (x << 8) & 0xFFFF
    return x


class Xorshift16(StreamSchedule):
    """16-bit xorshift (shifts 7, 9, 8; period 65535), hop = state mod M. A small-state
    variant, as one would pick on a tiny MCU. GF(2)-linear in the state, but the mod-M
    channel map is not linear."""
    name = "Xorshift-16"
    description = "16-bit xorshift (7,9,8), hop = state mod M"
    step = staticmethod(xorshift16_step)

    def _init_state(self, seed):
        return int.from_bytes(derive(seed, "xs16", 2), "big") % 65535 + 1

    def out(self, x):
        return x % self.M


# ----------------------------------------------------------------------------- 4. counter-mod
class CounterMod(Schedule):
    """hop = (a*slot + b) mod M with secret a in 1..M-1, b in 0..M-1 (M prime -> a full cycle).
    The classic 'increment by a constant' schedule."""
    name = "Counter-mod"
    description = "(a*slot + b) mod M, secret (a, b)"

    def __init__(self, M=13, seed=0):
        super().__init__(M, seed)
        d = derive(seed, "ctr", 4)
        self.a = int.from_bytes(d[:2], "big") % (M - 1) + 1
        self.b = int.from_bytes(d[2:], "big") % M

    def next_channel(self, slot):
        return (self.a * slot + self.b) % self.M


# ----------------------------------------------------------------------------- 5. table
class TableSchedule(Schedule):
    """A secret table of T=32 channels, replayed cyclically (e.g. a table burnt into flash)."""
    name = "Table-32"
    description = "secret 32-entry table repeated"
    T = 32

    def __init__(self, M=13, seed=0):
        super().__init__(M, seed)
        rng = random.Random(derive(seed, "table", 16))
        self.table = [rng.randrange(M) for _ in range(self.T)]

    def next_channel(self, slot):
        return self.table[slot % self.T]


# ----------------------------------------------------------------------------- 6. HMAC
class HmacSha256(Schedule):
    """hop_t = int(HMAC-SHA256(key, t)[:8]) mod M. 256-bit key, random access, no state.
    Modulo bias <= M/2^64 (negligible)."""
    name = "HMAC-SHA256"
    description = "HMAC-SHA256(key, slot) -> channel"

    def __init__(self, M=13, seed=0):
        super().__init__(M, seed)
        self.key = derive(seed, "hmac", 32)

    def next_channel(self, slot):
        d = hmac.new(self.key, int(slot).to_bytes(8, "big"), hashlib.sha256).digest()
        return int.from_bytes(d[:8], "big") % self.M


# ----------------------------------------------------------------------------- 7. SipHash
def _rotl(x, b):
    return ((x << b) | (x >> (64 - b))) & M64


def siphash24(key16: bytes, msg: bytes) -> int:
    """SipHash-2-4 (Aumasson/Bernstein), pure Python, 64-bit output."""
    k0, k1 = struct.unpack("<QQ", key16)
    v0 = k0 ^ 0x736F6D6570736575
    v1 = k1 ^ 0x646F72616E646F6D
    v2 = k0 ^ 0x6C7967656E657261
    v3 = k1 ^ 0x7465646279746573

    def rnd(v0, v1, v2, v3):
        v0 = (v0 + v1) & M64; v1 = _rotl(v1, 13); v1 ^= v0; v0 = _rotl(v0, 32)
        v2 = (v2 + v3) & M64; v3 = _rotl(v3, 16); v3 ^= v2
        v0 = (v0 + v3) & M64; v3 = _rotl(v3, 21); v3 ^= v0
        v2 = (v2 + v1) & M64; v1 = _rotl(v1, 17); v1 ^= v2; v2 = _rotl(v2, 32)
        return v0, v1, v2, v3

    n = len(msg)
    for i in range(0, n - n % 8, 8):
        m = struct.unpack("<Q", msg[i:i + 8])[0]
        v3 ^= m
        v0, v1, v2, v3 = rnd(v0, v1, v2, v3)
        v0, v1, v2, v3 = rnd(v0, v1, v2, v3)
        v0 ^= m
    last = (n & 0xFF) << 56
    tail = msg[n - n % 8:]
    for i, byte in enumerate(tail):
        last |= byte << (8 * i)
    v3 ^= last
    v0, v1, v2, v3 = rnd(v0, v1, v2, v3)
    v0, v1, v2, v3 = rnd(v0, v1, v2, v3)
    v0 ^= last
    v2 ^= 0xFF
    for _ in range(4):
        v0, v1, v2, v3 = rnd(v0, v1, v2, v3)
    return v0 ^ v1 ^ v2 ^ v3


class SipHash24(Schedule):
    """hop_t = SipHash-2-4(128-bit key, t as 8 bytes LE) mod M. Keyed short-input PRF."""
    name = "SipHash-2-4"
    description = "SipHash-2-4(key, slot) -> channel"

    def __init__(self, M=13, seed=0):
        super().__init__(M, seed)
        self.key = derive(seed, "sip", 16)

    def next_channel(self, slot):
        return siphash24(self.key, struct.pack("<Q", slot)) % self.M


# ----------------------------------------------------------------------------- controls
class ConstantChannel(Schedule):
    """Control: never hops (worst case, should score ~0)."""
    name = "Control: constant"
    description = "always the same channel (floor)"

    def __init__(self, M=13, seed=0):
        super().__init__(M, seed)
        self.c = int.from_bytes(derive(seed, "const", 2), "big") % M

    def next_channel(self, slot):
        return self.c


class IdealRandom(Schedule):
    """Control: i.i.d. uniform hops from numpy PCG64 (the 'ideal' the suite can measure;
    not a security claim, PCG64 itself is not a cryptographic generator)."""
    name = "Control: ideal-random"
    description = "i.i.d. uniform (PCG64), reference for the ceiling"

    def __init__(self, M=13, seed=0):
        super().__init__(M, seed)
        self._rng = np.random.default_rng(int.from_bytes(derive(seed, "ideal", 8), "big"))
        self._cache: list[int] = []

    def next_channel(self, slot):
        while len(self._cache) <= slot:
            self._cache.append(int(self._rng.integers(self.M)))
        return self._cache[slot]


BUILTIN_SCHEDULES = {c.name: c for c in (LFSR16, LCG24, Xorshift16, CounterMod, TableSchedule, HmacSha256, SipHash24)}
CONTROL_SCHEDULES = {c.name: c for c in (ConstantChannel, IdealRandom)}
