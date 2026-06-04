"""AXI4-Stream interface for the PL datapath assembly.

The synthesizable top-level is a set of streaming compute kernels fed by the PS
via AXI-DMA out of / into DDR (frames are too large for on-chip BRAM at the
whole-field FFT sizes -- see hdl/README.md "Top-level (DDR-streaming)"). Every
kernel speaks this small AXI-Stream dialect so they compose with
`amaranth.lib.wiring.connect` and map cleanly onto AXIS DMA endpoints.

`Stream` is declared from the *source* (producer) viewpoint:
    producer port:  Out(Stream(...))      # drives valid/first/last/payload
    consumer port:   In(Stream(...))      # drives ready
`wiring.connect` flips one side, so a producer's Out connects to a consumer's In.

FIRST/LAST mark frame boundaries (one frame == one N x N bin set), mirroring the
AXIS TLAST the FFT IP and the DMA use; FIRST resets per-frame accumulators
(e.g. the block-max in the cross-power) without a separate sideband.
"""
from amaranth import *
from amaranth.lib import wiring, data
from amaranth.lib.wiring import In, Out


def complex_layout(width: int) -> data.StructLayout:
    """Signed complex sample: real in the low half, imag in the high half."""
    return data.StructLayout({"re": signed(width), "im": signed(width)})


class Stream(wiring.Signature):
    """VALID/READY handshake + FIRST/LAST frame markers + opaque payload."""

    def __init__(self, payload_shape):
        self.payload_shape = payload_shape
        super().__init__({
            "valid":   Out(1),
            "ready":   In(1),
            "first":   Out(1),
            "last":    Out(1),
            "payload": Out(payload_shape),
        })
