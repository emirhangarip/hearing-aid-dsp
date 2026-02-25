`timescale 1ns / 1ps

module biquad_core_tdm #(
    // Pipeline Latency configuration
    // 1 = Registered Output (Same as parallel)
    // 2 = Input Reg + Output Reg (Better for DSP inference)
    parameter int PIPELINE_STAGES = 2 
)(
    input  logic        clk,
    input  logic        rst_n,
    
    // TDM Control
    input  logic        en,           // Clock enable for pipeline
    
    // Data Paths (Q1.23)
    input  logic signed [23:0] sample_in, // x[n]
    input  logic signed [23:0] x_z1,      // x[n-1]
    input  logic signed [23:0] x_z2,      // x[n-2]
    input  logic signed [23:0] y_z1,      // y[n-1]
    input  logic signed [23:0] y_z2,      // y[n-2]
    
    // Coefficients (Q2.22)
    input  logic signed [23:0] b0, b1, b2, a1, a2,
    
    // Output
    output logic signed [23:0] y_out      // y[n]
);

    // ------------------------------------------------------------------
    // Stage 1: Multiplication (DSP Inference)
    // ------------------------------------------------------------------
    // Q1.23 * Q2.22 = Q3.45 (48-bit)
    logic signed [47:0] p_b0, p_b1, p_b2, p_a1, p_a2;
    
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            p_b0 <= '0; p_b1 <= '0; p_b2 <= '0;
            p_a1 <= '0; p_a2 <= '0;
        end else if (en) begin
            if (PIPELINE_STAGES >= 2) begin
                // Explicit input registering for DSP mapping efficiency
                p_b0 <= sample_in * b0;
                p_b1 <= x_z1 * b1;
                p_b2 <= x_z2 * b2;
                p_a1 <= y_z1 * a1;
                p_a2 <= y_z2 * a2;
            end
        end
    end

    // ------------------------------------------------------------------
    // Stage 2: Accumulation & Saturation
    // ------------------------------------------------------------------
    logic signed [47:0] acc_comb;
    logic signed [47:0] prod_b0_mux, prod_b1_mux, prod_b2_mux;
    logic signed [47:0] prod_a1_mux, prod_a2_mux;
    
    always_comb begin
        // Mux between registered products or direct multiplication
        // based on pipeline setting.
        if (PIPELINE_STAGES >= 2) begin
            prod_b0_mux = p_b0; prod_b1_mux = p_b1; prod_b2_mux = p_b2;
            prod_a1_mux = p_a1; prod_a2_mux = p_a2;
        end else begin
            prod_b0_mux = sample_in * b0;
            prod_b1_mux = x_z1 * b1;
            prod_b2_mux = x_z2 * b2;
            prod_a1_mux = y_z1 * a1;
            prod_a2_mux = y_z2 * a2;
        end

        // Summation: Direct Form I
        acc_comb = (prod_b0_mux + prod_b1_mux + prod_b2_mux) - (prod_a1_mux + prod_a2_mux);
    end

    // ------------------------------------------------------------------
    // Stage 3: Shift & Saturate (Registered Output)
    // ------------------------------------------------------------------
    localparam signed [47:0] MAX_POS = 48'sd8388607;
    localparam signed [47:0] MAX_NEG = -48'sd8388608;
    
    logic signed [47:0] y_shifted;
    
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            y_out <= '0;
        end else if (en) begin
            // 1. Shift (Truncate)
            y_shifted = acc_comb >>> 22;

            // 2. Saturate
            if (y_shifted > MAX_POS) 
                y_out <= 24'sd8388607;
            else if (y_shifted < MAX_NEG) 
                y_out <= -24'sd8388608;
            else 
                y_out <= y_shifted[23:0];
        end
    end

endmodule