"""测试 src/webui/server.py — 静态文件服务与安全校验"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# 部分功能用 pytest 测试，handle_static 用 fixture + unittest.mock


# ═══════════════════════════════════════════════════════════════
# MIME_MAP
# ═══════════════════════════════════════════════════════════════

class TestMimeMap:
    """MIME_MAP 常见扩展名映射正确。"""

    def test_html(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".html"] == "text/html"

    def test_css(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".css"] == "text/css"

    def test_js(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".js"] == "application/javascript"

    def test_mjs(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".mjs"] == "application/javascript"

    def test_json(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".json"] == "application/json"

    def test_png(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".png"] == "image/png"

    def test_svg(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".svg"] == "image/svg+xml"

    def test_ico(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".ico"] == "image/x-icon"

    def test_woff2(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".woff2"] == "font/woff2"

    def test_wasm(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".wasm"] == "application/wasm"

    def test_txt(self):
        from src.webui.server import MIME_MAP
        assert MIME_MAP[".txt"] == "text/plain"

    def test_unknown_extension(self):
        from src.webui.server import MIME_MAP
        assert ".xyz" not in MIME_MAP

    def test_map_is_complete_common_types(self):
        """常见 web 类型齐全。"""
        from src.webui.server import MIME_MAP
        required = {".html", ".css", ".js", ".json", ".png", ".svg", ".ico"}
        assert required.issubset(set(MIME_MAP.keys()))


# ═══════════════════════════════════════════════════════════════
# handle_static
# ═══════════════════════════════════════════════════════════════

class TestHandleStatic:
    """handle_static — 静态文件服务与安全校验。"""

    @pytest.fixture
    def static_dir(self):
        """创建临时静态目录和测试文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 index.html
            index_path = Path(tmpdir) / "index.html"
            index_path.write_text("<html>Hello</html>", encoding="utf-8")
            # 创建 style.css
            css_path = Path(tmpdir) / "style.css"
            css_path.write_text("body {}", encoding="utf-8")
            # 创建 app.js
            js_path = Path(tmpdir) / "app.js"
            js_path.write_text("console.log()", encoding="utf-8")
            # 创建子目录文件
            sub_dir = Path(tmpdir) / "sub"
            sub_dir.mkdir()
            (sub_dir / "deep.js").write_text("// deep", encoding="utf-8")
            yield tmpdir

    @pytest.fixture
    def mock_request(self):
        """创建一个基础的 mock request。"""
        req = MagicMock(spec=web.Request)
        req.match_info = {"filename": "index.html"}
        return req

    async def _call_handle_static(self, filename, static_dir):
        """辅助方法：用指定 filename 和静态目录调用 handle_static。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": filename}
        with patch.object(server, "STATIC_DIR", static_dir):
            resp = await server.handle_static(request)
            return resp

    # ── 正常文件服务 ───────────────────────────────────

    async def test_returns_index_html(self, static_dir):
        """返回 index.html 文件内容。"""
        resp = await self._call_handle_static("index.html", static_dir)
        assert resp.status == 200
        body = resp.body
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        assert "<html>Hello</html>" in body

    async def test_returns_correct_mime_type_html(self, static_dir):
        """返回正确的 MIME type (text/html)。"""
        resp = await self._call_handle_static("index.html", static_dir)
        assert resp.content_type == "text/html"

    async def test_returns_correct_mime_type_css(self, static_dir):
        """CSS 文件返回 text/css。"""
        resp = await self._call_handle_static("style.css", static_dir)
        assert resp.content_type == "text/css"

    async def test_returns_correct_mime_type_js(self, static_dir):
        """JS 文件返回 application/javascript。"""
        resp = await self._call_handle_static("app.js", static_dir)
        assert resp.content_type == "application/javascript"

    async def test_unknown_extension_octet_stream(self, static_dir):
        """未知扩展名返回 application/octet-stream。"""
        # 创建一个 .xyz 文件
        (Path(static_dir) / "test.xyz").write_text("binary", encoding="utf-8")
        resp = await self._call_handle_static("test.xyz", static_dir)
        assert resp.content_type == "application/octet-stream"

    # ── 缓存策略 ───────────────────────────────────────

    async def test_cache_control_html_no_cache(self, static_dir):
        """HTML 文件 Cache-Control: max-age=0。"""
        resp = await self._call_handle_static("index.html", static_dir)
        assert resp.headers["Cache-Control"] == "public, max-age=0"

    async def test_cache_control_js_no_cache(self, static_dir):
        """JS 文件 Cache-Control: max-age=0。"""
        resp = await self._call_handle_static("app.js", static_dir)
        assert resp.headers["Cache-Control"] == "public, max-age=0"

    async def test_cache_control_css_cached(self, static_dir):
        """CSS 文件 Cache-Control: max-age=0（开发迭代频繁，不缓存）。"""
        resp = await self._call_handle_static("style.css", static_dir)
        assert resp.headers["Cache-Control"] == "public, max-age=0"

    async def test_cache_control_png_cached(self, static_dir):
        """PNG 文件 Cache-Control: max-age=3600。"""
        (Path(static_dir) / "icon.png").write_text("PNG", encoding="utf-8")
        resp = await self._call_handle_static("icon.png", static_dir)
        assert resp.headers["Cache-Control"] == "public, max-age=3600"

    # ── 路径穿越攻击 ───────────────────────────────────

    async def test_path_traversal_rejected(self, static_dir):
        """路径穿越攻击（../../etc/passwd）被拒绝 → HTTPForbidden。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": "../../etc/passwd"}

        with patch.object(server, "STATIC_DIR", static_dir):
            with pytest.raises(web.HTTPForbidden):
                await server.handle_static(request)

    async def test_path_traversal_with_encoded_slashes(self, static_dir):
        """URL 编码的路径穿越变体 — %2F 不被 pathlib 解析为路径分隔符，
        而是视为普通文件名，文件不存在时返回 HTTPNotFound。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": "..%2F..%2Fetc%2Fpasswd"}

        with patch.object(server, "STATIC_DIR", static_dir):
            with pytest.raises(web.HTTPNotFound):
                await server.handle_static(request)

    async def test_absolute_path_rejected(self, static_dir):
        """绝对路径 /etc/passwd 被拒绝。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": "/etc/passwd"}

        with patch.object(server, "STATIC_DIR", static_dir):
            with pytest.raises(web.HTTPForbidden):
                await server.handle_static(request)

    async def test_subdir_with_dotdot_rejected(self, static_dir):
        """子目录加 .. 越界被拒绝。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": "sub/../../etc/passwd"}

        with patch.object(server, "STATIC_DIR", static_dir):
            with pytest.raises(web.HTTPForbidden):
                await server.handle_static(request)

    # ── 不存在的文件 ───────────────────────────────────

    async def test_nonexistent_file_returns_not_found(self, static_dir):
        """不存在的文件 → HTTPNotFound。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": "nonexistent.js"}

        with patch.object(server, "STATIC_DIR", static_dir):
            with pytest.raises(web.HTTPNotFound):
                await server.handle_static(request)

    # ── 子目录文件 ─────────────────────────────────────

    async def test_subdirectory_file_served(self, static_dir):
        """子目录文件可通过 filename='sub/deep.js' 访问。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {"filename": "sub/deep.js"}

        with patch.object(server, "STATIC_DIR", static_dir):
            resp = await server.handle_static(request)
        assert resp.status == 200
        body = resp.body
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        assert "// deep" in body

    # ── charset ─────────────────────────────────────────

    async def test_charset_for_html(self, static_dir):
        """HTML 文件响应 charset=utf-8。"""
        resp = await self._call_handle_static("index.html", static_dir)
        assert resp.charset == "utf-8"

    async def test_no_charset_for_binary(self, static_dir):
        """二进制文件无 charset。"""
        (Path(static_dir) / "icon.png").write_text("PNG", encoding="utf-8")
        resp = await self._call_handle_static("icon.png", static_dir)
        assert resp.charset is None

    # ── 默认 filename ──────────────────────────────────

    async def test_default_filename_when_missing(self, static_dir):
        """match_info 中缺少 filename 时默认使用 index.html。"""
        from src.webui import server
        request = MagicMock(spec=web.Request)
        request.match_info = {}

        with patch.object(server, "STATIC_DIR", static_dir):
            resp = await server.handle_static(request)
        assert resp.status == 200
        body = resp.body
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        assert "<html>Hello</html>" in body


# ═══════════════════════════════════════════════════════════════
# STATIC_DIR / INDEX_HTML 路径存在
# ═══════════════════════════════════════════════════════════════

class TestStaticPaths:
    """STATIC_DIR 和 INDEX_HTML 路径存在。"""

    def test_static_dir_exists(self):
        from src.webui.server import STATIC_DIR
        assert os.path.isdir(STATIC_DIR), f"STATIC_DIR 不存在: {STATIC_DIR}"

    def test_index_html_exists(self):
        from src.webui.server import STATIC_DIR
        index_path = os.path.join(STATIC_DIR, "index.html")
        assert os.path.isfile(index_path), f"index.html 不存在: {index_path}"

    def test_static_dir_contains_expected_files(self):
        """STATIC_DIR 包含必要的静态文件。"""
        from src.webui.server import STATIC_DIR
        expected = {"index.html", "style.css", "app.js"}
        actual = set(os.listdir(STATIC_DIR))
        assert expected.intersection(actual), f"STATIC_DIR 缺少必要文件，现有: {actual}"
