`timescale 1ns / 1ps
`default_nettype none

module hearing_core #(
    parameter DAC_BW   = 32,
    parameter OSR      = 64,
    parameter AUDIO_FS = 48000,       // Audio sampling frequency in Hz
    parameter CLK_FREQ = 100000000    // 100 MHz modulator clock
)(
    // System
    input  wire clk,           // 100 MHz modulator clock
    input  wire rst_n,         // Active-low reset
    
    // I2S inputs
    input  wire i2s_sck,
    input  wire i2s_ws,
    input  wire i2s_sd,
    
    // SPI inputs
    input  wire spi_sclk,
    input  wire spi_cs_n,
    input  wire spi_mosi,
    
    // Outputs
    output wire pdm_out_left,
    output wire pdm_out_right
);

    // ============================================================
    // 1) SPI Receiver (WDRC Coefficients Loader)
    // ============================================================
    logic        lut_wr_en;
    logic [3:0]  lut_wr_band;
    logic [9:0]  lut_wr_addr;
    logic [23:0] lut_wr_data;
    
    spi_wdrc_loader #(
        .BANDS(10),
        .ADDR_WIDTH(10),
        .DATA_WIDTH(24)
    ) loader (
        .clk(clk),
        .rst_n(rst_n),
        .spi_sclk(spi_sclk),
        .spi_cs_n(spi_cs_n),
        .spi_mosi(spi_mosi),
        .wr_en(lut_wr_en),
        .wr_band(lut_wr_band),
        .wr_addr(lut_wr_addr),
        .wr_data(lut_wr_data)
    );
    
    // ============================================================
    // 2) I2S Receiver
    // ============================================================
    wire [31:0] pcm_left;
    wire [31:0] pcm_right;
    wire        left_ready;
    wire        right_ready;
    
    i2s_receiver i2s_rx (
        .clk        (clk),
        .rst_n      (rst_n),
        .i2s_sck    (i2s_sck),
        .i2s_ws     (i2s_ws),
        .i2s_sd     (i2s_sd),
        .pcm_left   (pcm_left),
        .pcm_right  (pcm_right),
        .left_ready (left_ready),
        .right_ready(right_ready)
    );

    // ============================================================
    // 3) Robust Data Capture
    // ============================================================
    // Removed the 3-stage synchronizers. Since i2s_rx uses the same 
    // 'clk', we must latch data immediately when 'ready' is high.
    
    reg [31:0] pcm_left_hold;
    reg [31:0] pcm_right_hold;
    
    // Pulse Generation for TDM Core
    reg right_ready_prev;
    wire tdm_sample_valid;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pcm_left_hold    <= 32'd0;
            pcm_right_hold   <= 32'd0;
            right_ready_prev <= 1'b0;
        end else begin
            // Latch Left Data immediately when valid
            if (left_ready) begin
                pcm_left_hold <= pcm_left;
            end

            // Latch Right Data immediately when valid
            if (right_ready) begin
                pcm_right_hold <= pcm_right;
            end
            
            // Track previous state for edge detection
            right_ready_prev <= right_ready;
        end
    end

    // Create a single-cycle pulse on the rising edge of right_ready
    // This drives the TDM core state machine.
    assign tdm_sample_valid = right_ready && !right_ready_prev;

    // ============================================================
    // 4) LEFT Channel: I2S -> PDM (Passthrough)
    // ============================================================
    ds_modulator #(
        .DAC_BW(DAC_BW),
        .OSR   (OSR)
    ) ds_left (
        .clk (clk),
        .rst_n(rst_n),
        .din (pcm_left_hold),
        .dout(pdm_out_left)
    );

    // ============================================================
    // 5) RIGHT Channel: I2S -> TDM Processing -> PDM
    // ============================================================
    
    // 5.1 Format Conversion: 32-bit I2S -> 24-bit Q1.23
    // Taking the top 24 bits ensures MSB alignment.
    wire signed [23:0] tdm_audio_in;
    assign tdm_audio_in = pcm_right_hold[31:8];

    // 5.2 TDM Core Instantiation
    wire signed [23:0] tdm_audio_out;
    wire               tdm_out_valid;

    top_hearing_tdm tdm_core (
        .clk         (clk),
        .rst_n       (rst_n),
        .sample_valid(tdm_sample_valid), // Clean 1-cycle pulse
        .audio_in    (tdm_audio_in),
        
        // LUT / SPI Interface
        .lut_wr_en   (lut_wr_en),
        .lut_wr_band (lut_wr_band),
        .lut_wr_addr (lut_wr_addr),
        .lut_wr_data (lut_wr_data),
        
        // Output
        .out_valid   (tdm_out_valid),
        .audio_out   (tdm_audio_out)
    );

    // 5.3 Capture TDM Output
    reg [31:0] pcm_tdm_hold;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pcm_tdm_hold <= 32'd0;
        end else if (tdm_out_valid) begin
            // Pad the 24-bit result back to 32-bit for the PDM modulator
            pcm_tdm_hold <= {tdm_audio_out, 8'h00};
        end
    end

    // 5.4 RIGHT PDM Modulator
    ds_modulator #(
        .DAC_BW(DAC_BW),
        .OSR   (OSR)
    ) ds_right (
        .clk (clk),
        .rst_n(rst_n),
        .din (pcm_tdm_hold),
        .dout(pdm_out_right)
    );

endmodule
`default_nettype wire