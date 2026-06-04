# target — on-board PS guiding app

The PS-side application that orchestrates the PL phase-correlation datapath
(`guider_hdl.top.PhaseCorrelatorPL`) over AXI-DMA. Python package
`guider_target` (src layout, venv in `target/.venv`).

```bash
cd target
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e ../golden_model
pytest
```

## What it does
`estimate_shift_pl(ref, img, backend)` runs the DDR pass schedule and returns
`(dy, dx, peak_value, correlation_surface)` — the same contract and `(dy,dx)`
convention as the golden model. The PS owns input scaling/quantization, the
block-max → BFP-shift handoff between the cross-power and rescale passes, and the
peak argmax + parabolic subpixel; every compute pass goes to the PL via a
`PLBackend`.

## Backend seam
`PLBackend` (backend.py) is one method per PL kernel pass; the orchestrator owns
the *schedule* (passes, order, DDR buffers, transposes), the backend owns *how* a
pass runs:

- **`ModelBackend`** — golden-model arithmetic. `estimate_shift_pl(.., ModelBackend())`
  reproduces `guider_golden.fixed_point.estimate_shift` **bit-exact** (see
  tests), which certifies the sequencing — transposed reads, the sh handoff, the
  buffer flow — independent of the not-yet-built bitstream. This continues the
  project validation chain: orchestration reproduces the fixed-point model.
- **`UioBackend`** — real AXI-DMA on the Zynq (hw_backend.py). Scaffolded: maps
  the schedule onto udmabuf DDR buffers + uio control/status regs + per-endpoint
  DMA, with the register pokes marked TODO. Needs the integrated block design +
  bitstream (the salvaged BOOT.bin carries only the PS bring-up image) and the
  board. Loaded lazily so `import guider_target` works on the dev host.

## Pass schedule
Mirrors `guider_hdl/top.py`: window → FFT2(ref)=F, FFT2(img)=G (each = row pass,
transpose via column-major DMA, column pass) → cross-power R + block max →
rescale/phase-only P (shift from the block max) → IFFT2 → peak. One FFT IP,
time-shared across all FFT/IFFT passes.

## Status
Orchestration + ModelBackend done and cosim'd vs the golden model. Next: build
the integrated bitstream (block design: PhaseCorrelatorPL + AXI-DMA + udmabuf),
then fill in `UioBackend` and validate on hardware against `ModelBackend`.
