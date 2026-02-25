`timescale 1ns / 1ps

module tdm_filterbank_10band #(
    parameter int CORE_LATENCY = 2 // Must match biquad_core_tdm setting
)(
    input  logic        clk,
    input  logic        rst_n,
    
    // Audio Interface
    input  logic        sample_valid,
    input  logic signed [23:0] sample_in,
    
    // Output
    output logic signed [23:0] band_out [0:9],
    output logic        band_valid // Pulses high when all bands updated
);

    // -------------------------------------------------------------
    // State Memory (Distributed RAM)
    // Index: [Band 0-9][Section 0-1]
    // -------------------------------------------------------------
    // x[n-1], x[n-2], y[n-1], y[n-2]
    logic signed [23:0] mem_x_z1 [0:9][0:1];
    logic signed [23:0] mem_x_z2 [0:9][0:1];
    logic signed [23:0] mem_y_z1 [0:9][0:1];
    logic signed [23:0] mem_y_z2 [0:9][0:1];
    
    // -------------------------------------------------------------
    // TDM State Machine
    // -------------------------------------------------------------
    typedef enum {IDLE, LOAD_S0, WAIT_S0, LOAD_S1, WAIT_S1, NEXT_BAND} state_t;
    state_t state;
    
    logic [3:0] band_cnt;
    logic [4:0] wait_cnt; // Timer for core latency
    
    // Registers for pipeline handling
    logic signed [23:0] input_latch;
    logic signed [23:0] stage1_result;
    
    // Core Signals
    logic core_en;
    logic signed [23:0] c_x, c_x1, c_x2, c_y1, c_y2;
    logic signed [23:0] c_b0, c_b1, c_b2, c_a1, c_a2;
    logic signed [23:0] c_out;
    
    // ROM Address Calculation
    // ROM is flattened 0..19. Band i, Sect j -> addr = i*2 + j
    logic [4:0] rom_addr;
    
    // Instantiate Coeff ROM
    coeffs_rom u_rom (
        .addr(rom_addr),
        .b0(c_b0), .b1(c_b1), .b2(c_b2), .a1(c_a1), .a2(c_a2)
    );

    // Instantiate Stateless Core
    biquad_core_tdm #(.PIPELINE_STAGES(CORE_LATENCY)) u_core (
        .clk(clk), .rst_n(rst_n), .en(core_en),
        .sample_in(c_x), 
        .x_z1(c_x1), .x_z2(c_x2), .y_z1(c_y1), .y_z2(c_y2),
        .b0(c_b0), .b1(c_b1), .b2(c_b2), .a1(c_a1), .a2(c_a2),
        .y_out(c_out)
    );
    
    // Output register array
    logic signed [23:0] band_out_reg [0:9];
    assign band_out = band_out_reg;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            band_cnt <= 0;
            band_valid <= 0;
            core_en <= 0;
            rom_addr <= 0;
            // Clear memory logic omitted for brevity (Distributed RAM usually no reset)
            // In simulation, initialize RAMs to 0.
    for (int b = 0; b < 10; b++) begin
        for (int s = 0; s < 2; s++) begin
            mem_x_z1[b][s] <= '0;
            mem_x_z2[b][s] <= '0;
            mem_y_z1[b][s] <= '0;
            mem_y_z2[b][s] <= '0;
        end
    end
        end else begin
            // Pulse valid only for 1 cycle
            band_valid <= 0;
            core_en <= 0; // Default off unless firing
            
            case (state)
                IDLE: begin
                    if (sample_valid) begin
                        input_latch <= sample_in;
                        band_cnt <= 0;
                        state <= LOAD_S0;
                    end
                end

                // ----------------------------------------------------
                // Section 0 Processing
                // ----------------------------------------------------
                LOAD_S0: begin
                    // Setup inputs for Core
                    c_x  <= input_latch;
                    c_x1 <= mem_x_z1[band_cnt][0];
                    c_x2 <= mem_x_z2[band_cnt][0];
                    c_y1 <= mem_y_z1[band_cnt][0];
                    c_y2 <= mem_y_z2[band_cnt][0];
                    
                    rom_addr <= (band_cnt * 2); // Even index
                    
                    core_en <= 1; // Pulse enable
                    wait_cnt <= 0;
                    state <= WAIT_S0;
                end
                
                WAIT_S0: begin
                    core_en <= 1; // Keep enabling to flush pipeline
                    if (wait_cnt == CORE_LATENCY) begin
                        // Core output valid now
                        stage1_result <= c_out;
                        
                        // Writeback State Section 0
                        mem_x_z2[band_cnt][0] <= mem_x_z1[band_cnt][0];
                        mem_x_z1[band_cnt][0] <= input_latch;
                        mem_y_z2[band_cnt][0] <= mem_y_z1[band_cnt][0];
                        mem_y_z1[band_cnt][0] <= c_out;
                        
                        state <= LOAD_S1;
                    end else begin
                        wait_cnt <= wait_cnt + 1'b1;
                    end
                end

                // ----------------------------------------------------
                // Section 1 Processing
                // ----------------------------------------------------
                LOAD_S1: begin
                    // Input is output of Stage 0
                    c_x  <= stage1_result;
                    c_x1 <= mem_x_z1[band_cnt][1];
                    c_x2 <= mem_x_z2[band_cnt][1];
                    c_y1 <= mem_y_z1[band_cnt][1];
                    c_y2 <= mem_y_z2[band_cnt][1];
                    
                    rom_addr <= (band_cnt * 2) + 1; // Odd index
                    
                    core_en <= 1;
                    wait_cnt <= 0;
                    state <= WAIT_S1;
                end
                
                WAIT_S1: begin
                    core_en <= 1;
                    if (wait_cnt == CORE_LATENCY) begin
                        // Final result for this band
                        band_out_reg[band_cnt] <= c_out;
                        
                        // Writeback State Section 1
                        mem_x_z2[band_cnt][1] <= mem_x_z1[band_cnt][1];
                        mem_x_z1[band_cnt][1] <= stage1_result;
                        mem_y_z2[band_cnt][1] <= mem_y_z1[band_cnt][1];
                        mem_y_z1[band_cnt][1] <= c_out;
                        
                        state <= NEXT_BAND;
                    end else begin
                        wait_cnt <= wait_cnt + 1'b1;
                    end
                end

                // ----------------------------------------------------
                // Loop Logic
                // ----------------------------------------------------
                NEXT_BAND: begin
                    if (band_cnt == 9) begin
                        band_valid <= 1;
                        state <= IDLE;
                    end else begin
                        band_cnt <= band_cnt + 1'b1;
                        state <= LOAD_S0;
                    end
                end
            endcase
        end
    end
    
    // RAM Init for Sim
    initial begin
        for(int b=0; b<10; b++) begin
            for(int s=0; s<2; s++) begin
                mem_x_z1[b][s] = 0; mem_x_z2[b][s] = 0;
                mem_y_z1[b][s] = 0; mem_y_z2[b][s] = 0;
            end
        end
    end

endmodule