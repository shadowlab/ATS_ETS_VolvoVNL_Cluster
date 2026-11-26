#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* Change detection in regions of interest, against a baseline taken with exposure and gain held fixed.
 *
 * Kinds: "needle" (pixels that moved, after removing the region's overall brightness shift, so an even
 * glare or light change doesn't count), "lamp" (pixels that changed, brightness shift included) and "ref"
 * (an area that should never change; when it does, the frame is treated as glare). A region fires after
 * `persist` frames in a row above its threshold, unless the frame is glare: a ref region is above, or at
 * least glare_frac of the other regions are above together. */

typedef void (*det_event_cb_t)(const char *json);
typedef void (*det_hit_cb_t)(int id, uint8_t *jpg, size_t len);   /* takes ownership of jpg */

esp_err_t det_init(int w, int h, det_event_cb_t on_event, det_hit_cb_t on_hit);
void det_frame(const uint8_t *gray, int w, int h, int64_t t_ms);   /* called from the camera task */

/* From other tasks: */
char *det_config_json(void);                     /* caller frees */
esp_err_t det_set_config(const char *json, char *err, size_t errlen);   /* merges, saves to NVS, rebaselines */
void det_rebaseline(void);
void det_autoexposure(int target);               /* 0 = configured target */
esp_err_t det_copy_latest(uint8_t *dst, bool overlay);   /* w*h bytes */
char *det_status_json(void);                     /* caller frees */
