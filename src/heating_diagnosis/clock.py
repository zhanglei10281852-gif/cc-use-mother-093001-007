"""可注入时钟：跨午夜采暖窗口与临时停运的时间判定统一依赖它。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now()


class FixedClock:
    """测试与演示用：now() 返回固定时刻，可推进。"""

    def __init__(self, moment: datetime):
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        self._moment = moment

    def advance(self, delta: timedelta) -> None:
        self._moment = self._moment + delta
