module i2s_receiver (
    input  wire        clk,         // FPGA system clock
    input  wire        rst_n,       // Active-low reset

    input  wire        i2s_sck,     // I2S Serial Clock (from master)
    input  wire        i2s_ws,      // I2S Word Select (Left/Right)
    input  wire        i2s_sd,      // I2S Serial Data

    output reg [31:0]  pcm_left,    // Captured left channel
    output reg [31:0]  pcm_right,   // Captured right channel
    output reg         left_ready, // High for 1 clk when left data valid
    output reg         right_ready // High for 1 clk when right data valid
);

    // 1) Synchronize external I2S signals into clk domain
    reg [2:0] sck_sync, ws_sync, sd_sync;
    always @(posedge clk) begin
        sck_sync <= {sck_sync[1:0], i2s_sck};
        ws_sync  <= {ws_sync[1:0], i2s_ws};
        sd_sync  <= {sd_sync[1:0], i2s_sd};
    end

    wire sck_syncd = sck_sync[2];
    wire ws_syncd  = ws_sync[2];
    wire sd_syncd  = sd_sync[2];

    // 2) Edge detection on SCK
    reg sck_prev;
    wire sck_rising = (sck_syncd && !sck_prev);
    always @(posedge clk) begin
        sck_prev <= sck_syncd;
    end

    // 3) Word Select tracking
    reg ws_prev;
    wire ws_edge = (ws_syncd != ws_prev);

    // 4) Shift register and control
    reg [31:0] shift_reg;
    reg [5:0]  bit_cnt = 6'd31;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shift_reg   <= 32'd0;
            bit_cnt     <= 6'd31;
            pcm_left    <= 32'd0;
            pcm_right   <= 32'd0;
            left_ready  <= 1'b0;
            right_ready <= 1'b0;
            ws_prev     <= 1'b0;
        end else begin
            left_ready  <= 1'b0;
            right_ready <= 1'b0;

            if (sck_rising) begin
                // Track WS for channel edge detection
                ws_prev <= ws_syncd;

                // Shift in new SD bit
                shift_reg <= {shift_reg[30:0], sd_syncd};

                // Bit counter: reset on WS edge or complete word
                if (ws_edge || bit_cnt == 0) begin
                    bit_cnt <= 6'd31;
                end else begin
                    bit_cnt <= bit_cnt - 1'b1;
                end

                // Capture when full word received
                if (bit_cnt == 0) begin
                    if (!ws_syncd) begin
                        pcm_left   <= {shift_reg[30:0], sd_syncd};
                        left_ready <= 1'b1;
                    end else begin
                        pcm_right   <= {shift_reg[30:0], sd_syncd};
                        right_ready <= 1'b1;
                    end
                end
            end
        end
    end

endmodule
