#pragma once
#include "esp_err.h"
#include "driver/i2c_master.h"

/* Tab5 system I2C (SDA 31, SCL 32) and the two PI4IOE5V6408 expanders that switch the camera and C6. */
esp_err_t board_init(void);
i2c_master_bus_handle_t board_i2c(void);
esp_err_t board_camera_power(bool on);
esp_err_t board_wifi_power(bool on);
