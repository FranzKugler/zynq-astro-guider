# Compile the FFT IP + a generated testbench and run xsim to completion.
# The testbench does its own file I/O (absolute paths baked in by the Python
# harness), so it just needs to run to $finish.
#
# Usage: vivado -mode batch -source run_fft_cosim.tcl -tclargs <xci> <tb.sv> <simdir>

set xci    [lindex $argv 0]
set tb     [lindex $argv 1]
set simdir [lindex $argv 2]
set PART xc7z020clg400-1

create_project -force fftsim $simdir -part $PART
read_ip $xci
generate_target simulation [get_ips]
add_files -fileset sim_1 $tb
update_compile_order -fileset sim_1
set_property top fft_tb [get_filesets sim_1]
set_property -name {xsim.simulate.runtime} -value {all} -objects [get_filesets sim_1]

launch_simulation
run all
# the testbench ends with $finish; just quit Vivado (close_sim can hang on the
# interactive xsim kernel that launch_simulation keeps alive).
exit
