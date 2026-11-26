#include "detect.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "cam.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "jpeg.h"
#include "nvs.h"

static const char *TAG = "detect";

#define MAX_ROI DET_MAX_ROI
#define LEARN_FRAMES 8

typedef enum { KIND_NEEDLE, KIND_LAMP, KIND_REF } kind_t;
static const char *KIND_NAMES[] = {"needle", "lamp", "ref"};
static const float KIND_THR[] = {0.03f, 0.10f, 0.05f};

typedef struct {
    char name[24];
    int x, y, w, h;
    kind_t kind;
    float thr;
} roi_t;

typedef struct {
    roi_t roi[MAX_ROI];
    int n;
    int pix_thr;      /* gray levels a pixel must change by */
    int persist;      /* frames in a row */
    float glare_frac;
    float latch_s;    /* a change that lasts this long is logged as latched and becomes the new baseline */
    float adapt;      /* baseline follows slow drift in quiet regions, per frame (0 = off) */
    int target;       /* mean gray level for auto exposure */
    int32_t exposure, gain;   /* -1 = not set yet */
} cfg_t;

typedef struct {
    int above, below;
    bool active;
    int64_t since;
    int id;
    float score, peak;
} state_t;

static cfg_t s_cfg, s_pending;
static bool s_has_pending, s_want_rebase;
static int s_want_ae;
static state_t s_st[MAX_ROI];
static int s_w, s_h;
static uint16_t *s_base;     /* Q8 */
static uint8_t *s_latest;
static uint8_t *s_compose;
static int s_learn;
static int s_seq;
static bool s_glare;
static int s_glare_n;
static int64_t s_last_tick;
static SemaphoreHandle_t s_lock;
static det_event_cb_t s_on_event;
static det_hit_cb_t s_on_hit;
static volatile bool s_ready;

/* auto exposure: binary search on exposure, then on gain if still too dark */
static struct {
    int phase;        /* 0 idle, 1 exposure, 2 gain */
    int32_t lo, hi, cur;
    int wait, steps, target;
} s_ae;

static void defaults(cfg_t *c)
{
    memset(c, 0, sizeof(*c));
    c->pix_thr = 20;
    c->persist = 3;
    c->glare_frac = 0.5f;
    c->latch_s = 3.0f;
    c->adapt = 0.02f;
    c->target = 90;
    c->exposure = -1;
    c->gain = -1;
}

static void emit(cJSON *o)
{
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (s) {
        s_on_event(s);
        free(s);
    }
}

static cJSON *event(const char *ev, int64_t t)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", ev);
    cJSON_AddNumberToObject(o, "t", (double)t);
    return o;
}

/* ---------- config ---------- */

static cJSON *cfg_to_json(const cfg_t *c)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "width", s_w);
    cJSON_AddNumberToObject(o, "height", s_h);
    cJSON_AddNumberToObject(o, "pix_thr", c->pix_thr);
    cJSON_AddNumberToObject(o, "persist", c->persist);
    cJSON_AddNumberToObject(o, "glare_frac", c->glare_frac);
    cJSON_AddNumberToObject(o, "latch_s", c->latch_s);
    cJSON_AddNumberToObject(o, "adapt", c->adapt);
    cJSON_AddNumberToObject(o, "target", c->target);
    cJSON_AddNumberToObject(o, "exposure", c->exposure);
    cJSON_AddNumberToObject(o, "gain", c->gain);
    cJSON *a = cJSON_AddArrayToObject(o, "rois");
    for (int i = 0; i < c->n; i++) {
        const roi_t *r = &c->roi[i];
        cJSON *j = cJSON_CreateObject();
        cJSON_AddStringToObject(j, "name", r->name);
        cJSON_AddNumberToObject(j, "x", r->x);
        cJSON_AddNumberToObject(j, "y", r->y);
        cJSON_AddNumberToObject(j, "w", r->w);
        cJSON_AddNumberToObject(j, "h", r->h);
        cJSON_AddStringToObject(j, "kind", KIND_NAMES[r->kind]);
        cJSON_AddNumberToObject(j, "thr", r->thr);
        cJSON_AddItemToArray(a, j);
    }
    return o;
}

static void num(const cJSON *o, const char *k, double *v)
{
    const cJSON *j = cJSON_GetObjectItem(o, k);
    if (cJSON_IsNumber(j)) *v = j->valuedouble;
}

/* Merge JSON into c. Returns false with a message on a bad value. */
static bool cfg_merge(cfg_t *c, const cJSON *o, char *err, size_t errlen)
{
    double v;
#define FIELD(name, lo, hi, assign)                                                  \
    if (cJSON_GetObjectItem(o, name)) {                                               \
        v = NAN;                                                                      \
        num(o, name, &v);                                                             \
        if (!(v >= (lo) && v <= (hi))) {                                              \
            snprintf(err, errlen, "%s must be %g to %g", name, (double)(lo), (double)(hi)); \
            return false;                                                             \
        }                                                                             \
        assign;                                                                       \
    }
    FIELD("pix_thr", 1, 255, c->pix_thr = (int)v);
    FIELD("persist", 1, 60, c->persist = (int)v);
    FIELD("glare_frac", 0, 1, c->glare_frac = (float)v);
    FIELD("latch_s", 0.2, 600, c->latch_s = (float)v);
    FIELD("adapt", 0, 0.5, c->adapt = (float)v);
    FIELD("target", 10, 240, c->target = (int)v);
    FIELD("exposure", -1, 1e7, c->exposure = (int32_t)v);
    FIELD("gain", -1, 1e7, c->gain = (int32_t)v);
#undef FIELD
    const cJSON *a = cJSON_GetObjectItem(o, "rois");
    if (a) {
        if (!cJSON_IsArray(a) || cJSON_GetArraySize(a) > MAX_ROI) {
            snprintf(err, errlen, "rois must be a list of at most %d", MAX_ROI);
            return false;
        }
        c->n = 0;
        const cJSON *j;
        cJSON_ArrayForEach(j, a) {
            roi_t r = {0};
            const cJSON *name = cJSON_GetObjectItem(j, "name");
            snprintf(r.name, sizeof(r.name), "%s", cJSON_IsString(name) ? name->valuestring : "roi");
            double x = -1, y = -1, w = 0, h = 0;
            num(j, "x", &x);
            num(j, "y", &y);
            num(j, "w", &w);
            num(j, "h", &h);
            if (x < 0 || y < 0 || w < 2 || h < 2 || x + w > s_w || y + h > s_h) {
                snprintf(err, errlen, "roi %s is outside the %dx%d frame", r.name, s_w, s_h);
                return false;
            }
            r.x = (int)x; r.y = (int)y; r.w = (int)w; r.h = (int)h;
            const cJSON *kind = cJSON_GetObjectItem(j, "kind");
            r.kind = KIND_NEEDLE;
            for (int k = 0; k < 3; k++) {
                if (cJSON_IsString(kind) && strcmp(kind->valuestring, KIND_NAMES[k]) == 0) r.kind = k;
            }
            double thr = KIND_THR[r.kind];
            num(j, "thr", &thr);
            r.thr = (float)thr;
            c->roi[c->n++] = r;
        }
    }
    return true;
}

static void cfg_save(const cfg_t *c)
{
    cJSON *o = cfg_to_json(c);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    nvs_handle_t h;
    if (s && nvs_open("cam", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_str(h, "cfg", s);
        nvs_commit(h);
        nvs_close(h);
    }
    free(s);
}

static void cfg_load(cfg_t *c)
{
    defaults(c);
    nvs_handle_t h;
    if (nvs_open("cam", NVS_READONLY, &h) != ESP_OK) return;
    size_t len = 0;
    if (nvs_get_str(h, "cfg", NULL, &len) == ESP_OK && len > 1) {
        char *s = malloc(len);
        if (s && nvs_get_str(h, "cfg", s, &len) == ESP_OK) {
            cJSON *o = cJSON_Parse(s);
            char err[80];
            cfg_t tmp = *c;
            if (o && cfg_merge(&tmp, o, err, sizeof(err))) *c = tmp;
            cJSON_Delete(o);
        }
        free(s);
    }
    nvs_close(h);
}

static void apply_sensor(const cfg_t *c)
{
    if (c->exposure >= 0) cam_set_exposure(c->exposure);
    if (c->gain >= 0) cam_set_gain(c->gain);
}

/* ---------- public, other tasks ---------- */

char *det_config_json(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    cJSON *o = cfg_to_json(s_has_pending ? &s_pending : &s_cfg);
    xSemaphoreGive(s_lock);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    return s;
}

esp_err_t det_set_config(const char *json, char *err, size_t errlen)
{
    cJSON *o = cJSON_Parse(json);
    if (!o) {
        snprintf(err, errlen, "not JSON");
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    cfg_t c = s_has_pending ? s_pending : s_cfg;
    bool ok = cfg_merge(&c, o, err, errlen);
    if (ok) {
        s_pending = c;
        s_has_pending = true;
    }
    xSemaphoreGive(s_lock);
    cJSON_Delete(o);
    if (!ok) return ESP_ERR_INVALID_ARG;
    cfg_save(&c);
    return ESP_OK;
}

void det_rebaseline(void) { s_want_rebase = true; }
void det_autoexposure(int target) { s_want_ae = target > 0 ? target : -1; }

esp_err_t det_copy_latest(uint8_t *dst, bool overlay)
{
    if (!s_latest) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memcpy(dst, s_latest, (size_t)s_w * s_h);
    cfg_t c = s_cfg;
    bool act[MAX_ROI];
    for (int i = 0; i < c.n; i++) act[i] = s_st[i].active;
    xSemaphoreGive(s_lock);
    if (overlay) {
        for (int i = 0; i < c.n; i++) {
            const roi_t *r = &c.roi[i];
            const uint8_t col = act[i] ? 255 : (r->kind == KIND_REF ? 0 : 200);
            for (int x = r->x; x < r->x + r->w; x++) {
                dst[r->y * s_w + x] = col;
                dst[(r->y + r->h - 1) * s_w + x] = col;
            }
            for (int y = r->y; y < r->y + r->h; y++) {
                dst[y * s_w + r->x] = col;
                dst[y * s_w + r->x + r->w - 1] = col;
            }
        }
    }
    return ESP_OK;
}

esp_err_t det_view(uint8_t *gray, det_view_t *v)
{
    if (!s_ready || !s_latest) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memcpy(gray, s_latest, (size_t)s_w * s_h);
    v->n = s_cfg.n;
    for (int i = 0; i < s_cfg.n; i++) {
        const roi_t *r = &s_cfg.roi[i];
        v->roi[i] = (det_roi_view_t){.x = r->x, .y = r->y, .w = r->w, .h = r->h,
                                     .kind = KIND_NAMES[r->kind][0], .active = s_st[i].active};
    }
    v->events = s_seq;
    v->glare = s_glare;
    v->learning = s_learn > 0;
    v->autoexposure = s_ae.phase != 0;
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

char *det_status_json(void)
{
    cam_range_t e = {0}, g = {0};
    cam_get_exposure(&e);
    cam_get_gain(&g);
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "fps", cam_fps());
    cJSON_AddNumberToObject(o, "exposure", e.cur);
    cJSON_AddNumberToObject(o, "exposure_min", e.min);
    cJSON_AddNumberToObject(o, "exposure_max", e.max);
    cJSON_AddNumberToObject(o, "gain", g.cur);
    cJSON_AddNumberToObject(o, "gain_min", g.min);
    cJSON_AddNumberToObject(o, "gain_max", g.max);
    cJSON_AddBoolToObject(o, "learning", s_learn > 0);
    cJSON_AddBoolToObject(o, "autoexposure", s_ae.phase != 0);
    cJSON_AddBoolToObject(o, "glare", s_glare);
    cJSON_AddNumberToObject(o, "events", s_seq);
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    return s;
}

/* ---------- camera task ---------- */

static int roi_mean(const uint8_t *g, const roi_t *r)
{
    uint32_t sum = 0;
    for (int y = r->y; y < r->y + r->h; y++) {
        const uint8_t *p = g + y * s_w;
        for (int x = r->x; x < r->x + r->w; x++) sum += p[x];
    }
    return (int)(sum / (uint32_t)(r->w * r->h));
}

static int meter(const uint8_t *g)
{
    if (s_cfg.n == 0) {
        const roi_t all = {.x = 0, .y = 0, .w = s_w, .h = s_h};
        return roi_mean(g, &all);
    }
    int64_t sum = 0, n = 0;
    for (int i = 0; i < s_cfg.n; i++) {
        const roi_t *r = &s_cfg.roi[i];
        sum += (int64_t)roi_mean(g, r) * r->w * r->h;
        n += r->w * r->h;
    }
    return (int)(sum / n);
}

static void base_set(const uint8_t *g, const roi_t *r, int k)   /* k: running average weight 1/k */
{
    for (int y = r->y; y < r->y + r->h; y++) {
        uint16_t *b = s_base + y * s_w;
        const uint8_t *p = g + y * s_w;
        for (int x = r->x; x < r->x + r->w; x++) {
            const int v = p[x] << 8;
            b[x] = (uint16_t)(k <= 1 ? v : b[x] + (v - b[x]) / k);
        }
    }
}

static void base_adapt(const uint8_t *g, const roi_t *r, int shift)
{
    for (int y = r->y; y < r->y + r->h; y++) {
        uint16_t *b = s_base + y * s_w;
        const uint8_t *p = g + y * s_w;
        for (int x = r->x; x < r->x + r->w; x++) b[x] = (uint16_t)(b[x] + (((p[x] << 8) - b[x]) >> shift));
    }
}

static float roi_score(const uint8_t *g, const roi_t *r)
{
    int32_t sum_c = 0, sum_b = 0;
    const int n = r->w * r->h;
    for (int y = r->y; y < r->y + r->h; y++) {
        const uint8_t *p = g + y * s_w;
        const uint16_t *b = s_base + y * s_w;
        for (int x = r->x; x < r->x + r->w; x++) {
            sum_c += p[x];
            sum_b += b[x] >> 8;
        }
    }
    const int shift = r->kind == KIND_NEEDLE ? (sum_c - sum_b) / n : 0;
    const int thr = s_cfg.pix_thr;
    int count = 0;
    for (int y = r->y; y < r->y + r->h; y++) {
        const uint8_t *p = g + y * s_w;
        const uint16_t *b = s_base + y * s_w;
        for (int x = r->x; x < r->x + r->w; x++) {
            const int d = p[x] - (b[x] >> 8) - shift;
            if (d > thr || d < -thr) count++;
        }
    }
    return (float)count / (float)n;
}

static void compose_hit(const uint8_t *g, const roi_t *r, int id)
{
    /* left: baseline, right: this frame, the region boxed in both */
    const int W = s_w * 2;
    for (int y = 0; y < s_h; y++) {
        uint8_t *o = s_compose + y * W;
        const uint16_t *b = s_base + y * s_w;
        for (int x = 0; x < s_w; x++) o[x] = b[x] >> 8;
        memcpy(o + s_w, g + y * s_w, s_w);
    }
    for (int side = 0; side < 2; side++) {
        const int ox = side * s_w;
        for (int x = r->x; x < r->x + r->w; x++) {
            s_compose[r->y * W + ox + x] = 255;
            s_compose[(r->y + r->h - 1) * W + ox + x] = 255;
        }
        for (int y = r->y; y < r->y + r->h; y++) {
            s_compose[y * W + ox + r->x] = 255;
            s_compose[y * W + ox + r->x + r->w - 1] = 255;
        }
    }
    uint8_t *jpg = NULL;
    size_t len = 0;
    if (jpeg_gray(s_compose, W, s_h, &jpg, &len) == ESP_OK) s_on_hit(id, jpg, len);
}

static void reset_states(void)
{
    memset(s_st, 0, sizeof(s_st));
    s_glare = false;
    s_glare_n = 0;
    s_learn = LEARN_FRAMES;
}

static void autoexposure_step(const uint8_t *g, int64_t t)
{
    if (s_ae.wait > 0) {
        s_ae.wait--;
        return;
    }
    const int m = meter(g);
    const int target = s_ae.target;
    if (m < target) s_ae.lo = s_ae.cur; else s_ae.hi = s_ae.cur;
    const bool done_range = s_ae.hi - s_ae.lo <= 1 || ++s_ae.steps >= 12;
    if (done_range) {
        if (s_ae.phase == 1 && m < target * 7 / 10) {
            /* exposure at its longest and still dark: search the gain too */
            cam_range_t gr;
            if (cam_get_gain(&gr) == ESP_OK && gr.max > gr.cur) {
                s_ae.phase = 2;
                s_ae.lo = gr.cur;
                s_ae.hi = gr.max;
                s_ae.steps = 0;
                s_ae.cur = (s_ae.lo + s_ae.hi) / 2;
                cam_set_gain(s_ae.cur);
                s_ae.wait = 5;
                return;
            }
        }
        cam_range_t e = {0}, gr = {0};
        cam_get_exposure(&e);
        cam_get_gain(&gr);
        s_ae.phase = 0;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_cfg.exposure = e.cur;
        s_cfg.gain = gr.cur;
        s_cfg.target = target;
        cfg_t c = s_cfg;
        xSemaphoreGive(s_lock);
        cfg_save(&c);
        cJSON *o = event("autoexp", t);
        cJSON_AddNumberToObject(o, "exposure", e.cur);
        cJSON_AddNumberToObject(o, "gain", gr.cur);
        cJSON_AddNumberToObject(o, "mean", m);
        cJSON_AddNumberToObject(o, "target", target);
        emit(o);
        ESP_LOGI(TAG, "Auto exposure done: exposure %ld, gain %ld, mean %d (target %d)", (long)e.cur, (long)gr.cur, m, target);
        reset_states();
        return;
    }
    s_ae.cur = (s_ae.lo + s_ae.hi + 1) / 2;
    if (s_ae.phase == 1) cam_set_exposure(s_ae.cur); else cam_set_gain(s_ae.cur);
    s_ae.wait = 5;   /* a few frames for the sensor to apply it */
}

static void start_autoexposure(int target)
{
    cam_range_t e, g;
    if (cam_get_exposure(&e) != ESP_OK) {
        ESP_LOGW(TAG, "Exposure can't be set on this sensor");
        return;
    }
    if (cam_get_gain(&g) == ESP_OK) cam_set_gain(g.def > g.min ? g.def : g.min);
    s_ae.phase = 1;
    s_ae.lo = e.min;
    s_ae.hi = e.max;
    s_ae.steps = 0;
    s_ae.target = target;
    s_ae.cur = (e.min + e.max) / 2;
    cam_set_exposure(s_ae.cur);
    s_ae.wait = 5;
    ESP_LOGI(TAG, "Auto exposure: target mean %d", target);
}

void det_frame(const uint8_t *g, int w, int h, int64_t t)
{
    if (!s_ready) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memcpy(s_latest, g, (size_t)w * h);
    bool changed = false;
    if (s_has_pending) {
        s_cfg = s_pending;
        s_has_pending = false;
        changed = true;
    }
    xSemaphoreGive(s_lock);
    if (changed) {
        apply_sensor(&s_cfg);
        reset_states();
    }
    if (s_want_ae) {
        start_autoexposure(s_want_ae > 0 ? s_want_ae : s_cfg.target);
        s_want_ae = 0;
    }
    if (s_ae.phase) {
        autoexposure_step(g, t);
        return;
    }
    if (s_want_rebase) {
        s_want_rebase = false;
        reset_states();
    }
    if (s_learn > 0) {
        const int k = LEARN_FRAMES - s_learn + 1;
        for (int i = 0; i < s_cfg.n; i++) base_set(g, &s_cfg.roi[i], k);
        if (--s_learn == 0) emit(event("baseline", t));
        return;
    }

    /* scores */
    int above_n = 0, others = 0;
    bool ref_above = false;
    for (int i = 0; i < s_cfg.n; i++) {
        const roi_t *r = &s_cfg.roi[i];
        state_t *st = &s_st[i];
        st->score = roi_score(g, r);
        const bool above = st->score >= r->thr;
        if (r->kind == KIND_REF) {
            ref_above |= above;
        } else {
            others++;
            above_n += above;
        }
    }
    const bool glare_now = ref_above || (others >= 3 && s_cfg.glare_frac > 0 && above_n >= others * s_cfg.glare_frac);
    s_glare_n = glare_now ? s_glare_n + 1 : 0;
    if (!s_glare && s_glare_n >= s_cfg.persist) {
        s_glare = true;
        cJSON *o = event("glare", t);
        cJSON_AddNumberToObject(o, "id", ++s_seq);
        cJSON *a = cJSON_AddArrayToObject(o, "rois");
        for (int i = 0; i < s_cfg.n; i++) {
            if (s_st[i].score >= s_cfg.roi[i].thr) cJSON_AddItemToArray(a, cJSON_CreateString(s_cfg.roi[i].name));
        }
        emit(o);
    } else if (s_glare && !glare_now) {
        s_glare = false;
        emit(event("glare_end", t));
    }

    const int adapt_shift = s_cfg.adapt > 0 ? (int)lroundf(-log2f(s_cfg.adapt)) : 0;
    for (int i = 0; i < s_cfg.n; i++) {
        const roi_t *r = &s_cfg.roi[i];
        state_t *st = &s_st[i];
        if (r->kind == KIND_REF) {
            if (!glare_now && adapt_shift) base_adapt(g, r, adapt_shift < 1 ? 1 : adapt_shift);
            continue;
        }
        const bool above = st->score >= r->thr;
        st->above = above ? st->above + 1 : 0;
        st->below = above ? 0 : st->below + 1;
        if (!st->active) {
            if (above && st->above >= s_cfg.persist && !glare_now && !s_glare) {
                st->active = true;
                st->since = t;
                st->id = ++s_seq;
                st->peak = st->score;
                cJSON *o = event("start", t);
                cJSON_AddNumberToObject(o, "id", st->id);
                cJSON_AddStringToObject(o, "roi", r->name);
                cJSON_AddNumberToObject(o, "score", st->score);
                emit(o);
                compose_hit(g, r, st->id);
            } else if (!above && !glare_now && adapt_shift && st->score < r->thr / 2) {
                base_adapt(g, r, adapt_shift < 1 ? 1 : adapt_shift);
            }
            continue;
        }
        if (st->score > st->peak) st->peak = st->score;
        if (st->below >= s_cfg.persist) {
            st->active = false;
            cJSON *o = event("end", t);
            cJSON_AddNumberToObject(o, "id", st->id);
            cJSON_AddStringToObject(o, "roi", r->name);
            cJSON_AddNumberToObject(o, "peak", st->peak);
            cJSON_AddNumberToObject(o, "dur", (double)(t - st->since) / 1000.0);
            emit(o);
        } else if (t - st->since >= (int64_t)(s_cfg.latch_s * 1000)) {
            st->active = false;
            st->above = 0;
            base_set(g, r, 1);
            cJSON *o = event("latched", t);
            cJSON_AddNumberToObject(o, "id", st->id);
            cJSON_AddStringToObject(o, "roi", r->name);
            cJSON_AddNumberToObject(o, "peak", st->peak);
            emit(o);
        }
    }

    if (t - s_last_tick >= 1000) {
        s_last_tick = t;
        cJSON *o = event("tick", t);
        cJSON_AddNumberToObject(o, "fps", roundf(cam_fps() * 10) / 10);
        cJSON_AddBoolToObject(o, "glare", s_glare);
        cJSON *sc = cJSON_AddObjectToObject(o, "scores");
        for (int i = 0; i < s_cfg.n; i++) {
            cJSON_AddNumberToObject(sc, s_cfg.roi[i].name, roundf(s_st[i].score * 1000) / 1000);
        }
        emit(o);
    }
}

esp_err_t det_init(int w, int h, det_event_cb_t on_event, det_hit_cb_t on_hit)
{
    s_w = w;
    s_h = h;
    s_on_event = on_event;
    s_on_hit = on_hit;
    s_lock = xSemaphoreCreateMutex();
    s_base = heap_caps_calloc((size_t)w * h, sizeof(uint16_t), MALLOC_CAP_SPIRAM);
    s_latest = heap_caps_calloc((size_t)w * h, 1, MALLOC_CAP_SPIRAM);
    s_compose = heap_caps_calloc((size_t)w * 2 * h, 1, MALLOC_CAP_SPIRAM);
    if (!s_base || !s_latest || !s_compose) return ESP_ERR_NO_MEM;
    cfg_load(&s_cfg);
    ESP_LOGI(TAG, "%d regions, exposure %ld, gain %ld", s_cfg.n, (long)s_cfg.exposure, (long)s_cfg.gain);
    if (s_cfg.exposure >= 0) {
        apply_sensor(&s_cfg);
    } else {
        s_want_ae = -1;   /* first boot: set exposure once, then it stays */
    }
    reset_states();
    s_ready = true;
    return ESP_OK;
}
