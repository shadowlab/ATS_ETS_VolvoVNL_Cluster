#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define JPEG_MAX_PIXELS (1280 * 360)

esp_err_t jpeg_init(void);
/* Encode a gray image with the P4's JPEG engine. *out is heap memory (free() it). Thread-safe. */
esp_err_t jpeg_gray(const uint8_t *gray, int w, int h, uint8_t **out, size_t *len);
