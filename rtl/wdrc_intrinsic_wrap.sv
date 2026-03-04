`timescale 1ns / 1ps

// WDRC-only wrapper for intrinsic attack/release characterization.
// Bypasses filterbank and ds_modulator: drives tdm_wdrc_10band directly.
module wdrc_intrinsic_wrap (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               sample_valid,
    input  logic signed [23:0] band_in_0,
    input  logic signed [23:0] band_in_1,
    input  logic signed [23:0] band_in_2,
    input  logic signed [23:0] band_in_3,
    input  logic signed [23:0] band_in_4,
    input  logic signed [23:0] band_in_5,
    input  logic signed [23:0] band_in_6,
    input  logic signed [23:0] band_in_7,
    input  logic signed [23:0] band_in_8,
    input  logic signed [23:0] band_in_9,
    output logic               out_valid,
    output logic signed [23:0] y_full
);

    logic signed [23:0] bands_in [0:9];
    logic signed [23:0] bands_out [0:9];

    assign bands_in[0] = band_in_0;
    assign bands_in[1] = band_in_1;
    assign bands_in[2] = band_in_2;
    assign bands_in[3] = band_in_3;
    assign bands_in[4] = band_in_4;
    assign bands_in[5] = band_in_5;
    assign bands_in[6] = band_in_6;
    assign bands_in[7] = band_in_7;
    assign bands_in[8] = band_in_8;
    assign bands_in[9] = band_in_9;

    tdm_wdrc_10band u_wdrc (
        .clk        (clk),
        .rst_n      (rst_n),
        .sample_valid(sample_valid),
        .band_in    (bands_in),
        .band_out   (bands_out),
        .y_full     (y_full),
        .out_valid  (out_valid),
        .lut_wr_en  (1'b0),
        .lut_wr_band(4'd0),
        .lut_wr_addr(10'd0),
        .lut_wr_data(24'sd0)
    );

endmodule
