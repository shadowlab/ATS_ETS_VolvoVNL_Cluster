#pragma once
#include "esp_err.h"

/* The Tab5 screen: live preview with the regions, status, and the Wi-Fi picker. gray_w/h is the detector
 * frame size (0 when the camera failed: the screen still works for Wi-Fi). */
esp_err_t ui_start(int gray_w, int gray_h);
