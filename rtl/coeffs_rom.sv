`timescale 1ns / 1ps

module coeffs_rom (
    input  logic [4:0]  addr, // 0 to 19 (20 sections)
    output logic signed [23:0] b0,
    output logic signed [23:0] b1,
    output logic signed [23:0] b2,
    output logic signed [23:0] a1,
    output logic signed [23:0] a2
);

    // 20 rows, 120 bits wide (5 coeffs * 24 bits)
    // Packed format: {b0, b1, b2, a1, a2}
    (* syn_ramstyle = "block_ram" *)
    logic [119:0] rom [0:19];

    initial begin
        // Expects a file with 20 lines. Each line: b0 b1 b2 a1 a2 (in hex)
        $readmemh("fpga_coeff.mem", rom);
    end

    // Combinational Read
    logic [119:0] row_data;
    
    always_comb begin
        row_data = rom[addr];
        // Unpack
        b0 = row_data[119:96];
        b1 = row_data[95:72];
        b2 = row_data[71:48];
        a1 = row_data[47:24];
        a2 = row_data[23:0];
    end

endmodule