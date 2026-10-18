"""空模式提词：图片读取使用 read_image。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def test_images_use_read_image():
    text = read_prompt()
    assert_contains(text, "图片用 `read_image`", "不要用 bash 的 `cat`/`grep`/`sed`/`echo` 等替代")
