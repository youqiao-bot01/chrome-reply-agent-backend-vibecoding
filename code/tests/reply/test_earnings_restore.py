"""avgVV restore 计算测试。"""

from __future__ import annotations

from hubstudio_python.models.shop_restore_values import apply_restore_slots
from hubstudio_python.reply.service.earnings_restore import build_avgvv_restore_extra


def test_a2_cpm_restore_slots():
    extra = build_avgvv_restore_extra(12300, video_count=4)
    assert extra["avgVV"] == "12,300"
    assert extra["avgVV*0.002"] == "24.6"
    assert extra["avgVV*0.004"] == "49.2"
    assert extra["total"] == "98.4"
    assert extra["videoCount"] == "4"

    body = (
        "so {avgVV} views ≈ ${avgVV*0.002} per video). "
        "caps at ${avgVV*0.004}). "
        "Estimate: {videoCount} videos × avg ${avgVV*0.002} ≈ {total}"
    )
    out = apply_restore_slots(body, "toolant", extra=extra)
    assert "12,300" in out
    assert "24.6" in out
    assert "49.2" in out
    assert "98.4" in out
