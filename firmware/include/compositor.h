// Phase 2 layer compositor for the embodiment firmware.
//
// The renderer maintains a fixed set of layers, each pointing to a
// frame in an atlas. When the state machine (or, for Phase 2 today,
// an API service) changes a layer's frame, the compositor:
//
//   1. Repaints the bbox of the layer's previous frame using the
//      bhumi pixels (or the bg color if no bhumi is set).
//   2. Blits the new frame on top, skipping ATLAS_TRANSPARENT pixels.
//
// ESPHome's ili9xxx driver tracks the dirty rect across all
// `draw_pixel_at` calls and flushes via DMA SPI on `update()`. The
// compositor's job is just to keep blits localized to layers that
// actually changed.
//
// Design doc: docs/plans/embodiment-phase2-design-2026-05-02.md
#pragma once

#include <cstdint>
#include "esphome/components/display/display.h"

#include "atlas_test_atlas.h"

namespace narada {

// Layer ordering — drawn back-to-front. Higher index = on top.
enum LayerId : uint8_t {
  LAYER_BODY = 0,
  LAYER_MOOD,
  LAYER_EYE_LEFT,
  LAYER_EYE_RIGHT,
  LAYER_MOUTH,
  LAYER_MARG_1,
  LAYER_COUNT
};

static constexpr uint16_t FRAME_NONE = 0xFFFF;

struct LayerState {
  uint16_t current_frame = FRAME_NONE;
  uint16_t prev_frame = FRAME_NONE;
  int16_t  anchor_x = 0;
  int16_t  anchor_y = 0;
  bool     dirty = false;
};

class Compositor {
 public:
  // Atlas accessor — one face atlas for Phase 2. Phase 3 may swap to
  // multiple atlases (face + body + marginalia).
  void set_atlas(const AtlasFrame* frames,
                 const uint16_t* pixels,
                 uint16_t frame_count) {
    atlas_frames_ = frames;
    atlas_pixels_ = pixels;
    atlas_frame_count_ = frame_count;
  }

  void set_bg_color(uint16_t rgb565) { bg_color_ = rgb565; bg_dirty_ = true; }
  void set_bhumi(const uint8_t* buffer) { bhumi_ = buffer; bg_dirty_ = true; }

  // Set a layer's frame and anchor. If anything changed, the layer is
  // marked dirty; next render() will repaint it.
  void set_layer(LayerId id, uint16_t frame, int16_t x, int16_t y) {
    if (id >= LAYER_COUNT) return;
    auto& l = layers_[id];
    if (l.current_frame == frame && l.anchor_x == x && l.anchor_y == y) {
      return;
    }
    l.current_frame = frame;
    l.anchor_x = x;
    l.anchor_y = y;
    l.dirty = true;
  }

  void clear_layer(LayerId id) {
    if (id >= LAYER_COUNT) return;
    auto& l = layers_[id];
    if (l.current_frame == FRAME_NONE) return;
    l.current_frame = FRAME_NONE;
    l.dirty = true;
  }

  // Force a full repaint on next render — call after bg/bhumi change.
  void invalidate_all() {
    bg_dirty_ = true;
    for (auto& l : layers_) l.dirty = true;
  }

  // Render dirty layers into the supplied display buffer.
  void render(esphome::display::Display& it) {
    if (atlas_frames_ == nullptr) return;

    if (bg_dirty_) {
      paint_background_(it, 0, 0, 320, 240);
      bg_dirty_ = false;
      // Bg repaint blew away every layer; re-blit them.
      for (auto& l : layers_) l.dirty = true;
    }

    for (uint8_t i = 0; i < LAYER_COUNT; i++) {
      auto& l = layers_[i];
      if (!l.dirty) continue;

      // Repaint the prev frame's bbox with bg/bhumi (only if there
      // was a prev frame — first appearance has nothing to clear).
      if (l.prev_frame != FRAME_NONE && l.prev_frame < atlas_frame_count_) {
        const auto& pf = atlas_frames_[l.prev_frame];
        paint_background_(it,
                          l.anchor_x + pf.ox, l.anchor_y + pf.oy,
                          pf.w, pf.h);
        // Re-blit any layers BELOW this one whose bbox overlaps —
        // they're behind it but the bg repaint cleared them. For
        // Phase 2 we accept overdraw and just mark all lower layers
        // dirty if any layer changed. Future: bbox intersection.
      }

      // Blit current frame.
      if (l.current_frame != FRAME_NONE && l.current_frame < atlas_frame_count_) {
        const auto& cf = atlas_frames_[l.current_frame];
        blit_frame_(it, cf,
                    l.anchor_x + cf.ox, l.anchor_y + cf.oy);
      }

      l.prev_frame = l.current_frame;
      l.dirty = false;
    }
  }

  // Stats for the perf log — number of layers redrawn this frame.
  uint8_t take_dirty_count() {
    uint8_t n = 0;
    for (auto& l : layers_) if (l.dirty) n++;
    return n;
  }

 private:
  // Paint a rect with bhumi-or-bg-color content.
  void paint_background_(esphome::display::Display& it,
                         int x0, int y0, int w, int h) {
    if (x0 < 0) { w += x0; x0 = 0; }
    if (y0 < 0) { h += y0; y0 = 0; }
    if (x0 + w > 320) w = 320 - x0;
    if (y0 + h > 240) h = 240 - y0;
    if (w <= 0 || h <= 0) return;

    if (bhumi_ != nullptr) {
      for (int dy = 0; dy < h; dy++) {
        const uint8_t* row = bhumi_ + (uint32_t)((y0 + dy) * 320 + x0) * 2;
        for (int dx = 0; dx < w; dx++) {
          uint16_t c = ((uint16_t) row[0] << 8) | row[1];
          it.draw_pixel_at(x0 + dx, y0 + dy, color_from_rgb565_(c));
          row += 2;
        }
      }
    } else {
      auto col = color_from_rgb565_(bg_color_);
      for (int dy = 0; dy < h; dy++) {
        for (int dx = 0; dx < w; dx++) {
          it.draw_pixel_at(x0 + dx, y0 + dy, col);
        }
      }
    }
  }

  // Blit one atlas frame at (px, py), respecting transparent key.
  void blit_frame_(esphome::display::Display& it,
                   const AtlasFrame& f, int px, int py) {
    const uint16_t* src = atlas_pixels_ + f.pixel_offset;
    for (int dy = 0; dy < f.h; dy++) {
      int yy = py + dy;
      if (yy < 0 || yy >= 240) { src += f.w; continue; }
      for (int dx = 0; dx < f.w; dx++) {
        int xx = px + dx;
        uint16_t c = src[dx];
        if (c == ATLAS_TRANSPARENT) continue;
        if (xx < 0 || xx >= 320) continue;
        it.draw_pixel_at(xx, yy, color_from_rgb565_(c));
      }
      src += f.w;
    }
  }

  static esphome::Color color_from_rgb565_(uint16_t c) {
    uint8_t r = ((c >> 11) & 0x1F) << 3;
    uint8_t g = ((c >> 5) & 0x3F) << 2;
    uint8_t b = (c & 0x1F) << 3;
    return esphome::Color(r, g, b);
  }

  const AtlasFrame* atlas_frames_ = nullptr;
  const uint16_t*   atlas_pixels_ = nullptr;
  uint16_t          atlas_frame_count_ = 0;

  uint16_t bg_color_ = 0x0000;
  const uint8_t* bhumi_ = nullptr;
  bool bg_dirty_ = true;

  LayerState layers_[LAYER_COUNT] = {};
};

// Singleton accessor. ESPHome's `globals:` block can't hold a
// non-trivial C++ type cleanly (it generates the variable declaration
// before user includes are processed), so the compositor lives in a
// Meyer's singleton instead. All YAML lambdas reach it via this.
inline Compositor& comp() {
  static Compositor instance;
  return instance;
}

}  // namespace narada
