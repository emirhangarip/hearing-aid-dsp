`timescale 1ns / 1ps
`default_nettype none

// Wrapper for cocotb HiFi suite:
//  - Presents ds_modulator-style ports (clk/rst_n/din/dout)
//  - Internally runs top_hearing_tdm -> ds_modulator
//  - Generates sample_valid from clk in either:
//      * integer-divider mode (legacy, deterministic period),
//      * fractional-N mode (exact long-term AUDIO_FS from arbitrary CLK_FREQ).
module hearing_tdm_pdm_wrap #(
    parameter int CLK_FREQ = 100_000_000,
    parameter int AUDIO_FS = 48_000,
    parameter bit FRACTIONAL_STROBE = 1'b0,
    parameter bit WARN_ON_TRUNCATION = 1'b1,
    parameter int DAC_BW   = 32,
    parameter int OSR      = 64
)(
    input  logic              clk,
    input  logic              rst_n,
    input  logic signed [31:0] din,
    output logic              dout
);

    localparam int unsigned SAMPLE_PERIOD = (CLK_FREQ / AUDIO_FS);
    localparam int unsigned SAMPLE_CNT_W  = (SAMPLE_PERIOD > 1) ? $clog2(SAMPLE_PERIOD) : 1;

    localparam logic [31:0] CLK_FREQ_U = CLK_FREQ;
    localparam logic [31:0] AUDIO_FS_U = AUDIO_FS;

    logic                    sample_valid;

    generate
        if (!FRACTIONAL_STROBE) begin : gen_integer_strobe
            // Legacy mode: fixed integer period (e.g. 100 MHz / 48 kHz -> 2083).
            logic [SAMPLE_CNT_W-1:0] sample_cnt;
            localparam logic [SAMPLE_CNT_W-1:0] SAMPLE_PERIOD_M1 =
                SAMPLE_PERIOD[SAMPLE_CNT_W-1:0] - 1'b1;

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    sample_cnt <= '0;
                end else if (sample_cnt == SAMPLE_PERIOD_M1) begin
                    sample_cnt <= '0;
                end else begin
                    sample_cnt <= sample_cnt + 1'b1;
                end
            end

            assign sample_valid = (sample_cnt == '0);
        end else begin : gen_fractional_strobe
            // Fractional-N mode:
            // phase_acc += AUDIO_FS each clk, emit pulse on overflow past CLK_FREQ.
            // Gives exact long-term sample rate with +/-1 clk period jitter.
            logic [31:0] phase_acc;
            logic        sample_valid_frac;
            logic [32:0] phase_sum;

            always_comb begin
                phase_sum = {1'b0, phase_acc} + {1'b0, AUDIO_FS_U};
            end

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    phase_acc         <= 32'd0;
                    sample_valid_frac <= 1'b1;
                end else begin
                    if (phase_sum >= {1'b0, CLK_FREQ_U}) begin
                        phase_acc         <= phase_sum[31:0] - CLK_FREQ_U;
                        sample_valid_frac <= 1'b1;
                    end else begin
                        phase_acc         <= phase_sum[31:0];
                        sample_valid_frac <= 1'b0;
                    end
                end
            end

            assign sample_valid = sample_valid_frac;
        end
    endgenerate

    initial begin
        if (CLK_FREQ <= 0 || AUDIO_FS <= 0) begin
            $error("hearing_tdm_pdm_wrap: CLK_FREQ and AUDIO_FS must be positive.");
        end
        if (WARN_ON_TRUNCATION && !FRACTIONAL_STROBE && (CLK_FREQ % AUDIO_FS) != 0) begin
            $warning("hearing_tdm_pdm_wrap: integer strobe truncates CLK_FREQ/AUDIO_FS (set FRACTIONAL_STROBE=1 for exact long-term rate).");
        end
    end

    // Direct PCM input (Q1.23 from top 24 bits)
    wire signed [23:0] tdm_audio_in = din[31:8];

    // WDRC LUT write interface tied off (unity gain defaults in RAM)
    wire               lut_wr_en   = 1'b0;
    wire [3:0]         lut_wr_band = 4'd0;
    wire [9:0]         lut_wr_addr = 10'd0;
    wire signed [23:0] lut_wr_data = 24'sd0;

    wire signed [23:0] tdm_audio_out;
    wire               tdm_out_valid;

    top_hearing_tdm u_tdm_core (
        .clk         (clk),
        .rst_n       (rst_n),
        .sample_valid(sample_valid),
        .audio_in    (tdm_audio_in),
        .lut_wr_en   (lut_wr_en),
        .lut_wr_band (lut_wr_band),
        .lut_wr_addr (lut_wr_addr),
        .lut_wr_data (lut_wr_data),
        .out_valid   (tdm_out_valid),
        .audio_out   (tdm_audio_out)
    );

    // Hold audio output until next valid sample
    logic [31:0] pcm_hold;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pcm_hold <= 32'd0;
        end else if (tdm_out_valid) begin
            pcm_hold <= {tdm_audio_out, 8'h00};
        end
    end

    ds_modulator #(
        .DAC_BW(DAC_BW),
        .OSR   (OSR)
    ) u_modulator (
        .clk (clk),
        .rst_n(rst_n),
        .din (pcm_hold),
        .dout(dout)
    );

endmodule

`default_nettype wire
