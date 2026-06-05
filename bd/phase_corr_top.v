// Top for the phase-correlation datapath + GEM-EMIO Ethernet: wraps the phase_corr BD (datapath + PS7 with
// ENET0 on EMIO) and maps the GMII EMIO down to the RTL8201F 100M MII pins on
// bank 35. PHY sources tx_clk/rx_clk (MII inputs to the MAC); the upper GMII
// nibble / tx_er / col / crs are unused at 100M. MDIO via a local IOBUF.

`timescale 1 ps / 1 ps
`default_nettype none

module phase_corr_top (
    inout  wire [14:0] DDR_addr,
    inout  wire [2:0]  DDR_ba,
    inout  wire        DDR_cas_n,
    inout  wire        DDR_ck_n,
    inout  wire        DDR_ck_p,
    inout  wire        DDR_cke,
    inout  wire        DDR_cs_n,
    inout  wire [3:0]  DDR_dm,
    inout  wire [31:0] DDR_dq,
    inout  wire [3:0]  DDR_dqs_n,
    inout  wire [3:0]  DDR_dqs_p,
    inout  wire        DDR_odt,
    inout  wire        DDR_ras_n,
    inout  wire        DDR_reset_n,
    inout  wire        DDR_we_n,
    inout  wire        FIXED_IO_ddr_vrn,
    inout  wire        FIXED_IO_ddr_vrp,
    inout  wire [53:0] FIXED_IO_mio,
    inout  wire        FIXED_IO_ps_clk,
    inout  wire        FIXED_IO_ps_porb,
    inout  wire        FIXED_IO_ps_srstb,
    // RTL8201F MII (bank 35)
    output wire [3:0]  mii_txd,
    output wire        mii_tx_en,
    input  wire        mii_tx_clk,
    input  wire [3:0]  mii_rxd,
    input  wire        mii_rx_dv,
    input  wire        mii_rx_clk,
    output wire        mdc,
    inout  wire        mdio,
    output wire        phy_rst_n
);
    wire [7:0] gmii_txd;
    wire       mdio_i, mdio_o, mdio_t;

    assign mii_txd   = gmii_txd[3:0];   // upper nibble unused at 100M MII
    assign phy_rst_n = 1'b1;            // hold PHY out of reset

    IOBUF mdio_iobuf (.I(mdio_o), .O(mdio_i), .T(mdio_t), .IO(mdio));

    phase_corr phase_corr_i (
        .DDR_addr(DDR_addr), .DDR_ba(DDR_ba), .DDR_cas_n(DDR_cas_n),
        .DDR_ck_n(DDR_ck_n), .DDR_ck_p(DDR_ck_p), .DDR_cke(DDR_cke),
        .DDR_cs_n(DDR_cs_n), .DDR_dm(DDR_dm), .DDR_dq(DDR_dq),
        .DDR_dqs_n(DDR_dqs_n), .DDR_dqs_p(DDR_dqs_p), .DDR_odt(DDR_odt),
        .DDR_ras_n(DDR_ras_n), .DDR_reset_n(DDR_reset_n), .DDR_we_n(DDR_we_n),
        .FIXED_IO_ddr_vrn(FIXED_IO_ddr_vrn), .FIXED_IO_ddr_vrp(FIXED_IO_ddr_vrp),
        .FIXED_IO_mio(FIXED_IO_mio), .FIXED_IO_ps_clk(FIXED_IO_ps_clk),
        .FIXED_IO_ps_porb(FIXED_IO_ps_porb), .FIXED_IO_ps_srstb(FIXED_IO_ps_srstb),
        .GMII_ETHERNET_0_0_col(1'b0),
        .GMII_ETHERNET_0_0_crs(1'b0),
        .GMII_ETHERNET_0_0_rx_clk(mii_rx_clk),
        .GMII_ETHERNET_0_0_rx_dv(mii_rx_dv),
        .GMII_ETHERNET_0_0_rx_er(1'b0),
        .GMII_ETHERNET_0_0_rxd({4'b0000, mii_rxd}),
        .GMII_ETHERNET_0_0_tx_clk(mii_tx_clk),
        .GMII_ETHERNET_0_0_tx_en(mii_tx_en),
        .GMII_ETHERNET_0_0_tx_er(),
        .GMII_ETHERNET_0_0_txd(gmii_txd),
        .MDIO_ETHERNET_0_0_mdc(mdc),
        .MDIO_ETHERNET_0_0_mdio_i(mdio_i),
        .MDIO_ETHERNET_0_0_mdio_o(mdio_o),
        .MDIO_ETHERNET_0_0_mdio_t(mdio_t)
    );
endmodule

`default_nettype wire
