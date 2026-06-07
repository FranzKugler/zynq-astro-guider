# Block design for the phase-correlation PL on the Z7-Lite (XC7Z020).
#
# Topology (see ../docs/bitstream_integration.md):
#   PS7 -M_AXI_GP0-> SmartConnect -> AXI-Lite of {IP, DMA0, DMA1, switch_in, switch_out}
#   PS7 <-S_AXI_HP0- SmartConnect <- AXI-DMA (DMA0 MM2S+S2MM, DMA1 MM2S)
#   DMA0.MM2S, DMA1.MM2S -> switch_in -> IP s_axis_* (active kernel input(s))
#   IP m_axis_*          -> switch_out -> DMA0.S2MM
#   one FCLK_CLK0 (100 MHz), proc_sys_reset from FCLK_RESET0_N.
#
# The PS routes the switches and sets the kernel direction/shift per pass, then
# kicks the DMAs (register-direct mode). Frames live in PS DDR3 (u-dma-buf).
#
# Usage:
#   vivado -mode batch -source bd/create_bd.tcl -tclargs <mode> [N]
#     mode = bd   : create + validate the block design only (fast)
#            all  : ... then synth, impl, write_bitstream + .xsa  (long)
# Run guider_hdl.build first to emit build/rtl/phase_correlator_top.v.

set MODE [lindex $argv 0]; if {$MODE eq ""} {set MODE "bd"}
set N    [lindex $argv 1]; if {$N    eq ""} {set N    256}
# PL clock. The phase-only CORDIC + cross-power are combinational (deep paths),
# so the datapath closes only at a low FCLK (~8 MHz) until they are pipelined.
# 6 MHz gives margin and is plenty for guide-frame rates (~10 fps at N=256).
set FREQ_MHZ [lindex $argv 2]; if {$FREQ_MHZ eq ""} {set FREQ_MHZ 6}

set PART     xc7z020clg400-1
set DESIGN   phase_corr
set ROOT     [file normalize [file dirname [info script]]/..]
set PROJDIR  $ROOT/hdl/build/vivado_bd
set RTL_TOP     $ROOT/hdl/build/rtl/phase_correlator_top.v
set RTL_WRAP    $ROOT/bd/phase_correlator_axi.v
set RTL_ABSORB  $ROOT/bd/stale_absorber.v
set TDATA_BYTES 16                                   ;# 128-bit uniform AXIS

if {![file exists $RTL_TOP]} {
    error "missing $RTL_TOP -- run: python -m guider_hdl.build hdl/build/rtl $N"
}

file mkdir $PROJDIR
create_project -force ${DESIGN} $PROJDIR -part $PART

# --- the Amaranth top + the AXI rename wrapper + the stale absorber as RTL sources ---
add_files -norecurse [list $RTL_TOP $RTL_WRAP $RTL_ABSORB]

# --- the Xilinx FFT IP the top instantiates as the black box fft_<N> ---
# Only needed for synthesis (MODE=all): validate_bd treats fft_<N> as an
# undefined sub-module deep inside the RTL reference, so BD-only runs skip the
# slow IP generation.
set IPNAME fft_${N}
if {$MODE eq "all"} {
    create_ip -name xfft -vendor xilinx.com -library ip -module_name $IPNAME
    set_property -dict [list \
        CONFIG.transform_length                     $N \
        CONFIG.implementation_options               pipelined_streaming_io \
        CONFIG.data_format                          fixed_point \
        CONFIG.input_width                          18 \
        CONFIG.phase_factor_width                   16 \
        CONFIG.scaling_options                      block_floating_point \
        CONFIG.rounding_modes                       convergent_rounding \
        CONFIG.output_ordering                      natural_order \
        CONFIG.aresetn                              true \
        CONFIG.complex_mult_type                    use_mults_resources \
        CONFIG.butterfly_type                       use_xtremedsp_slices \
    ] [get_ips $IPNAME]
    generate_target {synthesis simulation} [get_ips $IPNAME]
}
update_compile_order -fileset sources_1

# ---------------------------------------------------------------------------
create_bd_design $DESIGN

# --- Processing System 7 (no board preset; enable the ports we use) ---
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7]
# make FIXED_IO + DDR external as DDR_*/FIXED_IO_* (the custom top passes them
# through); no board preset, no auto AXI. Do this before the config below.
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "0" \
             Master "Disable" Slave "Disable"} $ps
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0          {1} \
    CONFIG.PCW_USE_S_AXI_HP0          {1} \
    CONFIG.PCW_EN_CLK0_PORT           {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $FREQ_MHZ \
    CONFIG.PCW_FCLK_CLK0_BUF          {TRUE} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT   {0} \
    CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_ENET0_ENET0_IO          {EMIO} \
    CONFIG.PCW_ENET0_GRP_MDIO_ENABLE   {1} \
    CONFIG.PCW_ENET0_GRP_MDIO_IO       {EMIO} \
] $ps

# ENET0 on EMIO -> the bank-35 RTL8201F MII (proven in create_enet_test.tcl); a
# custom top (phase_corr_top.v) maps GMII to MII so loading this bitstream keeps
# the board's macb/ssh alive. See full-bitstream-drops-ethernet memory.
make_bd_intf_pins_external [get_bd_intf_pins ps7/GMII_ETHERNET_0]
make_bd_intf_pins_external [get_bd_intf_pins ps7/MDIO_ETHERNET_0]

set fclk   [get_bd_pins ps7/FCLK_CLK0]
set gp0    [get_bd_intf_pins ps7/M_AXI_GP0]
set hp0    [get_bd_intf_pins ps7/S_AXI_HP0]

# --- reset ---
set rstgen [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst0]
connect_bd_net $fclk [get_bd_pins rst0/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst0/ext_reset_in]
set arstn  [get_bd_pins rst0/peripheral_aresetn]

# --- the phase-correlation IP (RTL module) ---
set ip [create_bd_cell -type module -reference phase_correlator_axi pc]

# --- DMAs (register-direct mode, 128-bit stream, 64-bit MM AXI to HP) ---
proc mk_dma {name s2mm} {
    set d [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma $name]
    set cfg [list \
        CONFIG.c_include_sg              {0} \
        CONFIG.c_sg_include_stscntrl_strm {0} \
        CONFIG.c_include_mm2s            {1} \
        CONFIG.c_include_s2mm            $s2mm \
        CONFIG.c_sg_length_width         {26} \
        CONFIG.c_m_axis_mm2s_tdata_width {128} \
        CONFIG.c_m_axi_mm2s_data_width   {128} \
        CONFIG.c_mm2s_burst_size         {16}]
    if {$s2mm} {
        lappend cfg \
            CONFIG.c_s_axis_s2mm_tdata_width {128} \
            CONFIG.c_m_axi_s2mm_data_width   {128} \
            CONFIG.c_s2mm_burst_size         {16}
    }
    set_property -dict $cfg $d
    return $d
}
mk_dma dma0 1            ;# MM2S + S2MM
mk_dma dma1 0            ;# MM2S only

# --- AXIS switches (uniform 128-bit, AXI-Lite controlled routing) ---
proc mk_switch {name nsi nmi} {
    set s [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_switch $name]
    set_property -dict [list \
        CONFIG.NUM_SI            $nsi \
        CONFIG.NUM_MI            $nmi \
        CONFIG.ROUTING_MODE      {1} \
        CONFIG.TDATA_NUM_BYTES   {16} \
        CONFIG.HAS_TLAST         {1} \
        CONFIG.DECODER_REG       {1} \
    ] $s
    return $s
}
mk_switch sw_in  2 6     ;# 2 MM2S -> 6 kernel inputs
mk_switch sw_out 4 1     ;# 4 kernel outputs -> 1 S2MM

# --- control bus: PS GP0 -> SmartConnect -> all AXI-Lite slaves ---
set scc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_ctrl]
set_property CONFIG.NUM_SI {1} $scc
set_property CONFIG.NUM_MI {5} $scc
connect_bd_intf_net $gp0 [get_bd_intf_pins smc_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M00_AXI] [get_bd_intf_pins pc/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M01_AXI] [get_bd_intf_pins dma0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M02_AXI] [get_bd_intf_pins dma1/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M03_AXI] [get_bd_intf_pins sw_in/S_AXI_CTRL]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M04_AXI] [get_bd_intf_pins sw_out/S_AXI_CTRL]

# --- memory bus: all DMA masters -> SmartConnect -> PS HP0 ---
set scm [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_mem]
set_property CONFIG.NUM_SI {3} $scm
set_property CONFIG.NUM_MI {1} $scm
connect_bd_intf_net [get_bd_intf_pins dma0/M_AXI_MM2S] [get_bd_intf_pins smc_mem/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins dma0/M_AXI_S2MM] [get_bd_intf_pins smc_mem/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins dma1/M_AXI_MM2S] [get_bd_intf_pins smc_mem/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_mem/M00_AXI] $hp0

# --- data path: MM2S -> sw_in -> kernel inputs ---
connect_bd_intf_net [get_bd_intf_pins dma0/M_AXIS_MM2S] [get_bd_intf_pins sw_in/S00_AXIS]
connect_bd_intf_net [get_bd_intf_pins dma1/M_AXIS_MM2S] [get_bd_intf_pins sw_in/S01_AXIS]
set sw_in_dst {window_sample window_coef fft_in xpower_f xpower_g rescale_r}
set i 0
foreach k $sw_in_dst {
    connect_bd_intf_net [get_bd_intf_pins sw_in/M0${i}_AXIS] \
        [get_bd_intf_pins pc/S_AXIS_[string toupper $k]]
    incr i
}

# --- data path: kernel outputs -> sw_out -> S2MM ---
set sw_out_src {window_out fft_out xpower_r rescale_p}
set i 0
foreach k $sw_out_src {
    connect_bd_intf_net [get_bd_intf_pins pc/M_AXIS_[string toupper $k]] \
        [get_bd_intf_pins sw_out/S0${i}_AXIS]
    incr i
}
# --- stale absorber between sw_out M00_AXIS and dma0 S_AXIS_S2MM ---
# The AXIS switch SRL is not reset by aresetn; its tail content from the
# previous frame appears as a 2-4 beat stale prefix on the next frame.
# skip_n (from CSR CTRL[10:7]) is loaded one cycle after each CTRL write;
# the absorber silently drains those beats before the DMA sees them.
set absorber [create_bd_cell -type module -reference stale_absorber absorber]
connect_bd_intf_net [get_bd_intf_pins sw_out/M00_AXIS]  [get_bd_intf_pins absorber/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins absorber/M_AXIS]  [get_bd_intf_pins dma0/S_AXIS_S2MM]

# --- clocks + resets to everything ---
foreach p {pc/aclk dma0/s_axi_lite_aclk dma0/m_axi_mm2s_aclk dma0/m_axi_s2mm_aclk \
           dma1/s_axi_lite_aclk dma1/m_axi_mm2s_aclk \
           sw_in/aclk sw_in/s_axi_ctrl_aclk sw_out/aclk sw_out/s_axi_ctrl_aclk \
           smc_ctrl/aclk smc_mem/aclk ps7/M_AXI_GP0_ACLK ps7/S_AXI_HP0_ACLK \
           absorber/aclk} {
    connect_bd_net $fclk [get_bd_pins $p]
}
foreach p {pc/aresetn dma0/axi_resetn dma1/axi_resetn \
           sw_in/s_axi_ctrl_aresetn sw_out/s_axi_ctrl_aresetn \
           smc_ctrl/aresetn smc_mem/aresetn \
           absorber/aresetn} {
    connect_bd_net $arstn [get_bd_pins $p]
}
# The AXIS switches' DATA-path reset comes from a dedicated proc_sys_reset driven
# by CTRL.dpath_reset (pc/dpath_reset, active-high -> mb_debug_sys_rst), so the PS
# can flush the switch's stale-beat prefix per frame with a SYNCHRONOUS, stretched
# reset (a bare combinational gate would be an async reset -- BD 41-1347). It also
# resets on the global FCLK_RESET0_N. Their control-plane (s_axi_ctrl_aresetn)
# stays on the global reset so routing writes work while the data path is flushed.
set rst_dpath [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_dpath]
connect_bd_net $fclk [get_bd_pins rst_dpath/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst_dpath/ext_reset_in]
connect_bd_net [get_bd_pins pc/dpath_reset] [get_bd_pins rst_dpath/mb_debug_sys_rst]
connect_bd_net [get_bd_pins rst_dpath/peripheral_aresetn] \
    [get_bd_pins sw_in/aresetn] [get_bd_pins sw_out/aresetn]

# CSR -> absorber skip control
connect_bd_net [get_bd_pins pc/o_skip_n]    [get_bd_pins absorber/skip_n]
connect_bd_net [get_bd_pins pc/o_skip_load] [get_bd_pins absorber/load]

assign_bd_address

# optional: System ILA on dma0.M_AXI_MM2S to capture the failing read transaction
# (DECERR debug). tclargs[3] == "ila". Produces a .ltx probes file.
if {[lindex $argv 3] eq "ila"} {
    set rd_net [get_bd_intf_nets -of [get_bd_intf_pins dma0/M_AXI_MM2S]]
    apply_bd_automation -rule xilinx.com:bd_rule:debug -dict [list \
        $rd_net [list AXI_R_ADDR "Data and Trigger" AXI_R_DATA "Data and Trigger" \
                      AXI_W_ADDR "Data" AXI_W_DATA "Data" AXI_B "Data and Trigger" \
                      CLK_SRC "/ps7/FCLK_CLK0" SYSTEM_ILA "Auto" APC_EN "0"]]
    puts "System ILA added on dma0/M_AXI_MM2S."
}

regenerate_bd_layout
save_bd_design
validate_bd_design
puts "BD '$DESIGN' created and validated."

if {$MODE eq "all"} {
    # custom top (phase_corr_top.v) wraps the BD: maps GMII EMIO -> bank-35 MII +
    # MDIO IOBUF + DDR/FIXED_IO passthrough, so the datapath bitstream keeps Ethernet.
    generate_target synthesis [get_files $PROJDIR/${DESIGN}.srcs/sources_1/bd/${DESIGN}/${DESIGN}.bd]
    add_files -norecurse [list $ROOT/bd/phase_corr_top.v $ROOT/bd/enet_test_pins.xdc]
    set_property top phase_corr_top [current_fileset]
    update_compile_order -fileset sources_1
    launch_runs impl_1 -to_step write_bitstream -jobs 8
    wait_on_run impl_1
    write_hw_platform -fixed -include_bit -force $PROJDIR/${DESIGN}.xsa
    set bit $PROJDIR/${DESIGN}.runs/impl_1/phase_corr_top.bit
    puts "bitstream ([file exists $bit]) + .xsa written under $PROJDIR"
}
