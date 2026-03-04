module ds_modulator #(
    parameter DAC_BW = 32,
    parameter OSR = 64
)(
    input clk,
    input rst_n,
    input signed [DAC_BW-1:0] din,
    output reg dout
);
    //MID_VAL calculation
    localparam MID_VAL = 1 << (DAC_BW-1);  // 2^31 exactly
    
    // Bit width extensions
    localparam BW_EXT = 8;
    localparam BW_TOT = DAC_BW + BW_EXT;
    localparam BW_TOT2 = BW_TOT + $clog2(OSR);  // 40 + 6 = 46
    
    // Internal signals
    reg signed [BW_TOT-1:0] DAC_acc_1st;
    reg signed [BW_TOT2-1:0] DAC_acc_2nd;
    reg dout_r;
    
    // feedback values
    wire signed [BW_TOT-1:0] MAX_VAL = MID_VAL;
    wire signed [BW_TOT-1:0] MIN_VAL = -MID_VAL;
    wire signed [BW_TOT2-1:0] MAX_VAL2 = {
        {(BW_TOT2-BW_TOT){MAX_VAL[BW_TOT-1]}},
        MAX_VAL
    };
    wire signed [BW_TOT2-1:0] MIN_VAL2 = {
        {(BW_TOT2-BW_TOT){MIN_VAL[BW_TOT-1]}},
        MIN_VAL
    };
    
    // feedback selection
    wire signed [BW_TOT-1:0] dac_val = (dout_r) ? MAX_VAL : MIN_VAL;
    wire signed [BW_TOT2-1:0] dac_val2 = (dout_r) ? MAX_VAL2 : MIN_VAL2;
    
    // Input extensions
    wire signed [BW_TOT-1:0] in_ext = {{BW_EXT{din[DAC_BW-1]}}, din};
    
    // Delta calculations
    wire signed [BW_TOT-1:0] delta_s0_c0 = in_ext - dac_val;
    wire signed [BW_TOT-1:0] delta_s0_c1 = DAC_acc_1st + delta_s0_c0;
    
    wire signed [BW_TOT2-1:0] in_ext2 = {{$clog2(OSR){delta_s0_c1[BW_TOT-1]}}, delta_s0_c1};
    wire signed [BW_TOT2-1:0] delta_s1_c0 = in_ext2 - dac_val2;
    wire signed [BW_TOT2-1:0] delta_s1_c1 = DAC_acc_2nd + delta_s1_c0;
    
    //  quantizer (remove inversion)
    wire signed [BW_TOT2-1:0] dithered_signal = delta_s1_c1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            DAC_acc_1st <= 'd0;
            DAC_acc_2nd <= 'd0;
            dout_r <= 1'b0;
        end else begin
            DAC_acc_1st <= delta_s0_c1;
            DAC_acc_2nd <= delta_s1_c1;
            // quantizer decision
            dout_r <= dithered_signal[BW_TOT2-1];  // Direct sign bit
        end
    end
    
    assign dout = !dout_r;
endmodule
