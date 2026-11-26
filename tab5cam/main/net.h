#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

/* Wi-Fi station through the C6. The network is kept in NVS (namespace "wifi"), set over USB with
 * `tab5_cam.py setup` or on the Tab5 screen. Reconnects on its own. */
esp_err_t net_start(void);
esp_err_t net_set_network(const char *ssid, const char *pass);
void net_ip(char *buf, size_t len);   /* "" when not connected */
int net_rssi(void);
void net_ssid(char *buf, size_t len); /* saved network, "" when none */

typedef struct {
    char ssid[33];
    int rssi;
    bool secure;
} net_ap_t;
/* Scan for networks (blocks a few seconds). Strongest first, one entry per name. -1 on failure. */
int net_scan(net_ap_t *out, int max);
