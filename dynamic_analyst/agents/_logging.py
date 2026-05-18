import json
import logging

from ..session_state import get_active_session


logger = logging.getLogger("adk_server")


def tool_log(event: str, **fields) -> None:
    sid = get_active_session() or "-"
    logger.info(json.dumps({"event": event, "session_id": sid, **fields}, default=str))
