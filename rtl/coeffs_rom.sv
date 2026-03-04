`timescale 1ns / 1ps

module coeffs_rom (
    input  logic [4:0]  addr, // 0 to 19 (20 sections)
    output logic signed [31:0] b0,
    output logic signed [31:0] b1,
    output logic signed [31:0] b2,
    output logic signed [31:0] a1,
    output logic signed [31:0] a2
);

    // 20 rows, 160 bits wide (5 coeffs * 32 bits, Q2.30)
    // Packed format: {b0, b1, b2, a1, a2}
    (* syn_ramstyle = "block_ram" *)
    logic [159:0] rom [0:19];

    initial begin
        // Expects a file with 20 lines. Each line: b0 b1 b2 a1 a2 (in hex)
        $readmemh("fpga_coeff.mem", rom);
    end

    // Combinational Read
    logic [159:0] row_data;
    
    always_comb begin
        row_data = rom[addr];
        // Unpack
        b0 = row_data[159:128];
        b1 = row_data[127:96];
        b2 = row_data[95:64];
        a1 = row_data[63:32];
        a2 = row_data[31:0];
    end

endmodule
