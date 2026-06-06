// Verilog wrapper around the Amaranth-emitted `phase_correlator_top`
// (guider_hdl.build) that re-presents its flat `iface__field` ports as standard
// Xilinx AXI interfaces, so the Vivado block design infers them. Pure rename +
// fixed TDATA widening -- no logic (FIRST is already regenerated inside the
// Amaranth top, see stream.py:FirstGen). Verilog-2001 so it can be a module
// reference in the BD.
//
// All AXIS data buses are widened to a uniform AXIS_TDATA_W (the AXIS Switch IP
// needs one width across its ports, and AXI-DMA stream widths are byte multiples):
// each kernel payload sits in the low bits, the rest is zero/ignored. The PS packs
// one complex element per AXIS_TDATA_W/8 bytes in DDR (see guider_target).
//
// Clock/reset: single AXI clock `aclk`; `aresetn` active-low -> the Amaranth
// top's active-high sync reset `rst`.

`default_nettype none

module phase_correlator_axi #(
    parameter integer AXIS_TDATA_W = 128      // uniform wire width (>= 74-bit payload)
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI_LITE:S_AXIS_WINDOW_SAMPLE:S_AXIS_WINDOW_COEF:M_AXIS_WINDOW_OUT:S_AXIS_FFT_IN:M_AXIS_FFT_OUT:S_AXIS_XPOWER_F:S_AXIS_XPOWER_G:M_AXIS_XPOWER_R:S_AXIS_RESCALE_R:M_AXIS_RESCALE_P, ASSOCIATED_RESET aresetn" *)
    input  wire                     aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire                     aresetn,

    // Datapath reset for the AXIS switches: aresetn gated by CTRL.dpath_reset, so
    // the PS can flush the switches' stale-beat prefix per frame. Drive the
    // switches' (data-path) aresetn from this; keep their s_axi_ctrl_aresetn and
    // the DMAs/CSR on the global aresetn. Active-low. Plain output (connected as a
    // net in the BD), no interface inference.
    output wire                     dpath_aresetn,

    // ---- AXI4-Lite control/status (S_AXI_LITE) ----
    input  wire [7:0]               s_axi_lite_awaddr,
    input  wire                     s_axi_lite_awvalid,
    output wire                     s_axi_lite_awready,
    input  wire [31:0]              s_axi_lite_wdata,
    input  wire [3:0]               s_axi_lite_wstrb,
    input  wire                     s_axi_lite_wvalid,
    output wire                     s_axi_lite_wready,
    output wire [1:0]               s_axi_lite_bresp,
    output wire                     s_axi_lite_bvalid,
    input  wire                     s_axi_lite_bready,
    input  wire [7:0]               s_axi_lite_araddr,
    input  wire                     s_axi_lite_arvalid,
    output wire                     s_axi_lite_arready,
    output wire [31:0]              s_axi_lite_rdata,
    output wire [1:0]               s_axi_lite_rresp,
    output wire                     s_axi_lite_rvalid,
    input  wire                     s_axi_lite_rready,

    // ---- AXIS inputs (DMA MM2S -> kernels) ----
    input  wire [AXIS_TDATA_W-1:0]  s_axis_window_sample_tdata,
    input  wire                     s_axis_window_sample_tlast,
    input  wire                     s_axis_window_sample_tvalid,
    output wire                     s_axis_window_sample_tready,

    input  wire [AXIS_TDATA_W-1:0]  s_axis_window_coef_tdata,
    input  wire                     s_axis_window_coef_tlast,
    input  wire                     s_axis_window_coef_tvalid,
    output wire                     s_axis_window_coef_tready,

    input  wire [AXIS_TDATA_W-1:0]  s_axis_fft_in_tdata,
    input  wire                     s_axis_fft_in_tlast,
    input  wire                     s_axis_fft_in_tvalid,
    output wire                     s_axis_fft_in_tready,

    input  wire [AXIS_TDATA_W-1:0]  s_axis_xpower_f_tdata,
    input  wire                     s_axis_xpower_f_tlast,
    input  wire                     s_axis_xpower_f_tvalid,
    output wire                     s_axis_xpower_f_tready,

    input  wire [AXIS_TDATA_W-1:0]  s_axis_xpower_g_tdata,
    input  wire                     s_axis_xpower_g_tlast,
    input  wire                     s_axis_xpower_g_tvalid,
    output wire                     s_axis_xpower_g_tready,

    input  wire [AXIS_TDATA_W-1:0]  s_axis_rescale_r_tdata,
    input  wire                     s_axis_rescale_r_tlast,
    input  wire                     s_axis_rescale_r_tvalid,
    output wire                     s_axis_rescale_r_tready,

    // ---- AXIS outputs (kernels -> DMA S2MM) ----
    output wire [AXIS_TDATA_W-1:0]  m_axis_window_out_tdata,
    output wire                     m_axis_window_out_tlast,
    output wire                     m_axis_window_out_tvalid,
    input  wire                     m_axis_window_out_tready,

    output wire [AXIS_TDATA_W-1:0]  m_axis_fft_out_tdata,
    output wire                     m_axis_fft_out_tlast,
    output wire                     m_axis_fft_out_tvalid,
    input  wire                     m_axis_fft_out_tready,

    output wire [AXIS_TDATA_W-1:0]  m_axis_xpower_r_tdata,
    output wire                     m_axis_xpower_r_tlast,
    output wire                     m_axis_xpower_r_tvalid,
    input  wire                     m_axis_xpower_r_tready,

    output wire [AXIS_TDATA_W-1:0]  m_axis_rescale_p_tdata,
    output wire                     m_axis_rescale_p_tlast,
    output wire                     m_axis_rescale_p_tvalid,
    input  wire                     m_axis_rescale_p_tready
);
    // payload widths of the Amaranth top (from guider_hdl.build emission)
    localparam W_WIN_SAMPLE = 12;
    localparam W_WIN_COEF   = 13;
    localparam W_WIN_OUT    = 14;
    localparam W_FFT        = 36;   // complex mant_bits=18
    localparam W_XPOW       = 36;   // complex mant_bits=18 (F, G)
    localparam W_R          = 74;   // complex 2*mant_bits+1
    localparam W_P          = 34;   // complex unit_bits+2

    wire rst = ~aresetn;
    wire core_dpath_reset;
    // active-high datapath reset from CTRL.dpath_reset, OR'd with the global reset
    assign dpath_aresetn = aresetn & ~core_dpath_reset;

    phase_correlator_top u_core (
        .clk (aclk),
        .rst (rst),
        .o_dpath_reset (core_dpath_reset),

        // AXI-Lite (subordinate)
        .s_axil__awaddr  (s_axi_lite_awaddr),
        .s_axil__awvalid (s_axi_lite_awvalid),
        .s_axil__awready (s_axi_lite_awready),
        .s_axil__wdata   (s_axi_lite_wdata),
        .s_axil__wstrb   (s_axi_lite_wstrb),
        .s_axil__wvalid  (s_axi_lite_wvalid),
        .s_axil__wready  (s_axi_lite_wready),
        .s_axil__bresp   (s_axi_lite_bresp),
        .s_axil__bvalid  (s_axi_lite_bvalid),
        .s_axil__bready  (s_axi_lite_bready),
        .s_axil__araddr  (s_axi_lite_araddr),
        .s_axil__arvalid (s_axi_lite_arvalid),
        .s_axil__arready (s_axi_lite_arready),
        .s_axil__rdata   (s_axi_lite_rdata),
        .s_axil__rresp   (s_axi_lite_rresp),
        .s_axil__rvalid  (s_axi_lite_rvalid),
        .s_axil__rready  (s_axi_lite_rready),

        // AXIS inputs: take the low payload bits of each TDATA
        .window_sample__valid   (s_axis_window_sample_tvalid),
        .window_sample__ready   (s_axis_window_sample_tready),
        .window_sample__last    (s_axis_window_sample_tlast),
        .window_sample__payload (s_axis_window_sample_tdata[W_WIN_SAMPLE-1:0]),

        .window_coef__valid     (s_axis_window_coef_tvalid),
        .window_coef__ready     (s_axis_window_coef_tready),
        .window_coef__last      (s_axis_window_coef_tlast),
        .window_coef__payload   (s_axis_window_coef_tdata[W_WIN_COEF-1:0]),

        .fft_in__valid          (s_axis_fft_in_tvalid),
        .fft_in__ready          (s_axis_fft_in_tready),
        .fft_in__last           (s_axis_fft_in_tlast),
        .fft_in__payload        (s_axis_fft_in_tdata[W_FFT-1:0]),

        .xpower_f__valid        (s_axis_xpower_f_tvalid),
        .xpower_f__ready        (s_axis_xpower_f_tready),
        .xpower_f__last         (s_axis_xpower_f_tlast),
        .xpower_f__payload      (s_axis_xpower_f_tdata[W_XPOW-1:0]),

        .xpower_g__valid        (s_axis_xpower_g_tvalid),
        .xpower_g__ready        (s_axis_xpower_g_tready),
        .xpower_g__last         (s_axis_xpower_g_tlast),
        .xpower_g__payload      (s_axis_xpower_g_tdata[W_XPOW-1:0]),

        .rescale_r__valid       (s_axis_rescale_r_tvalid),
        .rescale_r__ready       (s_axis_rescale_r_tready),
        .rescale_r__last        (s_axis_rescale_r_tlast),
        .rescale_r__payload     (s_axis_rescale_r_tdata[W_R-1:0]),

        // AXIS outputs: drive the low payload bits (high bits zeroed below)
        .window_out__valid      (m_axis_window_out_tvalid),
        .window_out__ready      (m_axis_window_out_tready),
        .window_out__last       (m_axis_window_out_tlast),
        .window_out__payload    (m_axis_window_out_tdata[W_WIN_OUT-1:0]),

        .fft_out__valid         (m_axis_fft_out_tvalid),
        .fft_out__ready         (m_axis_fft_out_tready),
        .fft_out__last          (m_axis_fft_out_tlast),
        .fft_out__payload       (m_axis_fft_out_tdata[W_FFT-1:0]),

        .xpower_r__valid        (m_axis_xpower_r_tvalid),
        .xpower_r__ready        (m_axis_xpower_r_tready),
        .xpower_r__last         (m_axis_xpower_r_tlast),
        .xpower_r__payload      (m_axis_xpower_r_tdata[W_R-1:0]),

        .rescale_p__valid       (m_axis_rescale_p_tvalid),
        .rescale_p__ready       (m_axis_rescale_p_tready),
        .rescale_p__last        (m_axis_rescale_p_tlast),
        .rescale_p__payload     (m_axis_rescale_p_tdata[W_P-1:0])
    );

    // zero the unused high TDATA bits on the master streams
    assign m_axis_window_out_tdata[AXIS_TDATA_W-1:W_WIN_OUT] = {(AXIS_TDATA_W-W_WIN_OUT){1'b0}};
    assign m_axis_fft_out_tdata   [AXIS_TDATA_W-1:W_FFT]     = {(AXIS_TDATA_W-W_FFT){1'b0}};
    assign m_axis_xpower_r_tdata  [AXIS_TDATA_W-1:W_R]       = {(AXIS_TDATA_W-W_R){1'b0}};
    assign m_axis_rescale_p_tdata [AXIS_TDATA_W-1:W_P]       = {(AXIS_TDATA_W-W_P){1'b0}};

endmodule

`default_nettype wire
