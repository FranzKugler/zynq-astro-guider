"""Hardware-abstraction seam between the PS orchestrator and the PL datapath.

`PLBackend` is the set of operations the orchestrator issues against the PL
phase-correlation datapath (guider_hdl.top.PhaseCorrelatorPL). Each method maps
to one DMA-driven pass over the kernels; the orchestrator owns the *schedule*
(which passes, in what order, with which DDR buffers and transposes), the backend
owns *how* a pass runs (model arithmetic, or real AXI-DMA on the board).

The granularity is one 1-D FFT pass (`fft_pass`), so the orchestrator performs
the 2-D transform itself as pass -> transpose -> pass, making the transposed DMA
read explicit (and testable). Frames are passed as (re, im) int arrays standing
in for the DDR buffers; a real backend DMAs them to/from PS DDR3.
"""
from __future__ import annotations
from abc import ABC, abstractmethod

import numpy as np

from guider_golden.fixed_point import FixedConfig


def shift_from_max(mx: int, cfg: FixedConfig) -> int:
    """BFP shift for a block whose max |component| is `mx` (== model _bfp_rescale).

    Mirrors guider_hdl.cross_power.ShiftFromMax: keep the largest magnitude inside
    `mant_bits`. This is the value the PS programs into rescale_sh between the
    cross-power and rescale/phase-only DMA passes.
    """
    limit = (1 << (cfg.mant_bits - 1)) - 1
    return 0 if mx <= limit else int(mx).bit_length() - (cfg.mant_bits - 1)


class PLBackend(ABC):
    """One method per PL kernel pass; see guider_hdl.top.PhaseCorrelatorPL."""

    cfg: FixedConfig

    @abstractmethod
    def window(self, samples: np.ndarray, coefs: np.ndarray) -> np.ndarray:
        """Hann window: elementwise samples * coefs >> window_bits (WindowStream)."""

    @abstractmethod
    def fft_pass(self, re: np.ndarray, im: np.ndarray,
                 inverse: bool) -> tuple[np.ndarray, np.ndarray]:
        """One 1-D BFP FFT along axis 1 (a batch of N rows) -- FftPass."""

    @abstractmethod
    def cross_power(self, f_re: np.ndarray, f_im: np.ndarray,
                    g_re: np.ndarray, g_im: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray, int]:
        """conj(F)*G (exact) + block max over the frame -- CrossPower (pass 1)."""

    @abstractmethod
    def rescale_phase(self, r_re: np.ndarray, r_im: np.ndarray,
                      sh: int) -> tuple[np.ndarray, np.ndarray]:
        """BFP-rescale R by `sh`, then phase-only normalize -- RescalePhase (pass 2)."""
