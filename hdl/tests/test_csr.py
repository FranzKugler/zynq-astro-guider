"""Cosim the AXI-Lite CSR: control writes drive the datapath, status latches read
back, and the wrapper routes CTRL through to PhaseCorrelatorPL.
"""
from amaranth.sim import Simulator

from guider_hdl.csr import PhaseCorrelatorCsr, PhaseCorrelatorTop, ID_MAGIC
from guider_hdl.fft_ip import FftStub

CTRL, STATUS, XPMAX_LO, XPMAX_HI, BLKEXP, ID = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14


async def axil_write(ctx, ax, addr, data):
    ctx.set(ax.awaddr, addr); ctx.set(ax.awvalid, 1)
    ctx.set(ax.wdata, data); ctx.set(ax.wstrb, 0xF); ctx.set(ax.wvalid, 1)
    ctx.set(ax.bready, 1)
    while not ctx.get(ax.awready):
        await ctx.tick()
    await ctx.tick(); ctx.set(ax.awvalid, 0)
    while not ctx.get(ax.wready):
        await ctx.tick()
    await ctx.tick(); ctx.set(ax.wvalid, 0)
    while not ctx.get(ax.bvalid):
        await ctx.tick()
    await ctx.tick(); ctx.set(ax.bready, 0)


async def axil_read(ctx, ax, addr):
    ctx.set(ax.araddr, addr); ctx.set(ax.arvalid, 1); ctx.set(ax.rready, 1)
    while not ctx.get(ax.arready):
        await ctx.tick()
    await ctx.tick(); ctx.set(ax.arvalid, 0)
    while not ctx.get(ax.rvalid):
        await ctx.tick()
    data = ctx.get(ax.rdata)
    await ctx.tick(); ctx.set(ax.rready, 0)
    return data


def _run(coro_factory, dut):
    res = {}

    async def tb(ctx):
        res["v"] = await coro_factory(ctx)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return res["v"]


def test_ctrl_write_drives_outputs_and_reads_back():
    dut = PhaseCorrelatorCsr(sh_bits=5)
    ax = dut.s_axil

    async def body(ctx):
        await axil_write(ctx, ax, CTRL, 0b1 | (13 << 1))      # inverse=1, sh=13
        rb = await axil_read(ctx, ax, CTRL)
        return ctx.get(dut.o_fft_inverse), ctx.get(dut.o_rescale_sh), rb

    inv, sh, rb = _run(body, dut)
    assert inv == 1 and sh == 13
    assert rb == (0b1 | (13 << 1))


def test_status_latch_and_clear():
    dut = PhaseCorrelatorCsr(max_bits=37, blk_bits=16)
    ax = dut.s_axil
    xmax = (0x1A << 32) | 0xCAFEF00D                          # 37-bit value
    blk = 0x1234

    async def body(ctx):
        # pulse the cross-power max strobe and the fft-done strobe
        ctx.set(dut.i_xpower_max, xmax)
        ctx.set(dut.i_xpower_max_valid, 1)
        ctx.set(dut.i_fft_blk_exp_sum, blk)
        ctx.set(dut.i_fft_done, 1)
        await ctx.tick()
        ctx.set(dut.i_xpower_max_valid, 0)
        ctx.set(dut.i_fft_done, 0)
        out = {
            "lo": await axil_read(ctx, ax, XPMAX_LO),
            "hi": await axil_read(ctx, ax, XPMAX_HI),
            "blk": await axil_read(ctx, ax, BLKEXP),
            "st": await axil_read(ctx, ax, STATUS),
            "id": await axil_read(ctx, ax, ID),
        }
        await axil_write(ctx, ax, STATUS, 0b11)              # W1C both done bits
        out["st_after"] = await axil_read(ctx, ax, STATUS)
        return out

    o = _run(body, dut)
    assert o["lo"] == 0xCAFEF00D
    assert o["hi"] == 0x1A
    assert o["blk"] == blk
    assert o["st"] == 0b11                                    # xpower_done & fft_done
    assert o["id"] == ID_MAGIC
    assert o["st_after"] == 0


def test_top_ctrl_propagates_into_datapath():
    dut = PhaseCorrelatorTop(n=8, mant_bits=18, core=FftStub(n=8, input_width=18))
    ax = dut.s_axil

    async def body(ctx):
        await axil_write(ctx, ax, CTRL, 0b1 | (7 << 1))       # inverse=1, sh=7
        return ctx.get(dut._pl.fft_inverse), ctx.get(dut._pl.rescale_sh)

    inv, sh = _run(body, dut)
    assert inv == 1 and sh == 7
