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
- [x] `corner_turn.py`  row/col transpose between FFT passes (ping-pong BRAM)
- [x] `cosim.py`        end-to-end integration: all blocks chained through pysim
      with the model FFT substituted; estimate_shift_hw matches the model and
      recovers shifts (HW vs model shift delta ~0)
- [x] FFT-IP xsim cosim   sim/fft_cosim.py drives the generated IP in Vivado xsim
      and matches a numpy DFT to ~1e-5 (up to the core's fixed common frame
      rotation, which cancels in the cross-power -- see the module docstring)
- [~] FFT-IP wrapper SKELETON    ip/gen_fft_ip.tcl + fft_ip.py (Instance, AXI-S,
      byte-aligned payload packing); config framing / tlast / blk_exp routing
      into the synthesizable top still TODO
- [x] top-level stream assembly  (synthesizable, DDR-streaming):
      - [x] `stream.py`       AXI-Stream interface (valid/ready/first/last/payload)
      - [x] `phase_stage.py`  CrossPower (pass 1: conj(F)*G + streaming block-max)
            and RescalePhase (pass 2: BFP rescale + phase-only); pysim cosim'd
            bit-exact (CORDIC tolerance) vs the model -- test_phase_stage.py
      - [x] `window.py:WindowStream`  Hann window kernel (sample x coef DMA
            streams -> WindowMul); cosim'd vs the model -- test_window.py
      - [x] `fft_pass.py:FftPass`  one 1-D FFT pass: config/tlast/output reframe/
            block-exp around FftIP; `fft_ip.py:FftStub` behavioral drop-in lets
            the framing be pysim'd -- test_fft_pass.py (transform itself: xsim)
      - [x] `top.py:PhaseCorrelatorPL`  instantiates the kernels + the shared FFT
            pass, exposes their AXIS endpoints + control/status as the PL's
            external contract; integration-cosim'd through the top -- test_top.py
      - [x] `csr.py:PhaseCorrelatorTop`  AXI-Lite CSR wrapper (one IP = AXI-Lite
            control/status + AXIS data); register map = the UioBackend contract,
            cosim'd in test_csr.py
- [x] PS orchestration (target/): estimate_shift_pl + ModelBackend, bit-exact vs
      the model (guider_target); UioBackend awaits the bitstream
- [~] M5 bitstream integration -- see ../docs/bitstream_integration.md
      - [x] `build.py`  emit Verilog for PhaseCorrelatorTop (FFT = black box
            fft_<N>): `python -m guider_hdl.build [outdir] [N]` (needs amaranth-yosys)

## Top-level (DDR-streaming)
Whole-field FFT frames do not fit in the XC7Z020's ~4.9 Mbit of BRAM (one N=256
corner-turn buffer alone is ~4.7 Mbit), so frames live in **PS DDR3** and the PL
is a set of AXI-Stream compute kernels driven by the PS via AXI-DMA. The on-chip
`corner_turn.py` ping-pong is kept for small-N/BRAM experiments; at whole-field
sizes the inter-pass transpose is a DMA addressing pattern (column-major read),
not an on-chip buffer.

PS-orchestrated pass schedule (each pass = one DMA in, compute, DMA out):
```
  ref:  window -> FFT2(rows) -> [transpose via DMA] -> FFT2(cols) -> F  (DDR)
  img:  window -> FFT2(rows) -> [transpose via DMA] -> FFT2(cols) -> G  (DDR)
  xpow: F,G -> CrossPower  -> R (DDR) + block max ---.   (pass 1, global BFP)
  norm: R,sh -> RescalePhase -> P (DDR)              `-> sh = ShiftFromMax(max)
  corr: P -> IFFT2(rows) -> [transpose] -> IFFT2(cols) -> corr (DDR)
  peak: PS reads corr, argmax + parabolic subpixel (zero FPGA cost)
```
A single FFT IP is time-shared across all FFT/IFFT passes. Verified in pysim with
the FFT substituted by the behavioral model (the IP transform itself is xsim-
verified, see below); only the streaming/framing glue is exercised in pysim.

## FFT IP (Vivado)
Generate the FFT IP (config derived from the fixed-point model: BFP, convergent
rounding, input_width=mant_bits, phase_factor_width=twiddle_bits, natural order):
```bash
vivado -mode batch -source ip/gen_fft_ip.tcl -tclargs 256 18 16 build/fft_ip
```
`fft_ip.py` instantiates it as a black box. The IP is not simulatable in pysim;
verify it in Vivado xsim against the model (tolerance), and keep using the model
FFT stub (guider_hdl.cosim) for fast pysim integration of the surrounding logic.

Run the xsim cosim (generates the IP if missing, drives it, checks vs numpy DFT):
```bash
python sim/fft_cosim.py 16        # VIVADO=/path/to/vivado if not the default
```
It passes when both the magnitude spectrum and the best-fit (over cyclic frame
rotation) DFT error are within tolerance. The streaming core frames at a fixed
phase offset from the cold-start testbench (captured frame == DFT of a +1 cyclic
shift); that common rotation cancels in the cross-power, so it is harmless --
hence the rotation-tolerant check. See sim/fft_cosim.py for the full reasoning.

## Conventions
- `fixed.py` holds primitives bit-matched to the model (`round_shift_expr` =
  the model's convergent `_round_shift`). Change them only in lockstep.
- Signed datapath words; BFP block exponent carried alongside the stream.
