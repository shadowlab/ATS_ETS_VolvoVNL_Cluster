#include "net.h"

#include <stdio.h>
#include <string.h>
#include "board.h"
#include "esp_event.h"
#include "esp_hosted.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mdns.h"
#include "nvs.h"

static const char *TAG = "net";
static esp_netif_t *s_sta;
static bool s_wifi_ok;
static volatile bool s_up;
static char s_ip[20];

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *ev = data;
        if (s_up) ESP_LOGW(TAG, "Wi-Fi lost (reason %d), reconnecting", ev->reason);
        s_up = false;
        s_ip[0] = 0;
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&ev->ip_info.ip));
        s_up = true;
        /* tab5_cam.py setup waits for this line */
        printf("IP %s  http://%s/  (or http://vnl-cam.local/)\n", s_ip, s_ip);
    }
}

static bool load(char *ssid, size_t sl, char *pass, size_t pl)
{
    nvs_handle_t h;
    if (nvs_open("wifi", NVS_READONLY, &h) != ESP_OK) return false;
    bool ok = nvs_get_str(h, "ssid", ssid, &sl) == ESP_OK && ssid[0];
    if (ok && nvs_get_str(h, "pass", pass, &pl) != ESP_OK) pass[0] = 0;
    nvs_close(h);
    return ok;
}

static void connect(const char *ssid, const char *pass)
{
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = pass[0] ? WIFI_AUTH_WEP : WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;
    wc.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    esp_wifi_disconnect();
    esp_wifi_set_config(WIFI_IF_STA, &wc);
    ESP_LOGI(TAG, "Joining \"%s\"", ssid);
    esp_wifi_connect();
}

esp_err_t net_start(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_sta = esp_netif_create_default_wifi_sta();
    esp_netif_set_hostname(s_sta, "vnl-cam");

    if (board_wifi_power(true) != ESP_OK) {
        ESP_LOGE(TAG, "Could not switch the C6 on");
        return ESP_FAIL;
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    if (esp_hosted_init() != 0 || esp_hosted_connect_to_slave() != 0) {
        ESP_LOGE(TAG, "No answer from the C6 over SDIO (it needs ESP-Hosted 3.0.9, see docs/tab5_camera.md)");
        return ESP_FAIL;
    }
    esp_hosted_coprocessor_fwver_t ver = {0};
    if (esp_hosted_get_coprocessor_fwversion(&ver) == 0) {
        ESP_LOGI(TAG, "C6 runs ESP-Hosted %d.%d.%d", (int)ver.major1, (int)ver.minor1, (int)ver.patch1);
    }
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t err = esp_wifi_init(&cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_init failed (0x%x)", err);
        return err;
    }
    esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, on_event, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    err = esp_wifi_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_start failed (0x%x)", err);
        return err;
    }
    esp_wifi_set_ps(WIFI_PS_NONE);   /* events go out as they happen */
    s_wifi_ok = true;

    if (mdns_init() == ESP_OK) {
        mdns_hostname_set("vnl-cam");
        mdns_service_add(NULL, "_http", "_tcp", 80, NULL, 0);
    }

    char ssid[33] = "", pass[65] = "";
    if (load(ssid, sizeof(ssid), pass, sizeof(pass))) {
        connect(ssid, pass);
    } else {
        printf("NO WIFI: set it with  python tab5_cam.py setup --port COMx --ssid <name> --password <pass>\n");
    }
    return ESP_OK;
}

esp_err_t net_set_network(const char *ssid, const char *pass)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open("wifi", NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "pass", pass);
    nvs_commit(h);
    nvs_close(h);
    if (!s_wifi_ok) return ESP_ERR_INVALID_STATE;
    connect(ssid, pass);
    return ESP_OK;
}

void net_ip(char *buf, size_t len) { strlcpy(buf, s_up ? s_ip : "", len); }

int net_rssi(void)
{
    wifi_ap_record_t ap;
    return s_up && esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}
