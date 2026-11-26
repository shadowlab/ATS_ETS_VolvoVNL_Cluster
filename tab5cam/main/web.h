#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t web_start(void);
void web_event(const char *json);                    /* queue an event for /events */
void web_hit(int id, uint8_t *jpg, size_t len);      /* keep a hit picture for /hit?id= (takes ownership) */
