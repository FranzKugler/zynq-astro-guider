# Architecture notes

## Pipeline (per guide frame)
window (Hann) -> FFT2 -> cross-power conj(F_ref)*F_img -> phase-only
normalize -> IFFT2 -> peak -> subpixel (parabolic) -> (dy, dx) shift.

## Validation chain
1. float golden model (numpy)                                   -- DONE
2. fixed-point model (same interface, bit-width = FFT spec)     -- DONE (fixed_point.md)
3. PL datapath (Amaranth kernels + Xilinx FFT IP), cosim'd vs 2 -- DONE
4. PS orchestration (guider_target) reproduces 2               -- DONE
5. integrated bitstream + on-board validation                  -- NEXT (bitstream_integration.md)

Each stage reproduces the previous on identical inputs.

## Partition (target)
PL: ingest -> window -> FFT2 -> x conj(ref FFT) -> IFFT2 -> |.| -> to PS
PS: subpixel peak, control loop, mount commands, Ethernet, trajectory log.
