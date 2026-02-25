// spi_wdrc_loader.sv
module spi_wdrc_loader #(
    parameter int BANDS      = 10,
    parameter int ADDR_WIDTH = 10,
    parameter int DATA_WIDTH = 24
) (
    input  logic                     clk,       // system clock (e.g., 100 MHz)
    input  logic                     rst_n,

    // SPI pins (Mode 0)
    input  logic                     spi_sclk,
    input  logic                     spi_cs_n,
    input  logic                     spi_mosi,
    //output logic                     spi_miso,  // not used yet

    // Write pulse to LUT RAM (synchronous to clk)
    output logic                     wr_en,
    output logic [$clog2(BANDS)-1:0] wr_band,
    output logic [ADDR_WIDTH-1:0]    wr_addr,
    output logic [DATA_WIDTH-1:0]    wr_data
);
    // --------------------------------------------------
    // Synchronize SCLK and CS_N into clk domain
    // --------------------------------------------------
    logic sclk_sync0, sclk_sync1;
    logic cs_sync0,   cs_sync1;
    logic mosi_sync0, mosi_sync1;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk_sync0 <= 0; sclk_sync1 <= 0;
            cs_sync0   <= 1; cs_sync1   <= 1;
            mosi_sync0 <= 0; mosi_sync1 <= 0;
        end else begin
            sclk_sync0 <= spi_sclk;
            sclk_sync1 <= sclk_sync0;

            cs_sync0   <= spi_cs_n;
            cs_sync1   <= cs_sync0;

            mosi_sync0 <= spi_mosi;
            mosi_sync1 <= mosi_sync0;
        end
    end

    wire cs_n  = cs_sync1;
    wire sclk  = sclk_sync1;
    wire mosi  = mosi_sync1;

    // Edge detect on SCLK (rising edge, mode 0)
    logic sclk_d;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) sclk_d <= 1'b0;
        else        sclk_d <= sclk;
    end
    wire sclk_rise = (~sclk_d) & sclk & (~cs_n);

    // --------------------------------------------------
    // Shift register + byte/bit counters
    // --------------------------------------------------
    localparam int FRAME_BYTES = 7;   // CMD + BAND + ADDR_H + ADDR_L + D2 + D1 + D0

    logic [2:0] bit_cnt;   // 0..7
    logic [2:0] byte_cnt;  // 0..6
    logic [7:0] shift_reg;
    logic [7:0] rx_bytes [0:FRAME_BYTES-1];

    typedef enum logic [1:0] {
        IDLE,
        RECV
    } state_t;

    state_t state, next_state;

    // State & counters
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= IDLE;
            bit_cnt   <= 3'd0;
            byte_cnt  <= 3'd0;
            shift_reg <= 8'h00;
        end else begin
            state <= next_state;

            if (state == IDLE) begin
                if (!cs_n) begin
                    // CS just went low – start receiving
                    bit_cnt  <= 3'd0;
                    byte_cnt <= 3'd0;
                    shift_reg<= 8'h00;
                end
            end else if (state == RECV) begin
                if (cs_n) begin
                    // aborted frame
                    bit_cnt  <= 3'd0;
                    byte_cnt <= 3'd0;
                end else if (sclk_rise) begin
                    // Shift MOSI in MSB-first
                    shift_reg <= {shift_reg[6:0], mosi};
                    bit_cnt   <= bit_cnt + 3'd1;
                    if (bit_cnt == 3'd7) begin
                        // full byte received
                        rx_bytes[byte_cnt] <= {shift_reg[6:0], mosi};
                        byte_cnt <= byte_cnt + 3'd1;
                        bit_cnt  <= 3'd0;
                    end
                end
            end
        end
    end

    // Next state
    always_comb begin
        next_state = state;
        unique case (state)
            IDLE: begin
                if (!cs_n) next_state = RECV;
            end
            RECV: begin
                // stay until CS high OR we consumed full frame
                if (cs_n)             next_state = IDLE;
                else if (byte_cnt == FRAME_BYTES && bit_cnt == 3'd0)
                    next_state = IDLE;
            end
        endcase
    end

    // --------------------------------------------------
    // Decode frame into write pulse
    // --------------------------------------------------
    logic wr_en_int;
    logic [$clog2(BANDS)-1:0] wr_band_int;
    logic [ADDR_WIDTH-1:0]    wr_addr_int;
    logic [DATA_WIDTH-1:0]    wr_data_int;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_en_int   <= 1'b0;
            wr_band_int <= '0;
            wr_addr_int <= '0;
            wr_data_int <= '0;
        end else begin
            wr_en_int <= 1'b0;  // default

            // At end of frame (all 7 bytes received and CS still low)
            if (state == RECV &&
                byte_cnt == FRAME_BYTES && bit_cnt == 3'd0 && !cs_n) begin

                logic [7:0] cmd;
                logic [7:0] band;
                logic [7:0] addr_h, addr_l;
                logic [7:0] d2, d1, d0;

                cmd    = rx_bytes[0];
                band   = rx_bytes[1];
                addr_h = rx_bytes[2];
                addr_l = rx_bytes[3];
                d2     = rx_bytes[4];
                d1     = rx_bytes[5];
                d0     = rx_bytes[6];

                if (cmd == 8'h01) begin
                    wr_band_int <= band[$clog2(BANDS)-1:0];
                    wr_addr_int <= {addr_h[1:0], addr_l}; // 10-bit addr
                    wr_data_int <= {d2, d1, d0};          // 24-bit gain
                    wr_en_int   <= 1'b1;
                end
            end
        end
    end

    assign wr_en   = wr_en_int;
    assign wr_band = wr_band_int;
    assign wr_addr = wr_addr_int;
    assign wr_data = wr_data_int;

    // we don't support readback for now
    //assign spi_miso = 1'b0;

endmodule
