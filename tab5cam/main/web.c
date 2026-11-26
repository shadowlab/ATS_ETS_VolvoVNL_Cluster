/* HTTP API (port 80):
 *   GET  /                 live page: picture with the regions, scores, events
 *   GET  /events[?since=n] Server-Sent Events, one JSON object per event ("id:" is the event's queue number)
 *   GET  /stream           MJPEG, regions drawn in (about 5 fps)
 *   GET  /snapshot.jpg     current picture, no overlay (?overlay=1 to draw the regions)
 *   GET  /hit?id=n         baseline | frame picture saved when region event n started
 *   GET  /config, POST /config (JSON, merged; "rois" replaces the list)
 *   POST /baseline, POST /autoexp[?target=n]
 *   GET  /status, GET /time
 */
#include "web.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cam.h"
#include "detect.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "jpeg.h"
#include "net.h"

static const char *TAG = "web";

#define RING 256
#define HITS 32

static struct {
    int n;
    char *json;
} s_ring[RING];
static int s_next = 1;           /* queue number of the next event */
static SemaphoreHandle_t s_lock;
static struct {
    int id;
    uint8_t *jpg;
    size_t len;
} s_hits[HITS];
static volatile int s_streams;

extern const char index_html_start[] asm("_binary_index_html_start");
extern const char index_html_end[] asm("_binary_index_html_end");

void web_event(const char *json)
{
    if (!strstr(json, "\"ev\":\"tick\"")) ESP_LOGI(TAG, "event %s", json);
    if (!s_lock) return;
    char *copy = strdup(json);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const int slot = s_next % RING;
    free(s_ring[slot].json);
    s_ring[slot].json = copy;
    s_ring[slot].n = s_next++;
    xSemaphoreGive(s_lock);
}

void web_hit(int id, uint8_t *jpg, size_t len)
{
    if (!s_lock) {
        free(jpg);
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const int slot = id % HITS;
    free(s_hits[slot].jpg);
    s_hits[slot].id = id;
    s_hits[slot].jpg = jpg;
    s_hits[slot].len = len;
    xSemaphoreGive(s_lock);
}

static int query_int(httpd_req_t *req, const char *key, int dflt)
{
    char q[64], v[16];
    if (httpd_req_get_url_query_str(req, q, sizeof(q)) == ESP_OK && httpd_query_key_value(q, key, v, sizeof(v)) == ESP_OK) {
        return atoi(v);
    }
    return dflt;
}

static esp_err_t send_json(httpd_req_t *req, char *s)
{
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    esp_err_t err = httpd_resp_sendstr(req, s ? s : "{}");
    free(s);
    return err;
}

static esp_err_t h_index(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, index_html_start, index_html_end - index_html_start - 1);
}

static esp_err_t h_time(httpd_req_t *req)
{
    char *s = malloc(48);
    snprintf(s, 48, "{\"t\":%lld}", esp_timer_get_time() / 1000);
    return send_json(req, s);
}

static esp_err_t h_status(httpd_req_t *req)
{
    char *d = det_status_json();
    char *s = malloc(512);
    char ip[20];
    net_ip(ip, sizeof(ip));
    snprintf(s, 512, "{\"t\":%lld,\"ip\":\"%s\",\"rssi\":%d,\"heap_psram\":%u,\"camera\":%s}", esp_timer_get_time() / 1000, ip,
             net_rssi(), (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM), d ? d : "{}");
    free(d);
    return send_json(req, s);
}

static esp_err_t h_config_get(httpd_req_t *req) { return send_json(req, det_config_json()); }

static esp_err_t h_config_post(httpd_req_t *req)
{
    if (req->content_len > 8192) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "too long");
    char *body = calloc(1, req->content_len + 1);
    int got = 0;
    while (got < (int)req->content_len) {
        int r = httpd_req_recv(req, body + got, req->content_len - got);
        if (r <= 0) {
            free(body);
            return ESP_FAIL;
        }
        got += r;
    }
    char err[96] = "";
    esp_err_t e = det_set_config(body, err, sizeof(err));
    free(body);
    if (e != ESP_OK) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, err);
    return send_json(req, det_config_json());
}

static esp_err_t h_baseline(httpd_req_t *req)
{
    det_rebaseline();
    return send_json(req, strdup("{\"ok\":true}"));
}

static esp_err_t h_autoexp(httpd_req_t *req)
{
    det_autoexposure(query_int(req, "target", 0));
    return send_json(req, strdup("{\"ok\":true}"));
}

static esp_err_t send_jpeg(httpd_req_t *req, const uint8_t *jpg, size_t len)
{
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, (const char *)jpg, len);
}

static esp_err_t snap(bool overlay, uint8_t **jpg, size_t *len)
{
    const int w = cam_width(), h = cam_height();
    uint8_t *g = heap_caps_malloc((size_t)w * h, MALLOC_CAP_SPIRAM);
    if (!g) return ESP_ERR_NO_MEM;
    esp_err_t err = det_copy_latest(g, overlay);
    if (err == ESP_OK) err = jpeg_gray(g, w, h, jpg, len);
    free(g);
    return err;
}

static esp_err_t h_snapshot(httpd_req_t *req)
{
    uint8_t *jpg = NULL;
    size_t len = 0;
    if (snap(query_int(req, "overlay", 0) != 0, &jpg, &len) != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no picture yet");
    }
    esp_err_t err = send_jpeg(req, jpg, len);
    free(jpg);
    return err;
}

static esp_err_t h_hit(httpd_req_t *req)
{
    const int id = query_int(req, "id", -1);
    uint8_t *copy = NULL;
    size_t len = 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const int slot = id >= 0 ? id % HITS : 0;
    if (id >= 0 && s_hits[slot].id == id && s_hits[slot].jpg) {
        copy = heap_caps_malloc(s_hits[slot].len, MALLOC_CAP_SPIRAM);
        if (copy) {
            memcpy(copy, s_hits[slot].jpg, s_hits[slot].len);
            len = s_hits[slot].len;
        }
    }
    xSemaphoreGive(s_lock);
    if (!copy) return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "no picture for that id (only the last 32 are kept)");
    esp_err_t err = send_jpeg(req, copy, len);
    free(copy);
    return err;
}

/* ---- long-lived responses run in their own task ---- */

static void events_task(void *arg)
{
    httpd_req_t *req = arg;
    int since = query_int(req, "since", 0);
    httpd_resp_set_type(req, "text/event-stream");
    httpd_resp_set_hdr(req, "Cache-Control", "no-cache");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    char hello[96];
    snprintf(hello, sizeof(hello), "data: {\"ev\":\"hello\",\"t\":%lld}\n\n", esp_timer_get_time() / 1000);
    bool ok = httpd_resp_send_chunk(req, hello, HTTPD_RESP_USE_STRLEN) == ESP_OK;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    int next = s_next;
    xSemaphoreGive(s_lock);
    if (since > 0 && since < next) next = since > next - RING + 1 ? since : next - RING + 1;   /* replay what was missed */
    if (next < 1) next = 1;
    char *line = malloc(1600);
    while (ok) {
        char *json = NULL;
        int n = 0;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (next < s_next) {
            if (next < s_next - RING + 1) next = s_next - RING + 1;
            const int slot = next % RING;
            if (s_ring[slot].n == next && s_ring[slot].json) json = strdup(s_ring[slot].json);
            n = next++;
        }
        xSemaphoreGive(s_lock);
        if (!n) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (!json) continue;
        snprintf(line, 1600, "id: %d\ndata: %s\n\n", n, json);
        free(json);
        ok = httpd_resp_send_chunk(req, line, HTTPD_RESP_USE_STRLEN) == ESP_OK;
    }
    free(line);
    httpd_req_async_handler_complete(req);
    vTaskDelete(NULL);
}

static void stream_task(void *arg)
{
    httpd_req_t *req = arg;
    httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    bool ok = true;
    char head[96];
    while (ok) {
        const int64_t t0 = esp_timer_get_time();
        uint8_t *jpg = NULL;
        size_t len = 0;
        if (snap(true, &jpg, &len) == ESP_OK) {
            snprintf(head, sizeof(head), "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", (unsigned)len);
            ok = httpd_resp_send_chunk(req, head, HTTPD_RESP_USE_STRLEN) == ESP_OK &&
                 httpd_resp_send_chunk(req, (const char *)jpg, len) == ESP_OK &&
                 httpd_resp_send_chunk(req, "\r\n", 2) == ESP_OK;
            free(jpg);
        }
        const int64_t left = 200000 - (esp_timer_get_time() - t0);
        if (left > 0) vTaskDelay(pdMS_TO_TICKS(left / 1000 + 1));
    }
    s_streams--;
    httpd_req_async_handler_complete(req);
    vTaskDelete(NULL);
}

static esp_err_t spawn(httpd_req_t *req, TaskFunction_t fn, const char *name)
{
    httpd_req_t *copy = NULL;
    if (httpd_req_async_handler_begin(req, &copy) != ESP_OK) return ESP_FAIL;
    if (xTaskCreate(fn, name, 6144, copy, 4, NULL) != pdPASS) {
        httpd_req_async_handler_complete(copy);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t h_events(httpd_req_t *req) { return spawn(req, events_task, "sse"); }

static esp_err_t h_stream(httpd_req_t *req)
{
    if (s_streams >= 2) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "two streams already open");
    s_streams++;
    esp_err_t err = spawn(req, stream_task, "mjpeg");
    if (err != ESP_OK) s_streams--;
    return err;
}

esp_err_t web_start(void)
{
    s_lock = xSemaphoreCreateMutex();
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.max_open_sockets = 10;
    cfg.max_uri_handlers = 16;
    cfg.lru_purge_enable = false;
    cfg.stack_size = 8192;
    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &cfg);
    if (err != ESP_OK) return err;
    const httpd_uri_t uris[] = {
        {.uri = "/", .method = HTTP_GET, .handler = h_index},
        {.uri = "/time", .method = HTTP_GET, .handler = h_time},
        {.uri = "/status", .method = HTTP_GET, .handler = h_status},
        {.uri = "/config", .method = HTTP_GET, .handler = h_config_get},
        {.uri = "/config", .method = HTTP_POST, .handler = h_config_post},
        {.uri = "/baseline", .method = HTTP_POST, .handler = h_baseline},
        {.uri = "/autoexp", .method = HTTP_POST, .handler = h_autoexp},
        {.uri = "/snapshot.jpg", .method = HTTP_GET, .handler = h_snapshot},
        {.uri = "/hit", .method = HTTP_GET, .handler = h_hit},
        {.uri = "/events", .method = HTTP_GET, .handler = h_events},
        {.uri = "/stream", .method = HTTP_GET, .handler = h_stream},
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) httpd_register_uri_handler(srv, &uris[i]);
    ESP_LOGI(TAG, "Web server on port 80");
    return ESP_OK;
}
