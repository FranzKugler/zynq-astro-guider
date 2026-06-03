# CLAUDE.md — zynq-astro-guider

Project context for Claude Code. Read this first.

## Goal
Custom astrophotography guiding camera on a Zynq-7020 (MicroPhase Z7-Lite).
Whole-field phase-only cross-correlation for mount-error estimation:
window -> FFT2 -> cross-power conj(F_ref)*F_img -> phase-only normalize ->
IFFT2 -> peak -> subpixel -> (dy,dx) drift. FFT runs in the PL. Experimental:
reconstruct the residual-tracking PSF from fast guide frames for deconvolution.

Stack: Amaranth HDL + Vivado 2025.2 + ikwzm Debian 12 (kernel 6.1.108-armv7-fpga).

## Repo layout
- golden_model/  numpy/scipy reference pipeline = bit-exact reference for the HDL.
  Python package `guider_golden` (src layout). venv in golden_model/.venv.
  `pip install -e ".[dev]"`, then `pytest`.
- hdl/      Amaranth + Xilinx FFT IP, cosim vs golden model (M4, not started)
- boot/     device trees, SD build, U-Boot for the Z7-Lite
- target/   on-board Python guiding app (later)
- hardware/ KiCad MIPI adapter
- docs/     architecture + decisions

## Conventions (do not break)
- estimate_shift(ref,img) -> (dy,dx,peak,corr); (dy,dx) = displacement of img
  vs ref, i.e. img(y,x) ~= ref(y-dy,x-dx). Cross-power uses conj(F_ref)*F_img so
  the correlation peak sits at +shift.
- Phase-only correlation. Hann window for non-periodic (real) frames;
  window=False only for periodic synthetic data.
- Subpixel: parabolic for now (runs on PS, zero FPGA impact). Upsampled-DFT
  (Guizar-Sicairos) is a documented later upgrade.
- Phase-only peak height is the live quality metric: peak < ~0.4 -> discard.

## Validation chain (each stage reproduces the previous on identical inputs)
1. float golden model (numpy)            -- current (M3)
2. fixed-point model, SAME estimate_shift interface, tested vs (1)  <- NEXT TASK
3. FPGA FFT (Amaranth + Xilinx FFT IP), cosim'd vs (2)

## Hardware facts (hard-won)
- SoC XC7Z020-CLG400, Vivado part xc7z020clg400-1. DDR3 512 MB. QSPI W25Q128.
- Ethernet RTL8201F, **MII** (vendor "RGMII" doc is WRONG), via PS-GEM EMIO on
  PL bank 35. PHY is on **MDIO address 0** (schematic "001" misleads). The booted
  DTB had a phy@1/reg=0 mismatch that broke the 6.1 macb; working fix = let macb
  scan the bus (remove phy-handle and the mdio child node).
- Bitstream is baked into the salvaged BOOT.bin (fpga_manager = "operating" at boot).
- Board reached via ssh; ikwzm Debian, user `franz` (sudo), single DHCP client
  (systemd-networkd), rootfs grown to full 64 GB SD.

## Dev environment
- Linux VM (Ubuntu 24.04) on Proxmox. Vivado 2025.2 ML Standard in /tools/Xilinx.
- Remote JTAG: board in another room; FT232H exported via usbip from a Pi and
  attached to the VM; hw_server runs on the VM.

## Milestones
- M0 blinky: DONE.  M2 PS+DDR+Ethernet+Debian: DONE.  M3 golden model: IN PROGRESS.
- Next: fixed-point model (M3) -> HDL FFT (M4). MIPI camera = separate high-risk
  strand with a USB-UVC fallback (board has PS USB host).

## House style
German is fine. Direct, technical, pragmatic, minimal caveats. Editor: joe.
Run pytest after changes; commit in small steps; keep build artifacts out of git.
