// StaleAbsorber: absorbs the AXIS switch SRL stale prefix using TLAST detection.
//
// The Xilinx AXIS switch in ROUTING_MODE=1 retains the tail of the previous
// frame in its SRL pipeline. After each dpath_reset + re-routing, the switch
// outputs these stale beats before the new frame begins. The stale prefix
// always ends with the previous frame's TLAST.
//
// The absorber operates as a two-state machine:
//   PASSING: transparent (after power-on or after a stale frame is absorbed).
//   ABSORBING: drains S_AXIS beats silently until S_AXIS_TLAST is received,
//     then transitions back to PASSING for the new frame.
//
// `start` is a 1-cycle pulse from the CSR (CTRL[7]) that arms the absorber
// into ABSORBING mode before each DMA pass (except the first call after boot,
// where the SRL is initialised to zero and no stale prefix exists).
//
// Vivado interface inference: S_AXIS_T* / M_AXIS_T* naming triggers automatic
// AXI4-Stream interface creation in the block design.
`timescale 1ns/1ps
module stale_absorber #(
    parameter DATA_W = 128
) (
    input  wire              aclk,
    input  wire              aresetn,
    // 1-cycle arm pulse from CSR CTRL[7]
    input  wire              start,
    // AXI4-Stream slave (from sw_out M00_AXIS)
    input  wire              S_AXIS_TVALID,
    output wire              S_AXIS_TREADY,
    input  wire [DATA_W-1:0] S_AXIS_TDATA,
    input  wire              S_AXIS_TLAST,
    // AXI4-Stream master (to dma0 S_AXIS_S2MM)
    output wire              M_AXIS_TVALID,
    input  wire              M_AXIS_TREADY,
    output wire [DATA_W-1:0] M_AXIS_TDATA,
    output wire              M_AXIS_TLAST
);
    // passing=1: transparent. passing=0: absorbing (draining stale prefix).
    // Power-on default is PASSING so the first frame after bitstream load
    // (SRL initialised to zero, no stale prefix) passes through correctly.
    reg passing;

    always @(posedge aclk) begin
        if (!aresetn)
            passing <= 1'b1;                       // reset: back to passing
        else if (start)
            passing <= 1'b0;                       // arm: absorb next stale prefix
        else if (!passing && S_AXIS_TVALID && S_AXIS_TLAST)
            passing <= 1'b1;                       // stale frame done, pass new frame
    end

    // When absorbing: drain s_axis (tready=1), suppress m_axis (tvalid=0).
    assign S_AXIS_TREADY = passing ? M_AXIS_TREADY : 1'b1;
    assign M_AXIS_TVALID = passing & S_AXIS_TVALID;
    assign M_AXIS_TDATA  = S_AXIS_TDATA;
    assign M_AXIS_TLAST  = S_AXIS_TLAST;
endmodule
