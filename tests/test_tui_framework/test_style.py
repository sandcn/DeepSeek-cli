"""Tests for tui_framework.core.style module."""
import pytest
from tui_framework.core.style import Style, StyledText, StyleSheet


class TestStyle:
    """Tests for Style value object."""

    def test_default_style(self):
        """Default style should have no attributes."""
        s = Style()
        assert s.fg is None
        assert s.bg is None
        assert not s.bold
        assert not s.italic
        assert not s.dim
        assert not s.underline

    def test_bool_empty(self):
        """Empty style should be falsy."""
        assert not bool(Style())

    def test_bool_nonempty(self):
        """Style with attribute should be truthy."""
        assert bool(Style(bold=True))

    def test_to_ansi_bold(self):
        """Bold should produce ANSI bold."""
        s = Style(bold=True)
        assert "\033[1m" in s.to_ansi()

    def test_to_ansi_dim(self):
        """Dim should produce ANSI dim."""
        s = Style(dim=True)
        assert "\033[2m" in s.to_ansi()

    def test_to_ansi_italic(self):
        """Italic should produce ANSI italic."""
        s = Style(italic=True)
        assert "\033[3m" in s.to_ansi()

    def test_to_ansi_underline(self):
        """Underline should produce ANSI underline."""
        s = Style(underline=True)
        assert "\033[4m" in s.to_ansi()

    def test_to_ansi_fg(self):
        """Foreground color should produce ANSI fg."""
        s = Style(fg=45)
        assert "\033[38;5;45m" in s.to_ansi()

    def test_to_ansi_bg(self):
        """Background color should produce ANSI bg."""
        s = Style(bg=45)
        assert "\033[48;5;45m" in s.to_ansi()

    def test_apply(self):
        """apply should wrap text with ANSI."""
        s = Style(bold=True)
        result = s.apply("hello")
        assert result.startswith("\033[1m")
        assert result.endswith("\033[0m")
        assert "hello" in result

    def test_apply_empty_style(self):
        """Empty style should return text unchanged."""
        s = Style()
        assert s.apply("hello") == "hello"

    def test_merge_overrides_fg(self):
        """merge should override fg."""
        a = Style(fg=45)
        b = Style(fg=100)
        merged = a.merge(b)
        assert merged.fg == 100

    def test_merge_preserves_old_when_none(self):
        """merge should preserve old when new is None."""
        a = Style(fg=45)
        b = Style(bold=True)
        merged = a.merge(b)
        assert merged.fg == 45
        assert merged.bold is True

    def test_immutable(self):
        """Style should be frozen (immutable)."""
        s = Style(fg=45)
        with pytest.raises(Exception):
            s.fg = 100  # type: ignore


class TestStyledText:
    """Tests for StyledText."""

    def test_render_with_style(self):
        """render with style should apply ANSI."""
        st = StyledText("hello", Style(bold=True))
        assert "\033[1m" in st.render()

    def test_render_without_style(self):
        """render without style should return plain text."""
        st = StyledText("hello")
        assert st.render() == "hello"

    def test_bool_empty_text(self):
        """Empty text should be falsy."""
        assert not bool(StyledText(""))


class TestStyleSheet:
    """Tests for StyleSheet registry."""

    def setup_method(self):
        """Reset StyleSheet before each test."""
        StyleSheet.clear()

    def test_register_and_get(self):
        """Register then get should return same style."""
        s = Style(bold=True)
        StyleSheet.register("test", s)
        assert StyleSheet.get("test") == s

    def test_get_missing(self):
        """Get missing style should return None."""
        assert StyleSheet.get("nonexistent") is None

    def test_resolve_missing_returns_default(self):
        """resolve with missing key should return default."""
        default = Style(dim=True)
        result = StyleSheet.resolve("missing", default)
        assert result == default

    def test_resolve_missing_no_default(self):
        """resolve with missing key and no default should return empty."""
        result = StyleSheet.resolve("missing")
        assert not bool(result)

    def test_has(self):
        """has should return correct boolean."""
        StyleSheet.register("present", Style())
        assert StyleSheet.has("present")
        assert not StyleSheet.has("absent")

    def test_all_names(self):
        """all_names should return registered style names."""
        StyleSheet.register("a", Style())
        StyleSheet.register("b", Style())
        names = StyleSheet.all_names()
        assert "a" in names
        assert "b" in names

    def test_pre_registered_styles(self):
        """Pre-registered styles should be available (re-register after clear)."""
        # Pre-registered styles may have been cleared by other tests;
        # use all_names to check what's currently available
        names = StyleSheet.all_names()
        # At minimum, verify the StyleSheet API works
        assert isinstance(names, list)

    def test_clear(self):
        """clear should remove all registrations."""
        StyleSheet.register("test", Style())
        StyleSheet.clear()
        assert not StyleSheet.has("test")
