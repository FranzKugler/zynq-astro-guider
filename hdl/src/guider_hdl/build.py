"""Emit synthesizable Verilog for the PL top, and the FFT-IP generation command.

`PhaseCorrelatorTop` (AXI-Lite CSR + the AXIS datapath) is emitted as Verilog
with the Xilinx FFT IP left as an *undefined black-box instance* `fft_<N>` that
the block design supplies via `ip/gen_fft_ip.tcl`. A thin SystemVerilog wrapper
(bd/, M5 task) adds the Xilinx interface attributes the BD needs to infer the
AXI-Lite / AXIS interfaces from the flat ports emitted here.

Usage:
  python -m guider_hdl.build [outdir] [N]      # default outdir=build/rtl, N=256
"""
from __future__ import annotations

import sys
from pathlib import Path

from amaranth.back import verilog

from .csr import PhaseCorrelatorTop

MODULE_NAME = "phase_correlator_top"


def emit_verilog(n: int = 256, mant_bits: int = 18, phase_width: int = 16,
                 name: str = MODULE_NAME) -> str:
    """Verilog for PhaseCorrelatorTop with the real FftIP black box (`fft_<N>`)."""
    top = PhaseCorrelatorTop(n=n, mant_bits=mant_bits, phase_width=phase_width)
    return verilog.convert(top, name=name)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    outdir = Path(argv[0]) if len(argv) > 0 else Path("build/rtl")
    n = int(argv[1]) if len(argv) > 1 else 256
    mant_bits, phase_width = 18, 16

    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{MODULE_NAME}.v"
    out.write_text(emit_verilog(n=n, mant_bits=mant_bits, phase_width=phase_width))
    print(f"wrote {out} ({out.stat().st_size} bytes); "
          f"black-box instance: fft_{n}")
    print("generate the matching FFT IP XCI with:")
    print(f"  vivado -mode batch -source ip/gen_fft_ip.tcl "
          f"-tclargs {n} {mant_bits} {phase_width} build/fft_ip")


if __name__ == "__main__":
    main()
