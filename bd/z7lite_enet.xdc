# Z7-Lite RTL8201F Ethernet PHY — bank-35 MII pin map (LVCMOS33).
# Extracted from the salvaged linux_sd design (constrs/ethernet.xdc), which used
# an AXI EthernetLite soft MAC. The pins are the physical board wiring and apply
# regardless of MAC choice (ethernetlite vs PS-GEM EMIO); only the get_ports
# names change to match the chosen top-level port names.
#
# This bitstream MUST drive these pins (some MAC -> PHY), or loading it kills the
# board's network (Ethernet is in the PL). See docs/bitstream_integration.md and
# the full-bitstream-drops-ethernet memory.
#
# signal            pin    | signal            pin
# ----------------- ------ | ----------------- ------
# mii txd[0]        M14    | mii rxd[0]        J14
# mii txd[1]        L15    | mii rxd[1]        K14
# mii txd[2]        M15    | mii rxd[2]        M18
# mii txd[3]        N15    | mii rxd[3]        M17
# mii tx_en         N16    | mii rx_dv         K18
# mii tx_clk        L14    | mii rx_clk        K17
# mdc               G14    | mdio              J15
# phy reset_n       H20    | sys_clk (50 MHz)  N18
#
# Below: the constraints as-is from linux_sd (axi_ethernetlite port names). For a
# PS-GEM EMIO build, reuse the PACKAGE_PIN values with the GEM EMIO port names.

set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_rtl_0_txd[0]}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_rtl_0_txd[1]}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_rtl_0_txd[2]}]
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_rtl_0_txd[3]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports mii_rtl_0_tx_en]
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33}           [get_ports mii_rtl_0_tx_clk]
set_property -dict {PACKAGE_PIN J14 IOSTANDARD LVCMOS33}           [get_ports {mii_rtl_0_rxd[0]}]
set_property -dict {PACKAGE_PIN K14 IOSTANDARD LVCMOS33}           [get_ports {mii_rtl_0_rxd[1]}]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33}           [get_ports {mii_rtl_0_rxd[2]}]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33}           [get_ports {mii_rtl_0_rxd[3]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVCMOS33}           [get_ports mii_rtl_0_rx_dv]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD LVCMOS33}           [get_ports mii_rtl_0_rx_clk]
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports mdio_rtl_0_mdc]
set_property -dict {PACKAGE_PIN J15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports mdio_rtl_0_mdio_io]
set_property -dict {PACKAGE_PIN H20 IOSTANDARD LVCMOS33 SLEW SLOW} [get_ports {reset_rtl_0[0]}]
set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVCMOS33}           [get_ports sys_clk]
