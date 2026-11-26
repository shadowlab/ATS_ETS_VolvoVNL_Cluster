#pragma once
#include <stddef.h>
#include "esp_err.h"

/* Wi-Fi station through the C6. The network is kept in NVS (namespace "wifi"), set over USB with
 * `tab5_cam.py setup`. Reconnects on its own. */
esp_err_t net_start(void);
esp_err_t net_set_network(const char *ssid, const char *pass);
void net_ip(char *buf, size_t len);   /* "" when not connected */
int net_rssi(void);
