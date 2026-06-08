// StaleAbsorber: absorbs the AXIS switch register-pipeline stale prefix by
// counting a fixed number of beats rather than waiting for TLAST.
//
// Root cause: when sw_out re-routes from the previous kernel's output to the
// new kernel's output, the switch's registered crossbar holds the LAST valid
// beat of the old source in its pipeline stages.  Those N_STALE beats appear
// at M00_AXIS before the new frame begins -- they carry TLAST=0 (the old
// frame's TLAST was already consumed by the DMA on the previous pass).
// Waiting for TLAST would absorb the entire new frame.
//
// `start` is a 1-cycle pulse from CSR CTRL[7].  The absorber counts N_STALE
// incoming TVALID beats silently, then transitions to PASSING for the new
// frame.  Power-on default is PASSING (first call after boot has no stale
// prefix).
//
// N_STALE=4 matches the measured stale-beat count for the Xilinx AXIS switch
// with ROUTING_MODE=1, DECODER_REG=1, NUM_SI_SLOTS=4 in the 6th/7th bitstream.
//
// Vivado interface inference: S_AXIS_T* / M_AXIS_T* naming triggers automatic
// AXI4-Stream interface creation in the block design.
`timescale 1ns/1ps
module stale_absorber #(
    parameter DATA_W  = 128,
    parameter N_STALE = 4    // stale beats to absorb on each start pulse
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
    localparam CNT_W = $clog2(N_STALE + 1);
    reg [CNT_W-1:0] cnt;
    reg passing;

    always @(posedge aclk) begin
        if (!aresetn) begin
            passing <= 1'b1;
            cnt     <= {CNT_W{1'b0}};
        end else if (start) begin
            passing <= 1'b0;
            cnt     <= N_STALE[CNT_W-1:0];
        end else if (!passing && S_AXIS_TVALID) begin
            // absorb beats until cnt reaches 1, then go PASSING
            if (cnt > 1)
                cnt <= cnt - 1'b1;
            else
                passing <= 1'b1;  // last stale beat absorbed this cycle
        end
    end

    // When absorbing: accept from slave (tready=1), suppress master (tvalid=0).
    assign S_AXIS_TREADY = passing ? M_AXIS_TREADY : 1'b1;
    assign M_AXIS_TVALID = passing & S_AXIS_TVALID;
    assign M_AXIS_TDATA  = S_AXIS_TDATA;
    assign M_AXIS_TLAST  = S_AXIS_TLAST;
endmodule
