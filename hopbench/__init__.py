"""HopBench: grade a frequency-hop schedule against a fixed suite of simulated attackers.

Simulation / education only. Nothing here touches RF. All schedules and attackers are
generic schemes implemented in this package.
"""
__version__ = "1.0.0"
from .schedules import Schedule, BUILTIN_SCHEDULES, CONTROL_SCHEDULES  # noqa: F401
