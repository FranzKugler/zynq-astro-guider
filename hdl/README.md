# hdl — PL datapath (M4)

Amaranth sources for the phase-correlation datapath in the PL, cosim'd against
the fixed-point golden model.

## FFT realization decision
The FFT/IFFT is the **Xilinx FFT IP** (Vivado), instantiated as a black box for
synthesis. It cannot run in the Amaranth simulator, so:

- Everything *around* the FFT (window, corner-turn, cross-power, phase-only
  CORDIC) is written in Amaranth and cosim'd **bit-exact** against
  `guider_golden.fixed_point` via the Amaranth simulator (pure Python, no Vivado
  in the verify loop).
- In simulation the FFT instance is replaced by a behavioral model (the golden
  model's BFP FFT); the real IP is verified separately in Vivado xsim.
- Cosim of the full chain including the IP is therefore tolerance-based, not
  bit-exact (the IP's internal schedule is not reproduced by the Python model).

`guider_golden.fixed_point` is the spec: `FixedConfig` bit widths map onto the
FFT-IP config (mant_bits -> output width, twiddle_bits -> phase-factor width).
See ../docs/fixed_point.md.

## Quickstart
```bash
cd hdl
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e ../golden_model    # amaranth + cosim reference
pytest -q
```

## Datapath blocks (roadmap)
Each block gets a bit-exact pysim cosim against the matching model stage.

- [x] `window.py`       WindowMul — Hann multiply + convergent rounding
- [x] `cross_power.py`  conj(F)*G complex multiply + BFP rescale
      (CrossMul, ShiftFromMax, BfpRescale; block-max reducer deferred to stream)
- [x] `phase_only.py`   R/|R| via two-pass CORDIC (vectoring + rotating)
      cordic_ref.py = bit-accurate Python spec; HW bit-exact to it, it ~= model
- [x] `cross_power.py:BlockMax`  streaming block-max (BFP pass 1, feeds ShiftFromMax)
- [ ] corner-turn buffer         (row/col transpose between FFT passes, BRAM)
- [ ] FFT-IP wrapper             (AXI-Stream, BFP exponent handling) + xsim
- [ ] top-level stream assembly  + control/peak readout to PS

## Conventions
- `fixed.py` holds primitives bit-matched to the model (`round_shift_expr` =
  the model's convergent `_round_shift`). Change them only in lockstep.
- Signed datapath words; BFP block exponent carried alongside the stream.
