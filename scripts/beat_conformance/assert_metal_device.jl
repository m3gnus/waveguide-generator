# HBB's independent integer-kernel control; no solver output can satisfy it.
using Metal
Metal.functional() || error("Metal is not functional")
function double!(output, input)
    index = thread_position_in_grid_1d()
    @inbounds output[index] = 2 * input[index]
    return nothing
end
input = MtlArray(Int32.(1:256))
output = Metal.zeros(Int32, 256)
Metal.@sync @metal threads=256 double!(output, input)
Array(output) == 2 .* Int32.(1:256) || error("Metal kernel returned the wrong answer")
println("WG_DEVICE=", Metal.device())
println("WG_KERNEL_VERIFIED=true")
