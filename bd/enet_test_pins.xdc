# Pin + clock constraints for enet_test_top (RTL8201F 100M MII, bank 35).
# Pins from the salvaged linux_sd ethernet.xdc; port names match enet_test_top.

set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_txd[0]}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_txd[1]}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_txd[2]}]
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {mii_txd[3]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports mii_tx_en]
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33}           [get_ports mii_tx_clk]
set_property -dict {PACKAGE_PIN J14 IOSTANDARD LVCMOS33}           [get_ports {mii_rxd[0]}]
set_property -dict {PACKAGE_PIN K14 IOSTANDARD LVCMOS33}           [get_ports {mii_rxd[1]}]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33}           [get_ports {mii_rxd[2]}]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33}           [get_ports {mii_rxd[3]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVCMOS33}           [get_ports mii_rx_dv]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD LVCMOS33}           [get_ports mii_rx_clk]
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports mdc]
set_property -dict {PACKAGE_PIN J15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports mdio]
set_property -dict {PACKAGE_PIN H20 IOSTANDARD LVCMOS33 SLEW SLOW} [get_ports phy_rst_n]

# MII clocks from the PHY: 25 MHz at 100 Mbps
create_clock -name mii_tx_clk -period 40.000 [get_ports mii_tx_clk]
create_clock -name mii_rx_clk -period 40.000 [get_ports mii_rx_clk]
