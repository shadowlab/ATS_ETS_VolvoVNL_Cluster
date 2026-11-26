#include "cam.h"

#include <fcntl.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "board.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_video_device.h"
#include "esp_video_init.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "linux/videodev2.h"

static const char *TAG = "cam";

#define NBUF 3

static int s_fd = -1;
static uint8_t *s_buf[NBUF];
static int s_sw, s_sh;          /* sensor frame */
static int s_w, s_h;            /* gray frame (half size) */
static uint32_t s_fmt;
static uint8_t *s_gray;
static cam_frame_cb_t s_cb;
static volatile float s_fps;

int cam_width(void) { return s_w; }
int cam_height(void) { return s_h; }
float cam_fps(void) { return s_fps; }

/* 2x2 average to gray. UYVY carries Y directly; RGB565 (little-endian) goes through BT.601 weights. */
static void to_gray(const uint8_t *src)
{
    const int stride = s_sw * 2;
    for (int y = 0; y < s_h; y++) {
        const uint8_t *r0 = src + (size_t)(2 * y) * stride;
        const uint8_t *r1 = r0 + stride;
        uint8_t *o = s_gray + (size_t)y * s_w;
        if (s_fmt == V4L2_PIX_FMT_UYVY) {
            for (int x = 0; x < s_w; x++) {
                const int i = 4 * x;
                o[x] = (uint8_t)((r0[i + 1] + r0[i + 3] + r1[i + 1] + r1[i + 3] + 2) >> 2);
            }
        } else {
            const uint16_t *p0 = (const uint16_t *)r0, *p1 = (const uint16_t *)r1;
            for (int x = 0; x < s_w; x++) {
                int sum = 0;
                const uint16_t px[4] = {p0[2 * x], p0[2 * x + 1], p1[2 * x], p1[2 * x + 1]};
                for (int k = 0; k < 4; k++) {
                    const int r = (px[k] >> 11) << 3, g = ((px[k] >> 5) & 0x3f) << 2, b = (px[k] & 0x1f) << 3;
                    sum += r * 77 + g * 150 + b * 29;
                }
                o[x] = (uint8_t)(sum >> 10);
            }
        }
    }
}

static void cam_task(void *arg)
{
    int frames = 0;
    int64_t t0 = esp_timer_get_time();
    for (;;) {
        struct v4l2_buffer buf = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP};
        if (ioctl(s_fd, VIDIOC_DQBUF, &buf) != 0) {
            ESP_LOGE(TAG, "DQBUF failed");
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        const int64_t t_ms = esp_timer_get_time() / 1000;
        to_gray(s_buf[buf.index]);
        ioctl(s_fd, VIDIOC_QBUF, &buf);
        s_cb(s_gray, s_w, s_h, t_ms);

        frames++;
        const int64_t now = esp_timer_get_time();
        if (now - t0 >= 2000000) {
            s_fps = frames * 1e6f / (float)(now - t0);
            frames = 0;
            t0 = now;
        }
    }
}

esp_err_t cam_start(cam_frame_cb_t cb)
{
    s_cb = cb;
    ESP_RETURN_ON_ERROR(board_camera_power(true), TAG, "camera power");
    vTaskDelay(pdMS_TO_TICKS(100));

    const esp_video_init_csi_config_t csi = {
        .sccb_config = {.init_sccb = false, .i2c_handle = board_i2c(), .freq = 400000},
        .reset_pin = -1,
        .pwdn_pin = -1,
    };
    const esp_video_init_config_t vcfg = {.csi = &csi};
    ESP_RETURN_ON_ERROR(esp_video_init(&vcfg), TAG, "esp_video_init (camera not found?)");

    s_fd = open(ESP_VIDEO_MIPI_CSI_DEVICE_NAME, O_RDONLY);
    ESP_RETURN_ON_FALSE(s_fd >= 0, ESP_FAIL, TAG, "open %s", ESP_VIDEO_MIPI_CSI_DEVICE_NAME);

    struct v4l2_format fmt = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    ESP_RETURN_ON_FALSE(ioctl(s_fd, VIDIOC_G_FMT, &fmt) == 0, ESP_FAIL, TAG, "G_FMT");
    s_sw = fmt.fmt.pix.width;
    s_sh = fmt.fmt.pix.height;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_UYVY;
    if (ioctl(s_fd, VIDIOC_S_FMT, &fmt) != 0) {
        fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_RGB565;
        ESP_RETURN_ON_FALSE(ioctl(s_fd, VIDIOC_S_FMT, &fmt) == 0, ESP_FAIL, TAG, "S_FMT");
    }
    s_fmt = fmt.fmt.pix.pixelformat;
    s_w = s_sw / 2;
    s_h = s_sh / 2;
    ESP_LOGI(TAG, "Sensor %dx%d %s, gray %dx%d", s_sw, s_sh, s_fmt == V4L2_PIX_FMT_UYVY ? "UYVY" : "RGB565", s_w, s_h);

    struct v4l2_requestbuffers req = {.count = NBUF, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP};
    ESP_RETURN_ON_FALSE(ioctl(s_fd, VIDIOC_REQBUFS, &req) == 0, ESP_FAIL, TAG, "REQBUFS");
    for (int i = 0; i < NBUF; i++) {
        struct v4l2_buffer buf = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP, .index = i};
        ESP_RETURN_ON_FALSE(ioctl(s_fd, VIDIOC_QUERYBUF, &buf) == 0, ESP_FAIL, TAG, "QUERYBUF");
        s_buf[i] = mmap(NULL, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, s_fd, buf.m.offset);
        ESP_RETURN_ON_FALSE(s_buf[i], ESP_FAIL, TAG, "mmap");
        ESP_RETURN_ON_FALSE(ioctl(s_fd, VIDIOC_QBUF, &buf) == 0, ESP_FAIL, TAG, "QBUF");
    }
    s_gray = heap_caps_malloc((size_t)s_w * s_h, MALLOC_CAP_SPIRAM);
    ESP_RETURN_ON_FALSE(s_gray, ESP_ERR_NO_MEM, TAG, "gray buffer");

    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ESP_RETURN_ON_FALSE(ioctl(s_fd, VIDIOC_STREAMON, &type) == 0, ESP_FAIL, TAG, "STREAMON");

    cam_range_t e = {0}, g = {0};
    cam_get_exposure(&e);
    cam_get_gain(&g);
    ESP_LOGI(TAG, "Exposure %ld (%ld-%ld), gain %ld (%ld-%ld)", (long)e.cur, (long)e.min, (long)e.max,
             (long)g.cur, (long)g.min, (long)g.max);
    xTaskCreatePinnedToCore(cam_task, "cam", 6144, NULL, 6, NULL, 1);
    return ESP_OK;
}

static esp_err_t get_ctrl(uint32_t id, cam_range_t *r)
{
    /* esp_video answers VIDIOC_QUERY_EXT_CTRL, not VIDIOC_QUERYCTRL */
    struct v4l2_query_ext_ctrl q = {.id = id};
    if (ioctl(s_fd, VIDIOC_QUERY_EXT_CTRL, &q) != 0) return ESP_ERR_NOT_SUPPORTED;
    r->min = (int32_t)q.minimum;
    r->max = (int32_t)q.maximum;
    r->def = (int32_t)q.default_value;
    struct v4l2_ext_control c = {.id = id};
    struct v4l2_ext_controls cs = {.ctrl_class = V4L2_CTRL_CLASS_USER, .count = 1, .controls = &c};
    r->cur = ioctl(s_fd, VIDIOC_G_EXT_CTRLS, &cs) == 0 ? c.value : q.default_value;
    return ESP_OK;
}

static esp_err_t set_ctrl(uint32_t id, int32_t v)
{
    struct v4l2_ext_control c = {.id = id, .value = v};
    struct v4l2_ext_controls cs = {.ctrl_class = V4L2_CTRL_CLASS_USER, .count = 1, .controls = &c};
    return ioctl(s_fd, VIDIOC_S_EXT_CTRLS, &cs) == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t cam_get_exposure(cam_range_t *r) { return get_ctrl(V4L2_CID_EXPOSURE, r); }
esp_err_t cam_get_gain(cam_range_t *r) { return get_ctrl(V4L2_CID_GAIN, r); }
esp_err_t cam_set_exposure(int32_t v) { return set_ctrl(V4L2_CID_EXPOSURE, v); }
esp_err_t cam_set_gain(int32_t v) { return set_ctrl(V4L2_CID_GAIN, v); }
