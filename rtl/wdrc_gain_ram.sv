`timescale 1ns / 1ps

module wdrc_gain_ram #(
    parameter int BANDS          = 10,
    parameter int ADDR_WIDTH     = 10,    // 1024 entries per band
    parameter int DATA_WIDTH     = 24,
    parameter bit INIT_FROM_FILE = 1'b1   // Try to load file, but default to Unity
)(
    input  logic                         clk,

    // Read port (used by WDRC core)
    input  logic [ADDR_WIDTH-1:0]        rd_addr,
    input  logic [$clog2(BANDS)-1:0]     rd_band,
    output logic [DATA_WIDTH-1:0]        rd_data,

    // Write port (from SPI loader)
    input  logic                         wr_en,
    input  logic [ADDR_WIDTH-1:0]        wr_addr,
    input  logic [$clog2(BANDS)-1:0]     wr_band,
    input  logic [DATA_WIDTH-1:0]        wr_data
);

    // -------------------------------------------------------------
    // Flattened memory dimensions
    // -------------------------------------------------------------
    localparam int DEPTH          = (1 << ADDR_WIDTH);
    localparam int TOT_DEPTH      = DEPTH * BANDS;
    localparam int TOT_ADDR_WIDTH = ADDR_WIDTH + $clog2(BANDS);

    // Infer Block RAM
    (* syn_ramstyle = "block_ram" *)
    logic [DATA_WIDTH-1:0] mem [0:TOT_DEPTH-1];

    logic [TOT_ADDR_WIDTH-1:0] rd_index;
    logic [TOT_ADDR_WIDTH-1:0] wr_index;

    // Address Calculation
    always_comb begin
        rd_index = {rd_band, rd_addr};
        wr_index = {wr_band, wr_addr};
    end

    // -------------------------------------------------------------
    // ROBUST INITIALIZATION (Crucial for avoiding silence)
    // -------------------------------------------------------------
    // Q4.20 Unity Gain (1.0) = 2^20 = 1048576 = 0x100000
    localparam logic [23:0] UNITY_GAIN = 24'h100000;

    initial begin
        int i;
        // 1. Default to Unity Gain (Passthrough)
        //    This ensures audio works even if SPI/File load fails.
        for (i = 0; i < TOT_DEPTH; i = i + 1) begin
            mem[i] = UNITY_GAIN;
        end

        // 2. Optional: Overwrite with .mem files if they exist
        //    (This works in Vivado Synthesis)
        if (INIT_FROM_FILE) begin
            // Note: If synthesis fails to find these files, it may
            // keep the UNITY_GAIN values or throw a warning. 
            // Ensure files exist in project root if you rely on them.
            // Example for band 0:
            // $readmemh("wdrc_gain_lut_b0.mem", mem, 0, 1023);
        end
    end

    // -------------------------------------------------------------
    // Synchronous Read / Write
    // -------------------------------------------------------------
    always_ff @(posedge clk) begin
        // Read Port
        rd_data <= mem[rd_index];

        // Write Port (SPI overwrites defaults)
        if (wr_en) begin
            mem[wr_index] <= wr_data;
        end
    end

endmodule