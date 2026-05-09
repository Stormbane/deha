// Sandhi catalog — table of available sandhis indexed by ID. The
// bridge picks one with `trigger_sandhi(id: int)`. v1 holds only the
// synthetic test sandhi; real-art entries land in Phase 4.
#pragma once

#include "sandhi_player.h"
#include "sandhi_test_sandhi.h"

namespace narada {

static const SandhiAsset kSandhiTest = {
  /*rle_data=*/      sandhi_test_sandhi_rle,
  /*frame_offsets=*/ sandhi_test_sandhi_offsets,
  /*frame_count=*/   SANDHI_TEST_SANDHI_FRAME_COUNT,
  /*width=*/         SANDHI_TEST_SANDHI_WIDTH,
  /*height=*/        SANDHI_TEST_SANDHI_HEIGHT,
  /*fps=*/           SANDHI_TEST_SANDHI_FPS,
};

inline const SandhiAsset* sandhi_by_id(int id) {
  switch (id) {
    case 0: return &kSandhiTest;
    default: return nullptr;
  }
}

}  // namespace narada
