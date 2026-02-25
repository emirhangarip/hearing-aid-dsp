`timescale 1ns / 1ps

module tdm_wdrc_10band #(
    parameter logic signed [23:0] ALPHA_ATK_Q = 24'sd8353728,
    parameter logic signed [23:0] ALPHA_REL_Q = 24'sd8386861
)(
    input  logic        clk,
    input  logic        rst_n,
    
    // Interface
    input  logic        sample_valid,
    input  logic signed [23:0] band_in [0:9],
    
    output logic signed [23:0] band_out [0:9],
    output logic signed [23:0] y_full,
    output logic        out_valid,

    // WDRC gain LUT write port
    input  logic              lut_wr_en,
    input  logic [3:0]        lut_wr_band,
    input  logic [9:0]        lut_wr_addr,
    input  logic signed [23:0] lut_wr_data
);

    // ----------------------------------------------------------------
    // 1. Memory & State
    // ----------------------------------------------------------------
    logic signed [23:0] env_state_ram [0:9];
    logic [3:0] band_cnt;
    
    typedef enum {
        IDLE, 
        LOAD_X,     // Latch Input & Read RAM
        ENV_PREP,   // Abs, Compare, Select Coeffs
        ENV_MULT,   // Multiply Only (Stage A)
        ENV_ADD,    // Add & Saturate (Stage B)
        WRITE_ENV,  // Write RAM -> Drive ROM
        READ_LUT,   // Wait for ROM Data
        MULT_GAIN,  // Gain Multiply
        SAT_GAIN,   // Gain Saturate
        ACCUMULATE, // Add to Accumulator
        DONE
    } state_t;
    state_t state;
    
    // ----------------------------------------------------------------
    // 2. Signals & Pipeline Registers
    // ----------------------------------------------------------------
    logic signed [23:0] x_reg;
    logic signed [23:0] env_prev_d;
    logic signed [23:0] env_next_d;
    logic        [9:0]  lut_addr_d;
    logic signed [23:0] y_calc_d;
    logic signed [23:0] gain_wire;

    // -- Envelope Math Registers --
    logic signed [23:0] abs_x, alpha_sel_reg, one_minus_sel_reg;
    logic signed [23:0] abs_x_reg, env_prev_reg;
    
    // -- Intermediate Multiply Results --
    logic signed [23:0] term1_res_reg;
    logic signed [23:0] term2_res_reg;

    logic signed [23:0] ONE_Q = 24'sd8388608;

    // -- Gain Apply Pipeline Register --
    logic signed [47:0] prod_reg; 

    // ----------------------------------------------------------------
    // 3. Gain LUT RAM
    // ----------------------------------------------------------------
    logic [3:0]          lut_band_sel;
    logic signed [23:0]  lut_gain_q;

    assign lut_band_sel = band_cnt;
    
    wdrc_gain_ram #(
        .BANDS     (10),
        .ADDR_WIDTH(10),
        .DATA_WIDTH(24),
        .INIT_FROM_FILE(1'b0)
    ) u_wdrc_gain_ram (
        .clk     (clk),
        .rd_addr (lut_addr_d),
        .rd_band (lut_band_sel),
        .rd_data (lut_gain_q),
        .wr_en   (lut_wr_en),
        .wr_addr (lut_wr_addr),
        .wr_band (lut_wr_band),
        .wr_data (lut_wr_data)
    );

    always_comb gain_wire = lut_gain_q;

    // ----------------------------------------------------------------
    // 4. Main State Machine
    // ----------------------------------------------------------------
    
    logic signed [31:0] accumulator;
    
    // Temporary variables
    logic signed [47:0] term1_q46, term2_q46;
    logic signed [23:0] env_sum;
    logic signed [47:0] y_shifted;

    localparam signed [47:0] MAX_POS = 48'sd8388607;
    localparam signed [47:0] MAX_NEG = -48'sd8388608;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            band_cnt <= 0;
            accumulator <= 0;
            out_valid <= 0;
            y_full <= 0;
            x_reg <= 0;
            env_prev_d <= 0;
            env_next_d <= 0;
            lut_addr_d <= 0;
            y_calc_d <= 0;
            prod_reg <= 0;
            term1_res_reg <= 0;
            term2_res_reg <= 0;
            for(int k=0; k<10; k++) env_state_ram[k] = 0;
        end else begin
            out_valid <= 0;

            case (state)
                IDLE: begin
                    if (sample_valid) begin
                        band_cnt <= 0;
                        accumulator <= 0;
                        state <= LOAD_X;
                    end
                end
                
                // --- STAGE 1: Load ---
                LOAD_X: begin
                    x_reg <= band_in[band_cnt];
                    env_prev_d <= env_state_ram[band_cnt]; 
                    state <= ENV_PREP;
                end

                // --- STAGE 2: Env Prep ---
                ENV_PREP: begin
                    if (x_reg[23]) abs_x = -x_reg; else abs_x = x_reg;
                    
                    if (abs_x > env_prev_d) begin
                        alpha_sel_reg     <= ALPHA_ATK_Q;
                        one_minus_sel_reg <= ONE_Q - ALPHA_ATK_Q;
                    end else begin
                        alpha_sel_reg     <= ALPHA_REL_Q;
                        one_minus_sel_reg <= ONE_Q - ALPHA_REL_Q;
                    end
                    abs_x_reg    <= abs_x;
                    env_prev_reg <= env_prev_d;
                    state <= ENV_MULT;
                end

                // --- STAGE 3A: Env Multiply (Critical Path Split) ---
                ENV_MULT: begin
                    // 1. Multiplications
                    // Path: Reg -> Mult -> Logic -> Reg
                    term1_q46 = $signed(alpha_sel_reg) * $signed(env_prev_reg);
                    term2_q46 = $signed(one_minus_sel_reg) * $signed(abs_x_reg);
                    
                    // 2. Rounding (Add constant)
                    term1_q46 = term1_q46 + 48'sd4194304; 
                    term2_q46 = term2_q46 + 48'sd4194304;
                    
                    // 3. Shift & Register (Breaks the path here!)
                    term1_res_reg <= term1_q46 >>> 23;
                    term2_res_reg <= term2_q46 >>> 23;
                    
                    state <= ENV_ADD;
                end

                // --- STAGE 3B: Env Add & Saturate ---
                ENV_ADD: begin
                    // Path: Reg -> Add -> Logic -> Reg
                    env_sum = term1_res_reg + term2_res_reg;
                    
                    // Saturate Envelope
                    if (env_sum[23]) env_next_d <= 0;
                    else if (env_sum > 24'sd8388607) env_next_d <= 24'sd8388607;
                    else env_next_d <= env_sum;
                    
                    // Saturate Address (Parallel Logic)
                    if (env_sum[23]) lut_addr_d <= 0;
                    else if (env_sum > 24'sd8388607) lut_addr_d <= 1023;
                    else lut_addr_d <= env_sum[22:13];

                    state <= WRITE_ENV;
                end
                
                // --- STAGE 4: Write Env ---
                WRITE_ENV: begin
                    env_state_ram[band_cnt] <= env_next_d;
                    state <= READ_LUT; 
                end
                
                READ_LUT: begin
                    state <= MULT_GAIN;
                end
                
                // --- STAGE 5: Gain Multiply ---
                MULT_GAIN: begin
                    prod_reg <= $signed(x_reg) * $signed(gain_wire);
                    state <= SAT_GAIN;
                end

                // --- STAGE 6: Gain Saturate ---
                SAT_GAIN: begin
                    logic signed [47:0] prod_rounded;
                    prod_rounded = prod_reg + 48'sd524288;
                    y_shifted = prod_rounded >>> 20;
                    
                    if (y_shifted > MAX_POS) y_calc_d <= 24'sd8388607;
                    else if (y_shifted < MAX_NEG) y_calc_d <= -24'sd8388608;
                    else y_calc_d <= y_shifted[23:0];

                    if (y_shifted > MAX_POS) band_out[band_cnt] <= 24'sd8388607;
                    else if (y_shifted < MAX_NEG) band_out[band_cnt] <= -24'sd8388608;
                    else band_out[band_cnt] <= y_shifted[23:0];

                    state <= ACCUMULATE;
                end
                
                // --- STAGE 7: Accumulate ---
                ACCUMULATE: begin
                    accumulator <= accumulator + y_calc_d;
                    
                    if (band_cnt == 9) begin
                        state <= DONE;
                    end else begin
                        band_cnt <= band_cnt + 1;
                        state <= LOAD_X;
                    end
                end
                
                DONE: begin
                    if (accumulator > 32'sd8388607) y_full <= 24'sd8388607;
                    else if (accumulator < -32'sd8388608) y_full <= -24'sd8388608;
                    else y_full <= accumulator[23:0];
                    
                    out_valid <= 1;
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule