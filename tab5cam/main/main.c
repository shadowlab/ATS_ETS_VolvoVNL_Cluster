/* VNL cluster camera on the M5Stack Tab5: watches regions of the cluster for changes during sweeps
 * and reports them over Wi-Fi. See docs/tab5_camera.md. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "board.h"
#include "cJSON.h"
#include "cam.h"
#include "detect.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "jpeg.h"
#include "net.h"
#include "nvs_flash.h"
#include "web.h"

static const char *TAG = "main";

/* One command per line on the USB port:
 *   wifi {"ssid":"...","pass":"..."}   save the network and join it
 *   status                             print the address and camera state
 *   reboot */
static void console(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    if (usb_serial_jtag_driver_install(&cfg) != ESP_OK) return;
    usb_serial_jtag_vfs_use_driver();
    setvbuf(stdin, NULL, _IONBF, 0);
    char line[256];
    size_t n = 0;
    for (;;) {
        int c = fgetc(stdin);
        if (c == EOF) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (c != '\n' && c != '\r') {
            if (n < sizeof(line) - 1) line[n++] = (char)c;
            continue;
        }
        line[n] = 0;
        n = 0;
        if (strncmp(line, "wifi ", 5) == 0) {
            cJSON *o = cJSON_Parse(line + 5);
            const cJSON *ssid = cJSON_GetObjectItem(o, "ssid"), *pass = cJSON_GetObjectItem(o, "pass");
            if (cJSON_IsString(ssid) && ssid->valuestring[0]) {
                esp_err_t err = net_set_network(ssid->valuestring, cJSON_IsString(pass) ? pass->valuestring : "");
                printf(err == ESP_OK ? "WIFI SAVED, joining\n" : "WIFI SAVED, but Wi-Fi did not start (see the log above)\n");
            } else {
                printf("ERROR: expected wifi {\"ssid\":\"...\",\"pass\":\"...\"}\n");
            }
            cJSON_Delete(o);
        } else if (strcmp(line, "status") == 0) {
            char ip[20];
            net_ip(ip, sizeof(ip));
            char *s = det_status_json();
            printf("STATUS ip=%s camera=%s\n", ip[0] ? ip : "none", s ? s : "{}");
            free(s);
        } else if (strcmp(line, "reboot") == 0) {
            esp_restart();
        } else if (line[0]) {
            printf("ERROR: unknown command (wifi, status, reboot)\n");
        }
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    ESP_LOGI(TAG, "vnl-tab5cam %s", esp_app_get_description()->version);
    ESP_ERROR_CHECK(board_init());
    if (net_start() != ESP_OK) ESP_LOGE(TAG, "No Wi-Fi: the camera runs, but nothing can reach it");
    ESP_ERROR_CHECK(jpeg_init());
    ESP_ERROR_CHECK(web_start());
    err = cam_start(det_frame);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CAMERA FAILED (0x%x)", err);
    } else {
        ESP_ERROR_CHECK(det_init(cam_width(), cam_height(), web_event, web_hit));
        ESP_LOGI(TAG, "CAMERA OK %dx%d", cam_width(), cam_height());
    }
    console();
}
