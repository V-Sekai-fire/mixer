# GPLv3, like the rest of the add-on. Copyright (c) 2026-present K. S. Ernest (iFire) Lee
#
# A console for the relay in Blender: what the add-on does on the relay (joins, poses, meshes published, errors) is
# appended to a text block called "Mixer relay log", which you read in the Text Editor (and it is printed to Blender's
# own console too). Only the last `MAX_LINES` lines are kept.

import logging
import time
from typing import Optional

import bpy

TEXT_NAME = "Mixer relay log"
MAX_LINES = 300
_t0 = time.monotonic()
_logger = logging.getLogger("mixer.relay")


class _TextHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
            lines = text.as_string().splitlines()[-(MAX_LINES - 1) :]
            lines.append(f"{time.monotonic() - _t0:8.2f}s  {record.levelname[0]}  {record.getMessage()}")
            text.clear()
            text.write("\n".join(lines) + "\n")
        except Exception:  # a log must never break the add-on
            pass


_installed: Optional[_TextHandler] = None


def install() -> None:
    """Start writing to the text block. Safe to call more than once."""
    global _installed
    if _installed is None:
        _installed = _TextHandler()
        _logger.addHandler(_installed)
        _logger.setLevel(logging.INFO)


def note(message: str) -> None:
    """One line in the console."""
    install()
    _logger.info(message)
    print("[mixer relay]", message, flush=True)
