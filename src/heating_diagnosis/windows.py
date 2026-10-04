"""采暖窗口：支持跨午夜的每日窗口，当前窗口由可注入时钟解析。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class DailyHeatingWindow:
    """每日采暖窗口；start > end 表示跨午夜（如 22:00–06:00）。"""

    start: time
    end: time

    def crosses_midnight(self) -> bool:
        return self.start > self.end

    def contains(self, ts: datetime) -> bool:
        t = ts.time()
        if not self.crosses_midnight():
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def current_interval(self, clock) -> tuple[datetime, datetime]:
        """按注入时钟解析包含 now 的绝对窗口区间；不在窗口内则抛错。"""
        now = clock.now()
        today = now.date()
        if self.crosses_midnight():
            start_today = datetime.combine(today, self.start)
            if now >= start_today:
                return start_today, datetime.combine(today + timedelta(days=1), self.end)
            end_today = datetime.combine(today, self.end)
            if now < end_today:
                return datetime.combine(today - timedelta(days=1), self.start), end_today
            raise ValueError("当前时刻不在采暖窗口内")
        start = datetime.combine(today, self.start)
        end = datetime.combine(today, self.end)
        if start <= now < end:
            return start, end
        raise ValueError("当前时刻不在采暖窗口内")
