from hubstudio_python.reply.service.toolant_whatsapp import (
    classify_whatsapp_response,
    infer_toolant_conversion_beat,
    try_handle_whatsapp_response,
)


def test_joined_sample_review():
    assert classify_whatsapp_response("I joined the group!") == "joined"
    beat = infer_toolant_conversion_beat(
        creator_progress=["已加窗", "已申请批样"],
        response_type="joined",
    )
    assert beat == "sample_review"


def test_no_whatsapp_showcase():
    assert classify_whatsapp_response("I don't have WhatsApp") == "no_whatsapp"
    beat = infer_toolant_conversion_beat(
        creator_progress=["未加窗", "未申请批样"],
        response_type="no_whatsapp",
    )
    assert beat == "showcase"


def test_not_found():
    assert classify_whatsapp_response("I can't find the group link") == "not_found"


def test_shared_number():
    assert classify_whatsapp_response("My WhatsApp is +1 628-268-7659") == "shared_number"


def test_handle_joined_reply():
    result = try_handle_whatsapp_response(
        shop="toolant",
        creator_id="",
        creator_name="risingwithkat",
        latest_message="I joined!",
        creator_progress=["未加窗", "未申请批样"],
        monthly_gmv=1500,
    )
    assert result is not None
    assert result.response_type == "joined"
    assert result.join_wa_value == 1
    assert "showcase" in result.reply.lower() or "http" in result.reply.lower()


if __name__ == "__main__":
    test_joined_sample_review()
    test_no_whatsapp_showcase()
    test_not_found()
    test_shared_number()
    test_handle_joined_reply()
    print("ok")
