#pragma once
#include <stdint.h>
#include "esp_err.h"

/* Gray frames at half the sensor size (800x600 from 1600x1200), one callback per frame from the camera task. */
typedef void (*cam_frame_cb_t)(const uint8_t *gray, int w, int h, int64_t t_ms);

typedef struct {
    int32_t min, max, def, cur;
} cam_range_t;

esp_err_t cam_start(cam_frame_cb_t cb);
int cam_width(void);
int cam_height(void);
float cam_fps(void);

/* Sensor exposure (lines) and gain (index). Nothing changes them except these calls. */
esp_err_t cam_get_exposure(cam_range_t *r);
esp_err_t cam_get_gain(cam_range_t *r);
esp_err_t cam_set_exposure(int32_t v);
esp_err_t cam_set_gain(int32_t v);
