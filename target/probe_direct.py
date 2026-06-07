#!/usr/bin/env python3
"""Minimal test: read switch MUX registers back after routing, then run xpower.
Also: check if bR.phys actually gets written by reading /dev/mem directly."""
import sys, time, struct, mmap, os
sys.path.insert(0, 'target/src')
sys.path.insert(0, 'golden_model/src')
import numpy as np
from guider_golden.fixed_point import FixedConfig
from guider_target.uio_backend import (
    UdmaBuf, MmReg, AxisSwitch, _dma_reset, _dma_kick,
    CSR_BASE, DMA0_BASE, DMA1_BASE, SWIN_BASE, SWOUT_BASE,
    MM2S_CR, MM2S_SR, MM2S_SA, MM2S_LEN, S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN,
    DMASR_IOC, M_XP_F, M_XP_G, S_DMA0, S_DMA1, O_XP_R, WORD_BYTES,
    CSR_ID, CSR_ID_MAGIC, SW_MUX0, SW_DISABLE,
)
cfg = FixedConfig()
csr = MmReg(CSR_BASE)
assert csr.rd(CSR_ID) == CSR_ID_MAGIC
dma0 = MmReg(DMA0_BASE)
dma1 = MmReg(DMA1_BASE)
sw_in_reg = MmReg(SWIN_BASE)
sw_out_reg = MmReg(SWOUT_BASE)
sw_in = AxisSwitch(sw_in_reg)
sw_out = AxisSwitch(sw_out_reg)
bF, bG, bR = UdmaBuf("udmabuf0"), UdmaBuf("udmabuf1"), UdmaBuf("udmabuf2")

N = 64
mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
lim = 1 << (mant - 1)

# Set routing
sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
sw_out.route({0: O_XP_R}, 1)

# Read back MI_MUX registers from both switches
print("sw_in MI_MUX[0..5]:", [hex(sw_in_reg.rd(SW_MUX0 + 4*i)) for i in range(6)])
print("sw_out MI_MUX[0..3]:", [hex(sw_out_reg.rd(SW_MUX0 + 4*i)) for i in range(4)])

# Read back DMA status
print("dma0 MM2S_SR=0x%08x S2MM_SR=0x%08x" % (dma0.rd(MM2S_SR), dma0.rd(S2MM_SR)))
print("dma1 MM2S_SR=0x%08x" % dma1.rd(MM2S_SR))
print("bF.phys=0x%x bG.phys=0x%x bR.phys=0x%x" % (bF.phys, bG.phys, bR.phys))

# Write known pattern to bF and bG, run, check bR
rng = np.random.default_rng(42)
f_re = rng.integers(-lim, lim, N*N).astype(np.int64)
f_im = rng.integers(-lim, lim, N*N).astype(np.int64)
g_re = rng.integers(-lim, lim, N*N).astype(np.int64)
g_im = rng.integers(-lim, lim, N*N).astype(np.int64)
bF.write_complex(f_re, f_im, mant, mant)
bG.write_complex(g_re, g_im, mant, mant)

# Verify bF was written correctly
bF.from_device()
lo0, _ = struct.unpack_from('<QQ', bF.m, 0)
re_mask = (1<<18)-1
packed0 = int(f_re[0] & re_mask) | (int(f_im[0] & re_mask) << 18)
print("bF[0] write check: lo=0x%x expected=0x%x match=%s" % (lo0, packed0, lo0==packed0))

nb = N * N * WORD_BYTES
PREFIX = 4
_dma_reset(dma0); _dma_reset(dma1)
_dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, (N*N + PREFIX) * WORD_BYTES)
_dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
_dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)

t0 = time.time()
while not (dma0.rd(S2MM_SR) & DMASR_IOC):
    if time.time() - t0 > 10: raise TimeoutError("timeout S2MM")

elapsed = time.time() - t0
print("IOC after %.3fs" % elapsed)
print("dma0 after: MM2S_SR=0x%08x S2MM_SR=0x%08x" % (dma0.rd(MM2S_SR), dma0.rd(S2MM_SR)))
print("dma1 after: MM2S_SR=0x%08x" % dma1.rd(MM2S_SR))

# read bR[0..PREFIX+3]
r_re, r_im = bR.read_complex(inb, inb, N*N + PREFIX, offset_beats=0)
exp_re = f_re * g_re + f_im * g_im
print("bR[0..%d]:" % (PREFIX+3))
for i in range(PREFIX+4):
    print("  [%d] hw=%d exp[%d]=%d" % (i, r_re[i], i-PREFIX, exp_re[i-PREFIX] if i>=PREFIX else -1))
# Check at prefix offset
ok = np.array_equal(r_re[PREFIX:PREFIX+N*N], exp_re)
print("bit-exact at offset %d: %s" % (PREFIX, ok))
if not ok:
    bad = np.where(r_re[PREFIX:PREFIX+N*N] != exp_re)[0][:3]
    for j in bad:
        print("  [%d+%d] hw=%d exp=%d" % (PREFIX, j, r_re[PREFIX+j], exp_re[j]))
