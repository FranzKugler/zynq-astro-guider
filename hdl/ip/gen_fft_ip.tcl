# Generate the Xilinx FFT IP for the guider datapath.
#
# Config is derived from guider_golden.fixed_point (the bit spec): one 1-D FFT,
# time-shared for the row and column passes of the 2-D transform, with the
# corner-turn (guider_hdl.corner_turn) between passes.
#
#   transform_length  = N             FFT size (guide-frame side, power of two)
#   input_width       = mant_bits     FFT mantissa per component (model: 18)
#   phase_factor_width= twiddle_bits  twiddle precision           (model: 16)
#   scaling_options   = scaled         fixed per-stage scale schedule via SCALE_SCH
#   rounding_modes    = convergent    == model _round_shift "convergent"
#   output_ordering   = natural_order == model does input bit-reversal (DIT)
#
# The model is the spec only up to a tolerance: the IP's internal schedule is
# not reproduced bit-for-bit by the Python model (cosim against it is xsim-based
# and tolerance-checked).
#
# Usage:
#   vivado -mode batch -source gen_fft_ip.tcl -tclargs [N] [input_width] [phase_width] [outdir]
# Defaults: N=256 input_width=18 phase_width=16 outdir=build/fft_ip

set N      [lindex $argv 0]; if {$N      eq ""} {set N      256}
set IW     [lindex $argv 1]; if {$IW     eq ""} {set IW     18}
set PW     [lindex $argv 2]; if {$PW     eq ""} {set PW     16}
set OUTDIR [lindex $argv 3]; if {$OUTDIR eq ""} {set OUTDIR "build/fft_ip"}

set PART xc7z020clg400-1
set IPNAME fft_${N}

file mkdir $OUTDIR
create_project -in_memory -part $PART

create_ip -name xfft -vendor xilinx.com -library ip \
    -module_name $IPNAME -dir $OUTDIR

set_property -dict [list \
    CONFIG.transform_length                     $N \
    CONFIG.implementation_options               pipelined_streaming_io \
    CONFIG.run_time_configurable_transform_length false \
    CONFIG.data_format                          fixed_point \
    CONFIG.input_width                          $IW \
    CONFIG.phase_factor_width                   $PW \
    CONFIG.scaling_options                      scaled \
    CONFIG.rounding_modes                       convergent_rounding \
    CONFIG.output_ordering                      natural_order \
    CONFIG.aresetn                              true \
    CONFIG.complex_mult_type                    use_mults_resources \
    CONFIG.butterfly_type                       use_xtremedsp_slices \
] [get_ips $IPNAME]

generate_target {instantiation_template synthesis simulation} [get_ips $IPNAME]

# Uncomment to also synthesize the IP OOC (slow; needed before xsim cosim):
# synth_ip [get_ips $IPNAME]

puts "FFT IP '$IPNAME' generated under $OUTDIR (N=$N, input_width=$IW, phase_width=$PW, Scaled)."
