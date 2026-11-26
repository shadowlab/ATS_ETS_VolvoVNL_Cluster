/* Pins and expander bits from Espressif's esp-bsp m5stack_tab5 BSP (Apache-2.0). */
#include "board.h"
#include "esp_check.h"
#include "esp_io_expander_pi4ioe5v6408.h"

static const char *TAG = "board";
static i2c_master_bus_handle_t s_i2c;
static esp_io_expander_handle_t s_exp0;   /* 0x43: LCD, touch, speaker, camera */
static esp_io_expander_handle_t s_exp1;   /* 0x44: USB 5 V, C6 supply */

#define EXP0_CAMERA_EN IO_EXPANDER_PIN_NUM_6
#define EXP1_WLAN_EN   IO_EXPANDER_PIN_NUM_0

esp_err_t board_init(void)
{
    const i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = 31,
        .scl_io_num = 32,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&cfg, &s_i2c), TAG, "I2C bus");
    ESP_RETURN_ON_ERROR(esp_io_expander_new_i2c_pi4ioe5v6408(s_i2c, ESP_IO_EXPANDER_I2C_PI4IOE5V6408_ADDRESS_LOW,
                                                            &s_exp0), TAG, "expander 0x43");
    ESP_RETURN_ON_ERROR(esp_io_expander_new_i2c_pi4ioe5v6408(s_i2c, ESP_IO_EXPANDER_I2C_PI4IOE5V6408_ADDRESS_HIGH,
                                                            &s_exp1), TAG, "expander 0x44");
    return ESP_OK;
}

i2c_master_bus_handle_t board_i2c(void) { return s_i2c; }

static esp_err_t out(esp_io_expander_handle_t h, uint32_t pin, bool level)
{
    esp_err_t ret = esp_io_expander_set_dir(h, pin, IO_EXPANDER_OUTPUT);
    ret |= esp_io_expander_set_level(h, pin, level);
    ret |= esp_io_expander_set_output_mode(h, pin, IO_EXPANDER_OUTPUT_MODE_PUSH_PULL);
    return ret;
}

esp_err_t board_camera_power(bool on) { return out(s_exp0, EXP0_CAMERA_EN, on); }
esp_err_t board_wifi_power(bool on) { return out(s_exp1, EXP1_WLAN_EN, on); }
