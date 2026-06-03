# Architecture notes

## Pipeline (per guide frame)
window (Hann) -> FFT2 -> cross-power conj(F_ref)*F_img -> phase-only
normalize -> IFFT2 -> peak -> subpixel (parabolic) -> (dy, dx) shift.

## Validation chain
1. float golden model (numpy)  <- you are here (M3)
2. fixed-point model (same interface, bit-width = FFT spec) — see fixed_point.md
3. FPGA FFT (Amaranth + Xilinx FFT IP), cosim'd against (2)

The fixed-point model and FPGA must reproduce (1) on identical inputs.

## Partition (target)
PL: ingest -> window -> FFT2 -> x conj(ref FFT) -> IFFT2 -> |.| -> to PS
PS: subpixel peak, control loop, mount commands, Ethernet, trajectory log.
