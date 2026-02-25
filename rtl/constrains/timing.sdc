# =========================================================
# 1. CLOCK DEFINITIONS
# =========================================================

# System clock (50 MHz - Pin E2)
create_clock -name clk -period 20 -waveform {0 10} [get_ports {clk}]

# I2S Bit Clock (external - 3.072 MHz)
create_clock -name i2s_sck -period 325.52 -waveform {0 162.76} [get_ports {i2s_sck}]

#create_generated_clock -name clk -source [get_clocks clk] -divide_by 0.5 [get_pins {u_pll/clkout}]

# =========================================================
# 2. CDC PROTECTION (Clock Domain Crossing)
# =========================================================

# These two clock groups are completely independent.
set_clock_groups -asynchronous -group [get_clocks {clk}] -group [get_clocks {i2s_sck}]

# =========================================================
# 3. INPUT DELAYS
# =========================================================

set_input_delay -clock [get_clocks {i2s_sck}] -min 1 [get_ports {i2s_sd}]
set_input_delay -clock [get_clocks {i2s_sck}] -max 5 [get_ports {i2s_sd}]

set_input_delay -clock [get_clocks {i2s_sck}] -min 1 [get_ports {i2s_ws}]
set_input_delay -clock [get_clocks {i2s_sck}] -max 5 [get_ports {i2s_ws}]

# =========================================================
# 4. OUTPUT DELAYS
# =========================================================

set_output_delay -clock [get_clocks {clk}] -min 0 [get_ports {pdm_out_left}]
set_output_delay -clock [get_clocks {clk}] -max 3 [get_ports {pdm_out_left}]

set_output_delay -clock [get_clocks {clk}] -min 0 [get_ports {pdm_out_right}]
set_output_delay -clock [get_clocks {clk}] -max 3 [get_ports {pdm_out_right}]

# =========================================================
# 5. SPI CLOCK DEFINITION
# =========================================================
# 10 MHz SPI Clock (100ns period). Adjust period as needed.
create_clock -name spi_sclk -period 100 -waveform {0 50} [get_ports {spi_sclk}]

# =========================================================
# 6. UPDATE CDC (Add SPI to the Asynchronous Groups)
# =========================================================

set_clock_groups -asynchronous -group [get_clocks {clk}] -group [get_clocks {i2s_sck}] -group [get_clocks {spi_sclk}]

# =========================================================
# 7. SPI INPUT DELAYS (MOSI, CS_N)
# =========================================================

set_input_delay -clock [get_clocks {spi_sclk}] -min 1 [get_ports {spi_mosi}]
set_input_delay -clock [get_clocks {spi_sclk}] -max 5 [get_ports {spi_mosi}]

set_input_delay -clock [get_clocks {spi_sclk}] -min 1 [get_ports {spi_cs_n}]
set_input_delay -clock [get_clocks {spi_sclk}] -max 5 [get_ports {spi_cs_n}]