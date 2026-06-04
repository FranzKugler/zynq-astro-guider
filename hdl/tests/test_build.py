"""Guard: PhaseCorrelatorTop stays Verilog-emittable, with the FFT as a black box.

Needs the Verilog backend (amaranth-yosys); skipped if it is unavailable.
"""
import pytest


def _emit(n):
    try:
        from guider_hdl.build import emit_verilog
    except Exception as e:                       # pragma: no cover
        pytest.skip(f"verilog backend unavailable: {e}")
    return emit_verilog(n=n, name="phase_correlator_top")


def test_emits_top_with_fft_blackbox():
    v = _emit(n=8)
    assert "module phase_correlator_top(" in v
    # the AXI-Lite slave + a couple of AXIS endpoints are present as ports
    assert "s_axil__awaddr" in v and "s_axil__rdata" in v
    assert "fft_in__valid" in v and "xpower_r__payload" in v
    # the Xilinx FFT IP is instantiated but NOT defined -> a black box
    assert "fft_8 " in v
    assert "module fft_8" not in v
