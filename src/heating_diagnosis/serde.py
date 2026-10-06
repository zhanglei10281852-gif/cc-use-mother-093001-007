"""dataclass 与 JSON 可序列化结构的双向转换。

datetime 编码为 ISO 字符串；解码依靠目标 dataclass 的类型标注还原。
"""
from __future__ import annotations

import dataclasses
import types
import typing
from datetime import datetime


def to_jsonable(obj):
    """把 dataclass / datetime / 容器转换为 JSON 可序列化结构。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    raise TypeError(f"不支持序列化的类型: {type(obj)!r}")


def from_jsonable(tp, data):
    """按目标类型标注把 JSON 结构还原为 dataclass 实例。"""
    return _convert(tp, data)


def _convert(tp, data):
    if data is None:
        return None
    origin = typing.get_origin(tp)
    if origin is None:
        if tp is datetime:
            return datetime.fromisoformat(data)
        if tp in (str, int, float, bool, dict, list) or tp is typing.Any:
            return data
        if isinstance(tp, type) and dataclasses.is_dataclass(tp):
            hints = typing.get_type_hints(tp)
            kwargs = {}
            for f in dataclasses.fields(tp):
                if f.name in data:
                    kwargs[f.name] = _convert(hints[f.name], data[f.name])
            return tp(**kwargs)
        raise TypeError(f"不支持反序列化的类型: {tp!r}")
    if origin in (typing.Union, types.UnionType):
        last_err: Exception | None = None
        for arg in typing.get_args(tp):
            if arg is type(None):
                continue
            try:
                return _convert(arg, data)
            except Exception as exc:  # noqa: BLE001 - 依次尝试联合类型分支
                last_err = exc
        raise TypeError(f"无法按 {tp!r} 反序列化: {data!r}") from last_err
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        return [_convert(item_tp, x) for x in data]
    if origin is tuple:
        args = typing.get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert(args[0], x) for x in data)
        return tuple(_convert(a, x) for a, x in zip(args, data))
    if origin is dict:
        _, value_tp = typing.get_args(tp)
        return {k: _convert(value_tp, v) for k, v in data.items()}
    raise TypeError(f"不支持反序列化的类型: {tp!r}")
