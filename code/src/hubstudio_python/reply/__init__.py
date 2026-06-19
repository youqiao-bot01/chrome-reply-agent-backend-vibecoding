"""在线回复：Chroma 检索 + DeepSeek 润色；离线流水线见 ``kb/service/pipelines/``。"""

from hubstudio_python.reply.interface.app import create_app, serve_fastapi
from hubstudio_python.reply.service.generate import (
    GenerateReplyRequest,
    GenerateReplyResult,
    generate_reply,
    result_to_dict,
)

__all__ = [
    "GenerateReplyRequest",
    "GenerateReplyResult",
    "generate_reply",
    "result_to_dict",
    "create_app",
    "serve_fastapi",
]
