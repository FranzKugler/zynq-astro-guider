# Minimal GEM-EMIO Ethernet test design (no datapath) for the Z7-Lite.
#
# Goal: prove the board's macb (PS-GEM) keeps working when we load a bitstream we
# built ourselves -- i.e. that our GEM0-EMIO -> RTL8201F MII routing is correct --
# BEFORE integrating it with the slow phase-correlation build. Just PS7 with ENET0
# on EMIO; a thin Verilog top maps the GMII EMIO to the bank-35 MII pins.
#
# Usage:
#   vivado -mode batch -source bd/create_enet_test.tcl -tclargs <mode>
#     wrap : create BD + ENET0 EMIO external + generate wrapper (fast; inspect ports)
#     all  : ... + add top.v + XDC + synth/impl/bitstream
#
# Stage 'wrap' first so we can read the generated GMII/MDIO port names and write
# bd/enet_test_top.v to match.

set MODE [lindex $argv 0]; if {$MODE eq ""} {set MODE "wrap"}
set PART    xc7z020clg400-1
set DESIGN  enet_test
set ROOT    [file normalize [file dirname [info script]]/..]
set PROJDIR $ROOT/hdl/build/vivado_enet

file mkdir $PROJDIR
create_project -force $DESIGN $PROJDIR -part $PART

create_bd_design $DESIGN
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "0" \
             Master "Disable" Slave "Disable"} $ps
set_property -dict [list \
    CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_ENET0_ENET0_IO          {EMIO} \
    CONFIG.PCW_ENET0_GRP_MDIO_ENABLE   {1} \
    CONFIG.PCW_ENET0_GRP_MDIO_IO       {EMIO} \
    CONFIG.PCW_USE_M_AXI_GP0           {0} \
    CONFIG.PCW_EN_CLK0_PORT            {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50} \
] $ps

# expose the ENET0 GMII + MDIO to the BD boundary
make_bd_intf_pins_external [get_bd_intf_pins ps7/GMII_ETHERNET_0]
make_bd_intf_pins_external [get_bd_intf_pins ps7/MDIO_ETHERNET_0]

regenerate_bd_layout
save_bd_design
validate_bd_design
puts "ENET test BD created + validated."

set bd_file [get_files $PROJDIR/${DESIGN}.srcs/sources_1/bd/${DESIGN}/${DESIGN}.bd]

if {$MODE eq "wrap"} {
    make_wrapper -files $bd_file -top
    puts "WRAPPER: $PROJDIR/${DESIGN}.gen/sources_1/bd/${DESIGN}/hdl/${DESIGN}_wrapper.v"
}

if {$MODE eq "all"} {
    generate_target synthesis [get_files $bd_file]
    add_files -norecurse [list $ROOT/bd/enet_test_top.v $ROOT/bd/enet_test_pins.xdc]
    set_property top enet_test_top [current_fileset]
    update_compile_order -fileset sources_1
    launch_runs impl_1 -to_step write_bitstream -jobs 8
    wait_on_run impl_1
    set bit $PROJDIR/${DESIGN}.runs/impl_1/enet_test_top.bit
    puts "ENET test bitstream: $bit ([file exists $bit])"
}
