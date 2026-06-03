"""Amaranth PL datapath for the phase-correlation guider.

The FFT/IFFT is the Xilinx FFT IP (black box, instantiated for synthesis). All
the surrounding logic -- windowing, corner-turn, cross-power, phase-only
normalization -- lives here and is cosim'd bit-exact against the fixed-point
golden model `guider_golden.fixed_point` using the Amaranth simulator.
"""
__version__ = "0.1.0"
