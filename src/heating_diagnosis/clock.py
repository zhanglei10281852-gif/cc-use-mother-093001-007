"""可注入时钟与跨午夜采暖窗口。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol


class Clock(Protocol):
    """时钟协议：诊断、停运、对比中所有“当前时刻”都通过它注入。"""
    def now(self) -> datetime: ...


class SystemClock:
    """生产环境使用的系统时钟。"""
    def now(self) -> datetime:
        return datetime.now()


class FixedClock:
    """测试/回放用时钟，可手动设置与推进。"""
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now


@dataclass(frozen=True)
class HeatingWindow:
    """每日采暖窗口，支持跨午夜（如 20:00 至次日 08:00）。

    按半开区间 [start, end) 处理，避免窗口边界点被两个窗口重复计入。
    """
    start: time = time(20, 0)
    end: time = time(8, 0)

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start

    def contains(self, ts: datetime) -> bool:
        t = ts.time()
        if self.crosses_midnight:
            return t >= self.start or t < self.end
        return self.start <= t < self.end

    def bounds_for_date(self, d: date) -> tuple[datetime, datetime]:
        """返回“开始于日期 d”的窗口边界（跨午夜时结束于次日）。"""
        start = datetime.combine(d, self.start)
        end = datetime.combine(d, self.end)
        if self.crosses_midnight:
            end += timedelta(days=1)
        return start, end

    def intersect(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """返回 [start, end] 与采暖窗口的交集片段，按时间升序。"""
        if end < start:
            raise ValueError("结束时间早于开始时间")
        segments: list[tuple[datetime, datetime]] = []
        day = start.date() - timedelta(days=1)
        last_day = end.date() + timedelta(days=1)
        while day <= last_day:
            ws, we = self.bounds_for_date(day)
            s, e = max(ws, start), min(we, end)
            if s < e:
                segments.append((s, e))
            day += timedelta(days=1)
        return segments
