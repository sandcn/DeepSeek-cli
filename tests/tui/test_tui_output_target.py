"""src/tui/_output_target — IOutputTarget 运行时协议单元测试。

覆盖：
  - runtime_checkable isinstance 判定
  - 缺失方法/属性的类被拒绝
  - 真实消费方（BaseDisplay / 协议实现）满足协议
"""

from __future__ import annotations

from src.tui._output_target import IOutputTarget


class _GoodTarget:
    def write_line(self, text: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def display_messages(self, messages: list, speed: int = 0) -> None:
        pass


class _MissingFlush:
    def write_line(self, text: str) -> None:
        pass

    def display_messages(self, messages: list, speed: int = 0) -> None:
        pass


def test_protocol_is_runtime_checkable():
    assert isinstance(_GoodTarget(), IOutputTarget)


def test_protocol_rejects_missing_method():
    assert not isinstance(_MissingFlush(), IOutputTarget)


def test_protocol_rejects_plain_object():
    assert not isinstance(object(), IOutputTarget)


def test_protocol_duck_types_without_inheritance():
    """无需继承即可满足协议（结构性子类型）。"""
    assert isinstance(_GoodTarget(), IOutputTarget)


def test_base_display_consumes_protocol():
    """BaseDisplay 通过类型注解消费 IOutputTarget（协议消费方回归护栏）。"""
    import inspect

    from src.tui._base_display import BaseDisplay
    from src.tui._output_target import IOutputTarget

    sig = inspect.signature(BaseDisplay.__init__)
    ann = sig.parameters["output_target"].annotation
    # 字符串前向引用 "IOutputTarget"
    assert "IOutputTarget" in str(ann)
    assert IOutputTarget.__name__ == "IOutputTarget"
