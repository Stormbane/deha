// Phase 3 sandhi player — on-device.
//
// Phase 1 measurement showed that per-pixel `draw_pixel_at` was the
// reason sandhi played at ~5 fps: the lambda spent 200 ms per frame
// before the SPI flush even started. Phase 3 uses the bulk
// `draw_pixels_at` API (RGB565 packed buffer, single dirty-rect
// flush) to push entire frames in one shot.
//
// Format spec / design: docs/plans/embodiment-phase3-sandhi-2026-05-02.md
#pragma once

#include <cstdint>
#include <cstring>

#include "esp_heap_caps.h"
#include "esphome/components/display/display.h"
#include "esphome/components/display/display_color_utils.h"

#include "compositor.h"

namespace narada {

struct SandhiAsset {
  const uint8_t*   rle_data;
  const uint32_t*  frame_offsets;
  uint16_t         frame_count;
  uint16_t         width;
  uint16_t         height;
  uint16_t         fps;
};

class SandhiPlayer {
 public:
  // Begin playing `asset`. Allocates the scratch buffer in PSRAM on
  // first call (kept across stop/start to avoid alloc churn). Returns
  // false if the buffer alloc failed — caller falls back to the
  // sprite compositor unchanged.
  bool start(const SandhiAsset* asset, uint32_t now_us) {
    if (asset == nullptr) return false;
    if (scratch_ == nullptr) {
      uint32_t bytes = (uint32_t) asset->width * asset->height * 2;
      scratch_ = (uint8_t*) heap_caps_malloc(
          bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
      if (scratch_ == nullptr) {
        ESP_LOGE("sandhi", "scratch alloc failed (%u bytes)", (unsigned) bytes);
        return false;
      }
      scratch_capacity_ = bytes;
    }
    asset_ = asset;
    current_frame_ = 0;
    next_frame_us_ = now_us;
    started_us_ = now_us;
    ESP_LOGI("sandhi", "start: %u frames @ %u fps",
             (unsigned) asset->frame_count, (unsigned) asset->fps);
    return true;
  }

  void stop() {
    if (asset_ == nullptr) return;
    ESP_LOGI("sandhi", "stop after frame %u/%u",
             (unsigned) current_frame_, (unsigned) asset_->frame_count);
    asset_ = nullptr;
    // Compositor's full state is invalid (we wrote over its
    // framebuffer); next render() must repaint everything.
    comp().invalidate_all();
  }

  bool active() const { return asset_ != nullptr; }

  // Per-frame tick. Pulls the current frame to the display when due.
  // Returns true if a frame was rendered this tick.
  // When the sandhi reaches its last frame, it auto-stops; the
  // compositor will pick up on the next display update.
  bool render(esphome::display::Display& it, uint32_t now_us) {
    if (asset_ == nullptr) return false;
    if ((int32_t)(now_us - next_frame_us_) < 0) return false;

    if (current_frame_ >= asset_->frame_count) {
      stop();
      return false;
    }

    decode_frame_(current_frame_);
    it.draw_pixels_at(
        0, 0, asset_->width, asset_->height,
        scratch_,
        esphome::display::COLOR_ORDER_RGB,
        esphome::display::COLOR_BITNESS_565,
        /*big_endian=*/true
    );

    current_frame_++;
    uint32_t step_us = 1000000u / (asset_->fps ? asset_->fps : 30);
    next_frame_us_ = now_us + step_us;
    return true;
  }

  uint32_t elapsed_us(uint32_t now_us) const {
    return now_us - started_us_;
  }

 private:
  void decode_frame_(uint16_t frame_index) {
    const uint8_t* p = asset_->rle_data + asset_->frame_offsets[frame_index];
    uint8_t* dst = scratch_;
    uint32_t dst_remaining = scratch_capacity_;

    while (true) {
      uint8_t run = *p++;
      if (run == 0) break;          // end-of-frame marker
      uint8_t hi = *p++;
      uint8_t lo = *p++;
      // Bulk fill: write `run` copies of (hi, lo) — big-endian RGB565
      // matches what draw_pixels_at expects with big_endian=true.
      uint32_t to_write = (uint32_t) run * 2;
      if (to_write > dst_remaining) to_write = dst_remaining;
      uint32_t pixels = to_write / 2;
      for (uint32_t i = 0; i < pixels; i++) {
        dst[0] = hi;
        dst[1] = lo;
        dst += 2;
      }
      dst_remaining -= to_write;
      if (dst_remaining == 0) break;
    }
  }

  const SandhiAsset* asset_ = nullptr;
  uint16_t current_frame_ = 0;
  uint32_t next_frame_us_ = 0;
  uint32_t started_us_ = 0;
  uint8_t* scratch_ = nullptr;
  uint32_t scratch_capacity_ = 0;
};

inline SandhiPlayer& sandhi() {
  static SandhiPlayer instance;
  return instance;
}

}  // namespace narada
