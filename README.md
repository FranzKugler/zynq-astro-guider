# zynq-astro-guider

Custom astrophotography guiding system on a Zynq-7020 (MicroPhase Z7-Lite):
whole-field phase-only cross-correlation for mount-error estimation, FFT in
the PL, closed-loop guiding, with an experimental PSF-from-guide-frames idea.

## Layout
- `golden_model/` — numpy/scipy reference pipeline (this is the bit-exact
  reference for the later fixed-point model and the FPGA FFT).
- `hdl/` — Amaranth sources + cosim against the golden model.
- `boot/` — device trees, SD build, U-Boot for the Z7-Lite Debian.
- `target/` — on-board Python guiding application.
- `hardware/` — KiCad MIPI camera-adapter board.
- `docs/` — architecture and decisions.

## Golden model — quickstart
```bash
cd golden_model
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```
