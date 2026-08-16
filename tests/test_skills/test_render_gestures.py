"""渲染与手势测试。"""

from src.skills import (
    SkillDefinition,
    InvocationPolicy,
    render_skill_content,
    scan_skill_gestures,
    inject_skill_gestures,
)


def _skill(name="demo", content="# 正文", directory="/skills/demo",
           description="描述", provider="local"):
    return SkillDefinition(
        name=name, description=description, invocation=InvocationPolicy(),
        source="project", provider=provider, rank=100,
        directory=directory, content=content,
    )


# ── render ──────────────────────────────────────────────

def test_render_skill_content_structure():
    text = render_skill_content(_skill())
    assert text.startswith('<skill_content name="demo">')
    assert "<skill_resources>" in text
    assert "/skills/demo" in text
    assert "<skill_instructions>" in text
    assert "# 正文" in text
    assert text.endswith("</skill_content>")


def test_render_escapes_name_attr():
    skill = _skill(name='a"b<c&d')
    text = render_skill_content(skill)
    assert 'name="a&quot;b&lt;c&amp;d"' in text


def test_render_without_directory():
    skill = _skill(directory=None)
    text = render_skill_content(skill)
    assert "provider" in text


# ── gestures 扫描 ───────────────────────────────────────

def test_gesture_scan_basic():
    assert scan_skill_gestures("帮我 /code-review 一下") == ["code-review"]
    assert scan_skill_gestures("/alpha 和 /beta 和 /alpha") == ["alpha", "beta"]
    assert scan_skill_gestures("行首 /lead 行尾") == ["lead"]


def test_gesture_scan_rejects_non_names():
    # 路径 / 分数 / URL / 非法字符
    assert scan_skill_gestures("路径 C:\\Users\\a 和 5/8 和 http://x.com") == []
    assert scan_skill_gestures("/Bad Name 和 /bad_name") == []
    assert scan_skill_gestures("中缀/foo中间") == []
    assert scan_skill_gestures("/foo。结尾句号") == []


# ── gestures 注入 ───────────────────────────────────────

class FakeAgent:
    def __init__(self):
        self.messages = []

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})


def test_inject_skill_gestures(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    for name, extra in (("alpha", {}), ("user-only", {"disable-model-invocation": "true"})):
        d = proj / ".skills" / name
        d.mkdir(parents=True)
        lines = ["---", f"name: {name}", f"description: 技能 {name}"]
        lines.extend(f"{k}: {v}" for k, v in extra.items())
        lines.append("---")
        lines.append(f"# {name} 正文")
        (d / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")

    agent = FakeAgent()
    count = inject_skill_gestures(agent, "请 /alpha 处理", cwd=str(proj))
    assert count == 1
    assert "<skill_content name=\"alpha\">" in agent.messages[0]["content"]

    # user-only 技能可手势调用
    count = inject_skill_gestures(agent, "/user-only", cwd=str(proj))
    assert count == 1

    # 未知技能忽略；无手势不注入
    assert inject_skill_gestures(agent, "/not-exist", cwd=str(proj)) == 0
    assert inject_skill_gestures(agent, "普通消息", cwd=str(proj)) == 0
    assert len(agent.messages) == 2
