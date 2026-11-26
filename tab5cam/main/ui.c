/* The Tab5's own screen: live camera preview with the regions drawn on it (for aiming), status, and a
 * Wi-Fi picker (scan, tap a network, type the password on the on-screen keyboard).
 *
 * LVGL draws a 1280x720 landscape picture into one PSRAM buffer; when it is done, the PPA rotates it into
 * the panel's 720x1280 frame buffer (double-buffered, so nothing tears). All LVGL calls happen in the UI
 * task. The backlight goes off two minutes after the last touch so its light doesn't land on the cluster;
 * a touch turns it back on. */
#include "ui.h"

#include <stdio.h>
#include <string.h>
#include "cam.h"
#include "detect.h"
#include "driver/ledc.h"
#include "driver/ppa.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "net.h"
#include "tab5_board.h"

static const char *TAG = "ui";

#define SCR_W 1280
#define SCR_H 720
#define PV_MAX_W 960   /* preview area, left; the frame is fitted into it */
#define PV_MAX_H 720
#define SCREEN_OFF_MS (2 * 60 * 1000)
#define MAX_APS 24

#define C_NEEDLE 0x07E0   /* RGB565 green */
#define C_LAMP   0x07FF   /* cyan */
#define C_REF    0x431F   /* blue */
#define C_HIT    0xF800   /* red */
#define C_GLARE  0xFFE0   /* yellow */

static ppa_client_handle_t s_ppa;
static uint16_t *s_draw;   /* LVGL's full-screen buffer, landscape */
static size_t s_draw_size;
static uint16_t *s_pv;     /* preview canvas */
static uint8_t *s_gray;
static int s_gw, s_gh;
static uint16_t s_lut[256];
static int s_pw_w, s_pw_h;   /* preview size */
static uint16_t s_xmap[PV_MAX_W];
static det_view_t s_view;

static bool s_screen_on = true;
static bool s_swallow;     /* the touch that wakes the screen doesn't also press a button */
static int64_t s_last_touch;
static int64_t s_rebase_at;

static lv_obj_t *s_main, *s_canvas, *s_status, *s_net;
static lv_obj_t *s_wifi, *s_list, *s_list_msg;
static lv_obj_t *s_pw, *s_pw_title, *s_pw_ta;

static net_ap_t s_aps[MAX_APS];
static volatile int s_scan_n;
static volatile bool s_scan_busy, s_scan_done;
static char s_sel[33];

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }
static uint32_t tick_ms(void) { return (uint32_t)now_ms(); }

/* ---------- display, touch, backlight ---------- */

static void backlight(bool on)
{
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, on ? 1023 : 0);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

static esp_err_t backlight_init(void)
{
    const ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&t), TAG, "backlight timer");
    const ledc_channel_config_t c = {
        .gpio_num = TAB5_LCD_BACKLIGHT_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    return ledc_channel_config(&c);
}

static void screen_on(void)
{
    if (s_screen_on) return;
    s_screen_on = true;
    s_rebase_at = 0;
    lv_obj_invalidate(lv_screen_active());
    backlight(true);
}

static void screen_off(void)
{
    if (!s_screen_on) return;
    s_screen_on = false;
    backlight(false);
    /* The screen lit the cluster; take the baseline again once it is dark. */
    s_rebase_at = now_ms() + 2000;
}

static void flush_cb(lv_display_t *d, const lv_area_t *area, uint8_t *px)
{
    if (lv_display_flush_is_last(d) && s_screen_on) {
        void *fb = tab5_display_begin_frame(false);
        if (fb) {
            const ppa_srm_oper_config_t op = {
                .in = {.buffer = s_draw, .pic_w = SCR_W, .pic_h = SCR_H, .block_w = SCR_W, .block_h = SCR_H,
                       .srm_cm = PPA_SRM_COLOR_MODE_RGB565},
                .out = {.buffer = fb, .buffer_size = tab5_display_fb_size(), .pic_w = TAB5_PANEL_W,
                        .pic_h = TAB5_PANEL_H, .srm_cm = PPA_SRM_COLOR_MODE_RGB565},
                .rotation_angle = TAB5_LANDSCAPE_ANGLE == 90 ? PPA_SRM_ROTATION_ANGLE_90 : PPA_SRM_ROTATION_ANGLE_270,
                .scale_x = 1.0f,
                .scale_y = 1.0f,
                .mode = PPA_TRANS_MODE_BLOCKING,
            };
            esp_err_t err = ppa_do_scale_rotate_mirror(s_ppa, &op);
            if (err != ESP_OK) ESP_LOGW(TAG, "PPA rotate failed (0x%x)", err);
            tab5_display_end_frame();
        }
    }
    lv_display_flush_ready(d);
}

static void touch_cb(lv_indev_t *in, lv_indev_data_t *data)
{
    tab5_touch_point_t p;
    if (tab5_touch_read(&p, 1) > 0) {
        s_last_touch = now_ms();
        if (!s_screen_on) {
            screen_on();
            s_swallow = true;
        }
        if (!s_swallow) {
            /* tab5_board's portrait frame -> our landscape frame */
            data->point.x = p.y;
            data->point.y = (TAB5_PANEL_W - 1) - p.x;
            data->state = LV_INDEV_STATE_PRESSED;
            return;
        }
    } else {
        s_swallow = false;
    }
    data->state = LV_INDEV_STATE_RELEASED;
}

/* ---------- preview ---------- */

static void rect(int x, int y, int w, int h, uint16_t c)
{
    const int t = 3;
    int x0 = x * s_pw_w / s_gw, y0 = y * s_pw_h / s_gh;
    int x1 = (x + w) * s_pw_w / s_gw - 1, y1 = (y + h) * s_pw_h / s_gh - 1;
    if (x1 >= s_pw_w) x1 = s_pw_w - 1;
    if (y1 >= s_pw_h) y1 = s_pw_h - 1;
    for (int yy = y0; yy <= y1; yy++) {
        uint16_t *row = s_pv + yy * s_pw_w;
        if (yy < y0 + t || yy > y1 - t) {
            for (int xx = x0; xx <= x1; xx++) row[xx] = c;
        } else {
            for (int k = 0; k < t; k++) row[x0 + k] = row[x1 - k] = c;
        }
    }
}

static void preview_tick(lv_timer_t *tm)
{
    if (!s_gray || !s_screen_on || lv_screen_active() != s_main) return;
    if (det_view(s_gray, &s_view) != ESP_OK) return;
    for (int y = 0; y < s_pw_h; y++) {
        const uint8_t *src = s_gray + (y * s_gh / s_pw_h) * s_gw;
        uint16_t *dst = s_pv + y * s_pw_w;
        for (int x = 0; x < s_pw_w; x++) dst[x] = s_lut[src[s_xmap[x]]];
    }
    for (int i = 0; i < s_view.n; i++) {
        const det_roi_view_t *r = &s_view.roi[i];
        uint16_t c = r->kind == 'r' ? C_REF : r->kind == 'l' ? C_LAMP : C_NEEDLE;
        if (s_view.glare) c = C_GLARE;
        if (r->active) c = C_HIT;
        rect(r->x, r->y, r->w, r->h, c);
    }
    lv_obj_invalidate(s_canvas);
}

/* ---------- Wi-Fi picker ---------- */

static void scan_task(void *arg)
{
    s_scan_n = net_scan(s_aps, MAX_APS);
    s_scan_done = true;
    s_scan_busy = false;
    vTaskDelete(NULL);
}

static void start_scan(void)
{
    if (s_scan_busy) return;
    s_scan_busy = true;
    s_scan_done = false;
    lv_obj_clean(s_list);
    lv_label_set_text(s_list_msg, "Scanning...");
    if (xTaskCreate(scan_task, "scan", 4096, NULL, 3, NULL) != pdPASS) {
        s_scan_busy = false;
        lv_label_set_text(s_list_msg, "Could not start the scan");
    }
}

static void join(const char *ssid, const char *pass)
{
    esp_err_t err = net_set_network(ssid, pass);
    ESP_LOGI(TAG, "Joining \"%s\" from the screen%s", ssid, err == ESP_OK ? "" : " (Wi-Fi is not running)");
    lv_screen_load(s_main);
}

static void ap_clicked(lv_event_t *e)
{
    const net_ap_t *ap = &s_aps[(int)(intptr_t)lv_event_get_user_data(e)];
    strlcpy(s_sel, ap->ssid, sizeof(s_sel));
    if (!ap->secure) {
        join(s_sel, "");
        return;
    }
    char t[64];
    snprintf(t, sizeof(t), "Password for %s", s_sel);
    lv_label_set_text(s_pw_title, t);
    lv_textarea_set_text(s_pw_ta, "");
    lv_textarea_set_password_mode(s_pw_ta, true);
    lv_screen_load(s_pw);
}

static void scan_results(void)
{
    s_scan_done = false;
    lv_obj_clean(s_list);
    if (s_scan_n < 0) {
        lv_label_set_text(s_list_msg, "Scan failed. Is the C6 running? Try Scan again.");
        return;
    }
    if (s_scan_n == 0) {
        lv_label_set_text(s_list_msg, "No networks found (the Tab5 only sees 2.4 GHz)");
        return;
    }
    lv_label_set_text(s_list_msg, "Tap a network");
    for (int i = 0; i < s_scan_n; i++) {
        char t[80];
        snprintf(t, sizeof(t), "%s    %d dBm%s", s_aps[i].ssid, s_aps[i].rssi, s_aps[i].secure ? "" : "    open");
        lv_obj_t *b = lv_list_add_button(s_list, LV_SYMBOL_WIFI, t);
        lv_obj_add_event_cb(b, ap_clicked, LV_EVENT_CLICKED, (void *)(intptr_t)i);
    }
}

static void pw_key(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_READY) {
        join(s_sel, lv_textarea_get_text(s_pw_ta));
    } else if (code == LV_EVENT_CANCEL) {
        lv_screen_load(s_wifi);
    }
}

static void on_btn(lv_event_t *e)
{
    switch ((int)(intptr_t)lv_event_get_user_data(e)) {
    case 0: lv_screen_load(s_wifi); start_scan(); break;
    case 1: det_rebaseline(); break;
    case 2: det_autoexposure(0); break;
    case 3: screen_off(); break;
    case 4: lv_screen_load(s_main); break;
    case 5: start_scan(); break;
    case 6: lv_textarea_set_password_mode(s_pw_ta, !lv_textarea_get_password_mode(s_pw_ta)); break;
    case 7: lv_screen_load(s_wifi); break;
    }
}

static lv_obj_t *button(lv_obj_t *parent, const char *text, int id, int x, int y, int w, int h)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_pos(b, x, y);
    lv_obj_set_size(b, w, h);
    lv_obj_add_event_cb(b, on_btn, LV_EVENT_CLICKED, (void *)(intptr_t)id);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, text);
    lv_obj_center(l);
    return b;
}

static lv_obj_t *screen(void)
{
    lv_obj_t *s = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(s, lv_color_black(), 0);
    lv_obj_remove_flag(s, LV_OBJ_FLAG_SCROLLABLE);
    return s;
}

/* ---------- status ---------- */

static void status_tick(lv_timer_t *tm)
{
    int64_t now = now_ms();
    if (s_screen_on && lv_screen_active() == s_main && now - s_last_touch > SCREEN_OFF_MS) screen_off();
    if (s_rebase_at && now >= s_rebase_at) {
        s_rebase_at = 0;
        det_rebaseline();
    }
    if (s_scan_done) scan_results();
    if (!s_screen_on) return;

    char ip[20], ssid[33], t[160];
    net_ip(ip, sizeof(ip));
    net_ssid(ssid, sizeof(ssid));
    if (!ssid[0]) {
        snprintf(t, sizeof(t), "Wi-Fi: not set\nTap Wi-Fi below");
    } else if (!ip[0]) {
        snprintf(t, sizeof(t), "Wi-Fi: %s\njoining...", ssid);
    } else {
        snprintf(t, sizeof(t), "Wi-Fi: %s  %d dBm\n%s\nvnl-cam.local", ssid, net_rssi(), ip);
    }
    lv_label_set_text(s_net, t);

    if (!s_gray) {
        lv_label_set_text(s_status, "CAMERA FAILED (see the USB log)");
        return;
    }
    cam_range_t ex = {0}, ga = {0};
    cam_get_exposure(&ex);
    cam_get_gain(&ga);
    snprintf(t, sizeof(t), "%.0f fps  exp %d  gain %d\n%d regions  %d events%s%s%s",
             (double)cam_fps(), (int)ex.cur, (int)ga.cur, s_view.n, s_view.events,
             s_view.learning ? "\nLEARNING" : "", s_view.autoexposure ? "\nAUTO EXPOSURE" : "",
             s_view.glare ? "\nGLARE" : "");
    lv_label_set_text(s_status, t);
}

/* ---------- setup ---------- */

static void build(void)
{
    /* main: preview left (fitted to 960x720), right column: network, buttons, status, legend */
    s_main = screen();
    s_canvas = lv_canvas_create(s_main);
    lv_canvas_set_buffer(s_canvas, s_pv, s_pw_w, s_pw_h, LV_COLOR_FORMAT_RGB565);
    lv_obj_set_pos(s_canvas, 0, (SCR_H - s_pw_h) / 2);
    const int bx = PV_MAX_W + 12, bw = SCR_W - PV_MAX_W - 20, bh = 76;
    s_net = lv_label_create(s_main);
    lv_obj_set_pos(s_net, bx, 8);
    lv_obj_set_width(s_net, bw);
    lv_label_set_long_mode(s_net, LV_LABEL_LONG_WRAP);
    button(s_main, LV_SYMBOL_WIFI "  Wi-Fi", 0, bx, 112, bw, bh);
    button(s_main, "New baseline", 1, bx, 196, bw, bh);
    button(s_main, "Auto exposure", 2, bx, 280, bw, bh);
    button(s_main, "Screen off", 3, bx, 364, bw, bh);
    s_status = lv_label_create(s_main);
    lv_obj_set_pos(s_status, bx, 452);
    lv_obj_set_width(s_status, bw);
    lv_label_set_long_mode(s_status, LV_LABEL_LONG_WRAP);
    static const struct { const char *t; uint32_t c; } legend[] = {
        {"needle", 0x00ff00}, {"lamp", 0x00ffff}, {"ref", 0x4060ff}, {"changed", 0xff0000}, {"glare", 0xffff00}};
    for (int i = 0; i < 5; i++) {
        lv_obj_t *l = lv_label_create(s_main);
        lv_obj_set_pos(l, bx + (i % 3) * 102, 640 + (i / 3) * 34);
        lv_obj_set_style_text_color(l, lv_color_hex(legend[i].c), 0);
        lv_label_set_text(l, legend[i].t);
    }

    /* network list */
    s_wifi = screen();
    lv_obj_t *t = lv_label_create(s_wifi);
    lv_obj_set_style_text_font(t, &lv_font_montserrat_32, 0);
    lv_label_set_text(t, "Choose a Wi-Fi network");
    lv_obj_set_pos(t, 20, 24);
    button(s_wifi, LV_SYMBOL_REFRESH "  Scan again", 5, 720, 12, 260, 80);
    button(s_wifi, LV_SYMBOL_LEFT "  Back", 4, 1000, 12, 260, 80);
    s_list_msg = lv_label_create(s_wifi);
    lv_obj_set_pos(s_list_msg, 20, 100);
    s_list = lv_list_create(s_wifi);
    lv_obj_set_pos(s_list, 20, 140);
    lv_obj_set_size(s_list, SCR_W - 40, SCR_H - 160);

    /* password */
    s_pw = screen();
    s_pw_title = lv_label_create(s_pw);
    lv_obj_set_style_text_font(s_pw_title, &lv_font_montserrat_32, 0);
    lv_obj_set_pos(s_pw_title, 20, 24);
    button(s_pw, LV_SYMBOL_EYE_OPEN "  Show", 6, 720, 12, 260, 80);
    button(s_pw, LV_SYMBOL_LEFT "  Back", 7, 1000, 12, 260, 80);
    s_pw_ta = lv_textarea_create(s_pw);
    lv_textarea_set_one_line(s_pw_ta, true);
    lv_textarea_set_password_mode(s_pw_ta, true);
    lv_textarea_set_max_length(s_pw_ta, 64);
    lv_obj_set_pos(s_pw_ta, 20, 120);
    lv_obj_set_width(s_pw_ta, SCR_W - 40);
    lv_obj_t *kb = lv_keyboard_create(s_pw);
    lv_obj_set_size(kb, SCR_W, SCR_H - 230);
    lv_obj_align(kb, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_keyboard_set_textarea(kb, s_pw_ta);
    lv_obj_add_event_cb(kb, pw_key, LV_EVENT_ALL, NULL);

    lv_timer_create(preview_tick, 150, NULL);
    lv_timer_create(status_tick, 500, NULL);
    lv_screen_load(s_main);
}

static void ui_task(void *arg)
{
    build();
    s_last_touch = now_ms();
    lv_timer_handler();
    backlight(true);
    for (;;) {
        uint32_t wait = lv_timer_handler();
        if (wait < 5) wait = 5;
        if (wait > 30) wait = 30;
        vTaskDelay(pdMS_TO_TICKS(wait));
    }
}

esp_err_t ui_start(int gray_w, int gray_h)
{
    ESP_RETURN_ON_ERROR(backlight_init(), TAG, "backlight");
    ESP_RETURN_ON_ERROR(tab5_display_init(), TAG, "display");
    if (tab5_touch_init() != ESP_OK) ESP_LOGW(TAG, "No touch: the screen shows the preview but can't be used");

    const ppa_client_config_t pc = {.oper_type = PPA_OPERATION_SRM, .max_pending_trans_num = 1};
    ESP_RETURN_ON_ERROR(ppa_register_client(&pc, &s_ppa), TAG, "PPA");

    s_draw_size = (size_t)SCR_W * SCR_H * 2;
    s_draw = heap_caps_aligned_calloc(128, 1, s_draw_size, MALLOC_CAP_SPIRAM);
    /* fit the detector frame into the preview area, keeping its shape */
    s_pw_w = PV_MAX_W;
    s_pw_h = gray_w > 0 ? PV_MAX_W * gray_h / gray_w : PV_MAX_H;
    if (s_pw_h > PV_MAX_H) {
        s_pw_h = PV_MAX_H;
        s_pw_w = PV_MAX_H * gray_w / gray_h;
    }
    s_pv = heap_caps_aligned_calloc(128, 1, (size_t)s_pw_w * s_pw_h * 2, MALLOC_CAP_SPIRAM);
    ESP_RETURN_ON_FALSE(s_draw && s_pv, ESP_ERR_NO_MEM, TAG, "screen buffers");
    if (gray_w > 0 && gray_h > 0) {
        s_gw = gray_w;
        s_gh = gray_h;
        s_gray = heap_caps_malloc((size_t)s_gw * s_gh, MALLOC_CAP_SPIRAM);
        ESP_RETURN_ON_FALSE(s_gray, ESP_ERR_NO_MEM, TAG, "preview buffer");
        for (int x = 0; x < s_pw_w; x++) s_xmap[x] = (uint16_t)(x * s_gw / s_pw_w);
    }
    for (int g = 0; g < 256; g++) s_lut[g] = (uint16_t)(((g >> 3) << 11) | ((g >> 2) << 5) | (g >> 3));

    lv_init();
    lv_tick_set_cb(tick_ms);
    lv_display_t *d = lv_display_create(SCR_W, SCR_H);
    lv_display_set_color_format(d, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(d, s_draw, NULL, s_draw_size, LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(d, flush_cb);
    lv_display_set_theme(d, lv_theme_default_init(d, lv_palette_main(LV_PALETTE_BLUE), lv_palette_main(LV_PALETTE_RED),
                                                  true, &lv_font_montserrat_24));
    lv_indev_t *in = lv_indev_create();
    lv_indev_set_type(in, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(in, touch_cb);

    ESP_RETURN_ON_FALSE(xTaskCreate(ui_task, "ui", 8192, NULL, 2, NULL) == pdPASS, ESP_ERR_NO_MEM, TAG, "ui task");
    ESP_LOGI(TAG, "SCREEN OK (Tab5 hardware rev %d)", (int)tab5_board_revision());
    return ESP_OK;
}
