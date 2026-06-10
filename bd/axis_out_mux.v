// 4:1 AXI Stream output mux -- replaces the Xilinx AXIS switch for sw_out.
// sel_r latches new sel on a 1-cycle commit pulse (from CSR CTRL[7]).
// No SRL pipeline = no routing glitch = no stale beats.
// Slave ordering: S00=window_out S01=fft_out S02=xpower_r S03=rescale_p
`timescale 1ns/1ps
module axis_out_mux (
    input  wire         aclk,
    input  wire         aresetn,
    // Control from CSR: commit is a 1-cycle pulse, sel is the new route
    input  wire [1:0]   sel,
    input  wire         commit,
    // S00: window_out
    input  wire [127:0] S00_AXIS_TDATA,
    input  wire         S00_AXIS_TVALID,
    output wire         S00_AXIS_TREADY,
    input  wire         S00_AXIS_TLAST,
    // S01: fft_out
    input  wire [127:0] S01_AXIS_TDATA,
    input  wire         S01_AXIS_TVALID,
    output wire         S01_AXIS_TREADY,
    input  wire         S01_AXIS_TLAST,
    // S02: xpower_r
    input  wire [127:0] S02_AXIS_TDATA,
    input  wire         S02_AXIS_TVALID,
    output wire         S02_AXIS_TREADY,
    input  wire         S02_AXIS_TLAST,
    // S03: rescale_p
    input  wire [127:0] S03_AXIS_TDATA,
    input  wire         S03_AXIS_TVALID,
    output wire         S03_AXIS_TREADY,
    input  wire         S03_AXIS_TLAST,
    // M00: to DMA0 S_AXIS_S2MM
    output wire [127:0] M00_AXIS_TDATA,
    output wire         M00_AXIS_TVALID,
    input  wire         M00_AXIS_TREADY,
    output wire         M00_AXIS_TLAST
);
    reg [1:0] sel_r;
    always @(posedge aclk) begin
        if (!aresetn)   sel_r <= 2'd0;
        else if (commit) sel_r <= sel;
    end

    assign M00_AXIS_TDATA  = (sel_r == 2'd0) ? S00_AXIS_TDATA  :
                             (sel_r == 2'd1) ? S01_AXIS_TDATA  :
                             (sel_r == 2'd2) ? S02_AXIS_TDATA  :
                                               S03_AXIS_TDATA;
    assign M00_AXIS_TVALID = (sel_r == 2'd0) ? S00_AXIS_TVALID :
                             (sel_r == 2'd1) ? S01_AXIS_TVALID :
                             (sel_r == 2'd2) ? S02_AXIS_TVALID :
                                               S03_AXIS_TVALID;
    assign M00_AXIS_TLAST  = (sel_r == 2'd0) ? S00_AXIS_TLAST  :
                             (sel_r == 2'd1) ? S01_AXIS_TLAST  :
                             (sel_r == 2'd2) ? S02_AXIS_TLAST  :
                                               S03_AXIS_TLAST;

    assign S00_AXIS_TREADY = (sel_r == 2'd0) & M00_AXIS_TREADY;
    assign S01_AXIS_TREADY = (sel_r == 2'd1) & M00_AXIS_TREADY;
    assign S02_AXIS_TREADY = (sel_r == 2'd2) & M00_AXIS_TREADY;
    assign S03_AXIS_TREADY = (sel_r == 2'd3) & M00_AXIS_TREADY;
endmodule
