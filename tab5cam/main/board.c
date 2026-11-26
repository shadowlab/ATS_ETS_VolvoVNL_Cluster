/* Board bring-up goes through components/tab5_board (from Lab's RetroESP Tab5 port, which follows
 * Espressif's esp-bsp m5stack_tab5 BSP, Apache-2.0): one I2C bus and one handle per IO expander,
 * shared by the camera, the C6 supply, the screen and touch. */
#include "board.h"
#include "tab5_board.h"

esp_err_t board_init(void) { return tab5_board_init(); }
i2c_master_bus_handle_t board_i2c(void) { return tab5_board_i2c_bus(); }
esp_err_t board_camera_power(bool on) { return tab5_board_camera_enable(on); }
esp_err_t board_wifi_power(bool on) { return tab5_board_wifi_power_enable(on); }
