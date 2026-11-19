"""Example of plugging your own schedule into HopBench.

    python -m hopbench score examples.custom_schedule:HashOfCounter --quick

(run from the repository root). The constructor receives (M, seed): `seed` is the secret of one
deployment, and HopBench builds many instances with different seeds.
"""
import hashlib

from hopbench import Schedule


class HashOfCounter(Schedule):
    """Toy 'keyed' schedule: first byte pair of SHA-256(seed || slot), mod M. Good enough to show the API."""
    name = "Example: SHA256-counter"
    description = "sha256(seed|slot) mod M"

    def next_channel(self, slot):
        d = hashlib.sha256(f"{self.seed}|{slot}".encode()).digest()
        return int.from_bytes(d[:8], "big") % self.M


class WeakCounter(Schedule):
    """Deliberately weak: a fixed stride. HopBench should flag it."""
    name = "Example: stride-5"
    description = "5*slot + seed mod M"

    def next_channel(self, slot):
        return (5 * slot + int(self.seed)) % self.M
