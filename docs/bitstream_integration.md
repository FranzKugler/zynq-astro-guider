# M5 — Vivado block design + bitstream integration (scoping)

Goal: put `guider_hdl.top.PhaseCorrelatorPL` on the XC7Z020 as a loadable
bitstream the PS app (`guider_target.UioBackend`) drives over AXI-DMA, and
validate it on-board against `ModelBackend` (identical results == HW correct).

This is the M5 plan. Nothing here is built yet; it records the topology, the
decisions, and the task breakdown.

## Deploy / iterate path — SSH, not JTAG
The board runs ikwzm Debian with the FPGA manager + `u-dma-buf`, so the whole
loop is over ssh (`ssh franz@zynq7020`):

1. build the bitstream in Vivado on the VM;
2. `scp` the `.bit.bin` (+ a device-tree overlay) to the board;
3. load via the FPGA manager / overlay (`fpga_manager`), no JTAG;
4. run `guider_target` over ssh; compare `UioBackend` vs `ModelBackend`.

JTAG (the usbip/FT232H path) is kept in reserve for **only** two things:
ILA / live signal probing, and PS7 re-init / unbricking.

### Board state (checked 2026-06-04, over ssh)
- fpga_manager `fpga0` ("Xilinx Zynq FPGA Manager", `f8007000.devcfg`) **state =
  operating**; DT-overlay configfs present; DT has `fpga-full` + `amba_pl`. →
  **runtime full-bitstream load via an overlay works; no BOOT.bin rebuild for
  loading.** (Kernel 6.1.108-armv7-fpga.)
- **`u-dma-buf` NOT loaded** (no `/dev/udmabuf*`) → install/load it (UioBackend
  needs it for contiguous DMA buffers).
- **`fclkcfg` NOT loaded** (no `/sys/class/fclkcfg/`) → set the PL clock via the
  overlay's clock config (or install fclkcfg); confirm FCLK_CLK0 ≈ 100 MHz
  (`sudo grep -i fclk /sys/kernel/debug/clk/clk_summary`).
- **CMA = 16 MB** (14 MB free) → enough for small N; for N=256 bump `cma=` in
  bootargs or point u-dma-buf at a reserved-memory region.
- CSR access: declare the AXI-Lite CSR as `generic-uio` in the overlay
  (→ `/dev/uioN`) or mmap `/dev/mem` at the `amba_pl` base.

**PS7 caveat.** On Zynq-7000 the PS7 (DDR, PLLs, clocks, MIO, AXI ports) is
initialised once at boot by the FSBL in the salvaged BOOT.bin; a runtime PL
bitstream swap only reconfigures the fabric. DDR + base clocks already work
(Linux boots). Our design additionally needs a PL clock (FCLK — reconfigurable
from Linux) and the **AXI-HP** slave ports for PL-mastered DMA to DDR (driven
from the PL side, so a fabric-only swap should suffice). If it turns out the
FSBL left no usable FCLK or HP routing, rebuild BOOT.bin from our `.xsa` and
reflash QSPI (doable over ssh via `flashcp`/mtd — still no JTAG). **First M5
step is to confirm the current BOOT.bin exposes a usable FCLK + HP path**; if
not, an `.xsa`-matched BOOT.bin rebuild is a sub-task.

## Block design topology
```
  PS7 ── M_AXI_GP0 ──> AXI SmartConnect ──> AXI-Lite: DMA ctrl, AXIS-switch ctrl,
   │                                                  PhaseCorrelatorPL ctrl/status
   │── FCLK_CLK0 (100 MHz)  ── PL clock
   │── FCLK_RESET0_N        ── Proc System Reset
   │<─ S_AXI_HP0 <── AXI-DMA (MM2S0/S2MM)  ─┐
   │<─ S_AXI_HP1 <── AXI-DMA (MM2S1)        │  data: DDR <-> kernels
                                            v
        MM2S0, MM2S1 ─> AXIS Switch ─> {window, fft, xpower, rescale} inputs
        kernel outputs ─> AXIS Switch ─> S2MM
```
Components: PS7, 2× AXI-DMA (gives MM2S0+S2MM and MM2S1 — two simultaneous input
streams for the 2-input passes window{sample,coef} and xpower{F,G}), two AXIS
switches (PS-routed per pass), AXI SmartConnect for the GP control bus, Processor
System Reset, and **`PhaseCorrelatorPL`** (Amaranth → Verilog, added as an RTL
module, wrapped with an AXI-Lite control/status register file — see below).

One FFT IP inside `PhaseCorrelatorPL` is time-shared across all FFT/IFFT passes:
the PS sets `fft_inverse`, routes the switch to the fft endpoint, and streams a
frame; repeat per pass.

## Data movement — transposes stay on the PS
The orchestrator already does the 2-D FFT as `pass → transpose → pass`, with the
transpose as a host `.T.copy()` between two `fft_pass` backend calls (see
`orchestrator._fft2`). So **every DMA transfer is contiguous, row-major**; the
column-major "corner-turn" is a PS memory operation on the udmabuf region (a
strided copy of ~0.5 MB for N=256 — well under a ms on the A9, negligible at
guide-frame rates). This sidesteps 2-D/scatter-gather DMA entirely. The PL never
sees a transpose.

Pass → transfers (each pass = configure regs, kick DMA(s), wait done):
| pass            | MM2S0      | MM2S1   | S2MM     | regs                       |
|-----------------|------------|---------|----------|----------------------------|
| window (×2)     | samples    | coefs   | windowed | switch→window              |
| FFT row/col(×4) | frame      | —       | frame    | switch→fft, fft_inverse=0  |
| cross-power     | F          | G       | R        | switch→xpower; read max    |
| rescale/phase   | R          | —       | P        | switch→rescale, rescale_sh |
| IFFT row/col    | frame      | —       | frame    | switch→fft, fft_inverse=1  |
| (transpose)     | — PS strided copy in DDR between the two FFT passes —       |
| peak            | — PS reads corr from DDR: argmax + parabolic subpixel —     |

## Control plane
`PhaseCorrelatorPL` exposes control/status as bare signals (`fft_inverse`,
`rescale_sh`, `xpower_max[37]`, `xpower_max_valid`, `fft_blk_exp_sum[16]`). Wrap
it with a small **AXI-Lite CSR** (hand-rolled in Amaranth, or amaranth-soc CSR +
an AXI-Lite bridge) so the whole PL is one IP with one AXI-Lite slave + the AXIS
data ports — a single uio register region for `UioBackend`. (Alternative: AXI
GPIO IP blocks in the BD, but the 37-bit `xpower_max` spans words awkwardly; the
CSR is cleaner.) The AXI-DMA and AXIS-switch each bring their own AXI-Lite.

## Build + package flow
1. `python -m guider_hdl.build [outdir] [N]`: emit Verilog for `PhaseCorrelatorTop`
   (CSR + datapath) via `amaranth.back.verilog` (needs `amaranth-yosys`). The FFT
   is left as a black-box instance `fft_<N>`; generate its XCI with
   `ip/gen_fft_ip.tcl` (the build prints the exact command). Emitted top ports are
   flat `iface__field` (e.g. `s_axil__awaddr`, `xpower_f__payload`); the BD wrapper
   renames them to Xilinx AXI-Lite / AXIS conventions.
2. `bd/create_bd.tcl` (new): build the block design (PS7 preset for the Z7-Lite,
   the IP above, DMAs, switches, connections), generate the HDL wrapper, synth +
   impl, `write_bitstream`, and `write_hw_platform` (.xsa) for a possible BOOT
   rebuild.
3. Convert `.bit` → `.bit.bin` (`bootgen` / `bif`) + a device-tree overlay for
   the FPGA manager; `scp` to the board.
4. `target/`: implement `UioBackend` against the uio/udmabuf regions; validate
   vs `ModelBackend`.

## Decisions
1. **DMA/routing**: 2× AXI-DMA (MM2S0+S2MM, MM2S1) + 2 AXIS switches, PS-routed
   per pass. (DECIDED)
2. **Control plane**: AXI-Lite CSR wrapper in Amaranth — one IP, one uio region.
   (DECIDED)
3. **PL clock**: single FCLK @ 100 MHz for the whole datapath; revisit only if the
   FFT IP misses timing on the −1 part. (DECIDED, revisit on timing)
4. **Packaging**: add `PhaseCorrelatorPL` as RTL via a Verilog wrapper rather than
   `package_ip`. (DECIDED)

## Task breakdown
- [x] confirm runtime bitstream load works (fpga_manager operating, fpga-full +
      overlay configfs) -- yes, no BOOT rebuild. Follow-ups (all ssh-side): install
      u-dma-buf; confirm/set FCLK_CLK0; CMA sizing for N=256; generic-uio vs /dev/mem
      for the CSR. See "Board state" above.
- [x] AXI-Lite CSR wrapper around PhaseCorrelatorPL (csr.py: AXILite,
      PhaseCorrelatorCsr, PhaseCorrelatorTop) + cosim (test_csr.py). Register map
      in the csr.py docstring is the UioBackend contract.
- [x] `guider_hdl.build`: emit Verilog for PhaseCorrelatorTop (FFT as black box
      fft_<N>) + print the FFT-IP XCI command. test_build.py guards it.
- [x] AXIS-native boundary: `stream.py:FirstGen` regenerates FIRST from LAST/reset
      inside the Amaranth top, so PhaseCorrelatorTop's data ports are TLAST-only
      (no TFIRST, which AXI-DMA doesn't provide). The BD SV wrapper is now a pure
      port rename. Cosim'd in test_axis.py (incl. 2-frame block-max reset).
- [~] `bd/create_bd.tcl` + `bd/phase_correlator_axi.v`: block design **validates**
      (PS7 + 2 AXI-DMA + 2 AXIS switches + SmartConnects + the IP, interfaces
      inferred from the Verilog wrapper's X_INTERFACE attrs). `-tclargs bd` =
      create+validate (fast, FFT IP skipped); `-tclargs all` = + synth/impl/
      bitstream/.xsa. Full build (timing closure) pending.
- [ ] timing closure @ 100 MHz (FFT IP is the long pole)
- [ ] .bit.bin + DT overlay + fpga_manager load over ssh
- [ ] implement `UioBackend`; on-board validation vs `ModelBackend`
