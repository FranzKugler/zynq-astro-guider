"""guider_target -- on-board PS app that orchestrates the PL phase-correlation
datapath (guider_hdl.top.PhaseCorrelatorPL) over AXI-DMA.

`estimate_shift_pl(ref, img, backend)` runs the DDR pass schedule; pick a backend:
  ModelBackend  -- golden-model arithmetic, for verification off the board.
  UioBackend    -- real AXI-DMA on the Zynq (needs the integrated bitstream).
"""
from .backend import PLBackend, shift_from_max
from .orchestrator import estimate_shift_pl
from .model_backend import ModelBackend

__version__ = "0.1.0"
__all__ = ["PLBackend", "shift_from_max", "estimate_shift_pl", "ModelBackend"]


def __getattr__(name):
    # UioBackend pulls in /dev/mem + udmabuf mmap glue that only exists on the
    # board; load it lazily so importing guider_target works on the dev host.
    if name == "UioBackend":
        from .uio_backend import UioBackend
        return UioBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
