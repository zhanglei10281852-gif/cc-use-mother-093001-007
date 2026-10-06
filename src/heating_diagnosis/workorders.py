"""工单生命周期：调度员确认建议生成工单，执行结果反馈诊断结论。"""
from __future__ import annotations

from dataclasses import replace

from . import models


class WorkOrderService:
    def __init__(self, store, clock) -> None:
        self._store = store
        self._clock = clock

    def confirm(self, suggestion_id: str, operator: str) -> models.WorkOrder:
        """确认建议并生成工单；同一建议重复确认返回已有工单，不重复派单。"""
        suggestion = self._store.suggestions.get(suggestion_id)
        if suggestion is None:
            raise KeyError(f"建议不存在: {suggestion_id}")
        if suggestion.status == "superseded":
            raise ValueError("建议已被新规则版本取代，请使用最新建议")
        existing = self._store.order_for_suggestion(suggestion_id)
        if existing is not None:
            return existing
        order = models.WorkOrder(
            order_id=self._store.next_id("WO"),
            suggestion_id=suggestion_id,
            event_id=suggestion.event_id,
            node_id=suggestion.target_node_id,
            action_type=suggestion.action_type,
            status="open",
            created_by=operator,
            created_at=self._clock.now(),
        )
        self._store.work_orders[order.order_id] = order
        self._store.suggestions[suggestion_id] = replace(suggestion, status="confirmed")
        return order

    def complete(self, order_id: str, result: models.WorkOrderResult) -> models.DiagnosisConclusion:
        """工单执行结果反馈到诊断结论；解决时同步关闭事件。"""
        order = self._store.work_orders.get(order_id)
        if order is None:
            raise KeyError(f"工单不存在: {order_id}")
        if order.status == "done":
            raise ValueError("工单已完成，不能重复反馈")
        now = self._clock.now()
        order.status = "done"
        order.completed_at = now
        order.result = result

        conclusion = self._store.conclusions[order.event_id]
        if result.confirmed_cause == "no_issue":
            conclusion.status = "false_positive"
        elif result.confirmed_cause == conclusion.category:
            conclusion.status = "confirmed"
        else:
            conclusion.status = "revised"
            conclusion.revised_category = result.confirmed_cause
        conclusion.feedback.append(models.FeedbackRecord(order_id, result, now))
        conclusion.updated_at = now

        if result.resolved:
            event = self._store.events[order.event_id]
            if event.status == "open":
                event.status = "closed"
                event.closed_at = now
        return conclusion
