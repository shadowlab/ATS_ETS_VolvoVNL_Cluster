#include "jpeg.h"

#include <stdlib.h>
#include <string.h>
#include "driver/jpeg_encode.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "jpeg";
static jpeg_encoder_handle_t s_enc;
static SemaphoreHandle_t s_lock;
static uint8_t *s_in, *s_out;
static size_t s_in_size, s_out_size;

esp_err_t jpeg_init(void)
{
    const jpeg_encode_engine_cfg_t ecfg = {.intr_priority = 0, .timeout_ms = 200};
    ESP_RETURN_ON_ERROR(jpeg_new_encoder_engine(&ecfg, &s_enc), TAG, "encoder");
    const jpeg_encode_memory_alloc_cfg_t in_cfg = {.buffer_direction = JPEG_ENC_ALLOC_INPUT_BUFFER};
    const jpeg_encode_memory_alloc_cfg_t out_cfg = {.buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER};
    s_in = jpeg_alloc_encoder_mem(JPEG_MAX_PIXELS, &in_cfg, &s_in_size);
    s_out = jpeg_alloc_encoder_mem(JPEG_MAX_PIXELS, &out_cfg, &s_out_size);
    ESP_RETURN_ON_FALSE(s_in && s_out, ESP_ERR_NO_MEM, TAG, "buffers");
    s_lock = xSemaphoreCreateMutex();
    return ESP_OK;
}

esp_err_t jpeg_gray(const uint8_t *gray, int w, int h, uint8_t **out, size_t *len)
{
    const size_t n = (size_t)w * h;
    if (!s_enc || n > s_in_size) return ESP_ERR_INVALID_ARG;
    const jpeg_encode_cfg_t cfg = {
        .width = w,
        .height = h,
        .src_type = JPEG_ENCODE_IN_FORMAT_GRAY,
        .sub_sample = JPEG_DOWN_SAMPLING_GRAY,
        .image_quality = 75,
    };
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memcpy(s_in, gray, n);
    uint32_t got = 0;
    esp_err_t err = jpeg_encoder_process(s_enc, &cfg, s_in, n, s_out, s_out_size, &got);
    if (err == ESP_OK) {
        *out = heap_caps_malloc(got, MALLOC_CAP_SPIRAM);
        if (*out) {
            memcpy(*out, s_out, got);
            *len = got;
        } else {
            err = ESP_ERR_NO_MEM;
        }
    }
    xSemaphoreGive(s_lock);
    return err;
}
