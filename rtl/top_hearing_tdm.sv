`timescale 1ns / 1ps

module top_hearing_tdm (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               sample_valid, // 48 kHz strobe
    input  logic signed [23:0] audio_in,     // Q1.23 input
    
    input  logic              lut_wr_en,
    input  logic [3:0]        lut_wr_band,   // 0..9
    input  logic [9:0]        lut_wr_addr,   // 0..1023
    input  logic signed [23:0] lut_wr_data,   // Q4.20 in 24-bit container

    output logic               out_valid,    // 1-cycle strobe
    output logic signed [23:0] audio_out     // Q1.23 output
);

// ------------------------------------------------------------
// BYPASS_ALL: completely ignore filterbank + WDRC + bands
// ------------------------------------------------------------

// ------------ Filterbank wiring ------------
logic signed [23:0] bands_from_fb [0:9];
logic               fb_done_valid;
logic signed [23:0] wdrc_bands_out[0:9];
           
tdm_filterbank_10band u_tdm_fb (
    .clk         (clk),
    .rst_n       (rst_n),
    .sample_valid(sample_valid),
    .sample_in   (audio_in),
    .band_out    (bands_from_fb),
    .band_valid  (fb_done_valid)
);

 // ------------ WDRC wiring ------------

tdm_wdrc_10band u_tdm_wdrc (
    .clk         (clk),
    .rst_n       (rst_n),
    .sample_valid(fb_done_valid),
    .band_in     (bands_from_fb),
    .lut_wr_en   (lut_wr_en),
    .lut_wr_band (lut_wr_band),   // 0..9
    .lut_wr_addr (lut_wr_addr),   // 0..1023
    .lut_wr_data (lut_wr_data),   // Q4.20 in 24-bit container
    .band_out    (wdrc_bands_out),
    .y_full      (audio_out),
    .out_valid   (out_valid)
);


endmodule
