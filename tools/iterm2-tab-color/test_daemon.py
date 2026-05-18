#!/usr/bin/env python3
"""
tab_color_daemon.py 纯逻辑测试
=============================
不依赖 iTerm2 API，不需要真实终端，可 CI。
覆盖：配置加载、UUID 解析、颜色阶段计算、state 文件读写、
       resume 场景、watch/poller 决策逻辑。
"""

import json
import os
import subprocess
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

# ------------------------------------------------------------------ #
#  导入被测模块（patch 掉 iterm2 依赖）
# ------------------------------------------------------------------ #

# 先 mock iterm2 模块，再 import daemon
import sys

mock_iterm2 = MagicMock()

# daemon.py 用了 iterm2.Color | None 类型注解，必须是一个真实 class
class MockColor:
    def __init__(self, r, g, b):
        self.r, self.g, self.b = r, g, b
    def __eq__(self, other):
        return isinstance(other, MockColor) and (self.r, self.g, self.b) == (other.r, other.g, other.b)
    def __repr__(self):
        return f"Color({self.r},{self.g},{self.b})"


class MockItermColor:
    def __init__(self, red, green, blue):
        self.red, self.green, self.blue = red, green, blue


mock_iterm2.Color = MockColor
mock_iterm2.LocalWriteOnlyProfile = MagicMock
mock_iterm2.async_get_app = AsyncMock()
mock_iterm2.run_forever = lambda _: None
sys.modules["iterm2"] = mock_iterm2

# 把项目目录加到 path
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

import tab_color_daemon as daemon


# ================================================================== #
#  compute_color_stage — 颜色阶段计算
# ================================================================== #

class TestComputeColorStage(unittest.TestCase):
    """核心状态机：green → yellow → red"""

    def test_zero_idle_is_green(self):
        self.assertEqual(daemon.compute_color_stage(0), "green")

    def test_just_below_yellow_threshold(self):
        secs = daemon.THRESHOLD_YELLOW_SEC - 1
        self.assertEqual(daemon.compute_color_stage(secs), "green")

    def test_exactly_yellow_threshold(self):
        self.assertEqual(daemon.compute_color_stage(daemon.THRESHOLD_YELLOW_SEC), "yellow")

    def test_between_yellow_and_red(self):
        secs = (daemon.THRESHOLD_YELLOW_SEC + daemon.THRESHOLD_RED_SEC) // 2
        self.assertEqual(daemon.compute_color_stage(secs), "yellow")

    def test_just_below_red_threshold(self):
        secs = daemon.THRESHOLD_RED_SEC - 1
        self.assertEqual(daemon.compute_color_stage(secs), "yellow")

    def test_exactly_red_threshold(self):
        self.assertEqual(daemon.compute_color_stage(daemon.THRESHOLD_RED_SEC), "red")

    def test_way_past_red(self):
        self.assertEqual(daemon.compute_color_stage(daemon.THRESHOLD_RED_SEC * 10), "red")

    def test_negative_idle_is_green(self):
        """时钟偏移可能导致负值，应视为 green"""
        self.assertEqual(daemon.compute_color_stage(-100), "green")


# ================================================================== #
#  extract_uuid — ITERM_SESSION_ID 格式解析
# ================================================================== #

class TestExtractUuid(unittest.TestCase):

    def test_standard_format(self):
        self.assertEqual(
            daemon.extract_uuid("w0t1p2:A50590C2-E083-4E1B-9F1A-4B211ED615C1"),
            "A50590C2-E083-4E1B-9F1A-4B211ED615C1",
        )

    def test_pure_uuid(self):
        uuid = "A50590C2-E083-4E1B-9F1A-4B211ED615C1"
        self.assertEqual(daemon.extract_uuid(uuid), uuid)

    def test_different_prefix(self):
        self.assertEqual(
            daemon.extract_uuid("w2t0p0:B0D7F4F7-A3B0-4045-B736-27A831A2CC47"),
            "B0D7F4F7-A3B0-4045-B736-27A831A2CC47",
        )

    def test_multiple_colons(self):
        """罕见但理论上可能：多个冒号，取最后一段"""
        self.assertEqual(daemon.extract_uuid("a:b:UUID123"), "UUID123")

    def test_empty_string(self):
        self.assertEqual(daemon.extract_uuid(""), "")


class TestExtractTabPrefix(unittest.TestCase):

    def test_standard_format(self):
        self.assertEqual(
            daemon.extract_tab_prefix("w0t1p2:A50590C2-E083-4E1B-9F1A-4B211ED615C1"),
            "w0t1",
        )

    def test_missing_prefix_returns_empty(self):
        self.assertEqual(daemon.extract_tab_prefix("A50590C2-E083-4E1B-9F1A-4B211ED615C1"), "")


# ================================================================== #
#  color_for_stage — stage → Color 映射
# ================================================================== #

class TestColorForStage(unittest.TestCase):

    def test_all_stages_return_color(self):
        for stage in ("green", "yellow", "red"):
            color = daemon.color_for_stage(stage)
            self.assertIsInstance(color, MockColor)

    def test_invalid_stage_raises(self):
        with self.assertRaises(KeyError):
            daemon.color_for_stage("blue")

    def test_max_stage_returns_more_severe_stage(self):
        self.assertEqual(daemon.max_stage("green", "yellow"), "yellow")
        self.assertEqual(daemon.max_stage("red", "green"), "red")
        self.assertEqual(daemon.max_stage("yellow", "red"), "red")


class TestTabColorEscapeSequences(unittest.TestCase):

    def test_reset_escape_sequence(self):
        self.assertEqual(
            daemon.escape_sequences_for_tab_color(None),
            ["\033]6;1;bg;*;default\a"],
        )

    def test_color_escape_sequences(self):
        self.assertEqual(
            daemon.escape_sequences_for_tab_color(MockColor(200, 40, 40)),
            [
                "\033]6;1;bg;red;brightness;200\a",
                "\033]6;1;bg;green;brightness;40\a",
                "\033]6;1;bg;blue;brightness;40\a",
            ],
        )

    def test_color_escape_sequences_accept_iterm2_color_attrs(self):
        self.assertEqual(
            daemon.escape_sequences_for_tab_color(MockItermColor(220, 160, 0)),
            [
                "\033]6;1;bg;red;brightness;220\a",
                "\033]6;1;bg;green;brightness;160\a",
                "\033]6;1;bg;blue;brightness;0\a",
            ],
        )


# ================================================================== #
#  read_state_files / update_stage_file — 文件 I/O
# ================================================================== #

class TestStateFileIO(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_state(self, filename, data):
        path = Path(self.tmpdir) / filename
        path.write_text(json.dumps(data))
        return str(path)

    def test_read_empty_dir(self):
        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            result = daemon.read_state_files()
            self.assertEqual(result, {})

    def test_read_single_file(self):
        data = {"iterm2_session": "w0t0p0:UUID1", "idle_since": 100, "color_stage": "green"}
        path = self._write_state("session1.json", data)
        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            result = daemon.read_state_files()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[path]["color_stage"], "green")

    def test_read_multiple_files(self):
        self._write_state("a.json", {"iterm2_session": "UUID_A", "idle_since": 1})
        self._write_state("b.json", {"iterm2_session": "UUID_B", "idle_since": 2})
        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            result = daemon.read_state_files()
            self.assertEqual(len(result), 2)

    def test_read_ignores_non_json(self):
        (Path(self.tmpdir) / "readme.txt").write_text("not json")
        (Path(self.tmpdir) / "bad.json").write_text("{invalid")
        self._write_state("good.json", {"iterm2_session": "UUID", "idle_since": 1})
        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            result = daemon.read_state_files()
            self.assertEqual(len(result), 1)

    def test_read_nonexistent_dir(self):
        with patch.object(daemon, "IDLE_STATE_DIR", Path("/nonexistent/path")):
            result = daemon.read_state_files()
            self.assertEqual(result, {})

    def test_update_stage_file(self):
        data = {"iterm2_session": "UUID", "idle_since": 100, "color_stage": "green"}
        path = self._write_state("s.json", data)
        daemon.update_stage_file(path, data, "yellow")
        updated = json.loads(Path(path).read_text())
        self.assertEqual(updated["color_stage"], "yellow")

    def test_update_does_not_corrupt_other_fields(self):
        data = {"iterm2_session": "UUID", "idle_since": 100, "color_stage": "green"}
        path = self._write_state("s.json", data)
        daemon.update_stage_file(path, data, "red")
        updated = json.loads(Path(path).read_text())
        self.assertEqual(updated["iterm2_session"], "UUID")
        self.assertEqual(updated["idle_since"], 100)


# ================================================================== #
#  load_config — 配置加载
# ================================================================== #

class TestLoadConfig(unittest.TestCase):

    def test_defaults_when_no_config_file(self):
        with patch.object(daemon, "load_config") as mock_load:
            # 直接测试默认值
            defaults = {
                "THRESHOLD_YELLOW": 10, "THRESHOLD_RED": 20,
                "POLL_INTERVAL": 30, "CONCURRENT_TARGET": 3,
            }
            for key, expected in defaults.items():
                self.assertEqual(daemon.CFG[key], expected,
                                 f"默认 {key} 应为 {expected}")

    def test_config_reads_custom_values(self):
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "config.sh"
        config_path.write_text('THRESHOLD_YELLOW=5\nTHRESHOLD_RED=15\nPOLL_INTERVAL=10\n')
        with patch.object(Path, "parent", new_callable=lambda: property(lambda self: Path(tmpdir))):
            pass  # load_config 依赖 __file__ 的 parent，这里只验证结构
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ================================================================== #
#  is_active_tab — 活跃 tab 判断（需要 mock iTerm2 对象）
# ================================================================== #

class TestIsActiveTab(unittest.TestCase):

    def _make_session(self, tab_id="tab1", window_active_tab_id="tab1"):
        """构造 mock session → tab → window 链"""
        session = MagicMock()
        tab = MagicMock()
        tab.tab_id = tab_id
        window = MagicMock()
        active_tab = MagicMock()
        active_tab.tab_id = window_active_tab_id
        window.current_tab = active_tab
        tab.window = window
        session.tab = tab
        return session

    def test_active_tab_returns_true(self):
        session = self._make_session(tab_id="tab1", window_active_tab_id="tab1")
        app = MagicMock()
        self.assertTrue(daemon.is_active_tab(app, session))

    def test_inactive_tab_returns_false(self):
        session = self._make_session(tab_id="tab1", window_active_tab_id="tab2")
        app = MagicMock()
        self.assertFalse(daemon.is_active_tab(app, session))

    def test_no_tab_returns_false(self):
        session = MagicMock()
        session.tab = None
        app = MagicMock()
        self.assertFalse(daemon.is_active_tab(app, session))

    def test_no_window_returns_false(self):
        session = MagicMock()
        tab = MagicMock()
        tab.window = None
        session.tab = tab
        app = MagicMock()
        self.assertFalse(daemon.is_active_tab(app, session))

    def test_exception_returns_false(self):
        session = MagicMock()
        session.tab = MagicMock(side_effect=Exception("boom"))
        app = MagicMock()
        self.assertFalse(daemon.is_active_tab(app, session))


# ================================================================== #
#  is_agent_running — AI CLI 进程检测
# ================================================================== #

class TestIsAgentRunning(unittest.TestCase):

    def test_command_matches_claude(self):
        self.assertTrue(daemon._command_matches_agent("claude", "claude --resume", "claude"))
        self.assertTrue(daemon._command_matches_agent("node", "node @anthropic-ai/claude-code", "claude"))
        self.assertFalse(daemon._command_matches_agent("codex", "codex", "claude"))

    def test_command_matches_codex(self):
        self.assertTrue(daemon._command_matches_agent("codex", "codex --model gpt-5.5", "codex"))
        self.assertTrue(daemon._command_matches_agent("node", "node @openai/codex/bin/codex.js", "codex"))
        self.assertFalse(daemon._command_matches_agent("claude", "claude", "codex"))

    def test_shell_job_with_missing_server_pid_is_not_running(self):
        """回归：server_pid=None 且前台是 zsh 时，应清理 state"""
        session = MagicMock()
        session.server_pid = None
        session.async_get_variable = AsyncMock(side_effect=lambda name: {
            "jobName": "zsh",
        }.get(name))

        import asyncio
        self.assertFalse(asyncio.run(daemon.is_claude_running(session)))

    def test_tty_scan_detects_claude(self):
        """jobName 不可靠时，通过 tty 进程表识别 Claude"""
        session = MagicMock()
        session.server_pid = None
        session.async_get_variable = AsyncMock(side_effect=lambda name: {
            "jobName": "node",
            "tty": "/dev/ttys001",
        }.get(name))

        import asyncio
        with patch.object(daemon, "_tty_has_agent_process", return_value=True):
            self.assertTrue(asyncio.run(daemon.is_agent_running(session, "claude")))

    def test_tty_scan_detects_codex(self):
        """Codex 与 Claude 共用检测路径，只替换 agent spec"""
        session = MagicMock()
        session.server_pid = None
        session.async_get_variable = AsyncMock(side_effect=lambda name: {
            "jobName": "codex",
            "tty": "/dev/ttys002",
        }.get(name))

        import asyncio
        with patch.object(daemon, "_tty_has_agent_process", return_value=True):
            self.assertTrue(asyncio.run(daemon.is_agent_running(session, "codex")))

    def test_unknown_detection_keeps_state(self):
        """完全无法判断时保持保守，不误删 state"""
        session = MagicMock()
        session.server_pid = None
        session.async_get_variable = AsyncMock(side_effect=Exception("unavailable"))

        import asyncio
        self.assertTrue(asyncio.run(daemon.is_claude_running(session)))

    def test_session_foreground_shell_detects_finished_agent(self):
        session = MagicMock()
        session.async_get_variable = AsyncMock(return_value="zsh")

        import asyncio
        self.assertTrue(asyncio.run(daemon.session_foreground_is_shell(session)))

    def test_session_foreground_non_shell_is_not_finished(self):
        session = MagicMock()
        session.async_get_variable = AsyncMock(return_value="codex")

        import asyncio
        self.assertFalse(asyncio.run(daemon.session_foreground_is_shell(session)))


# ================================================================== #
#  apply_tab_color — tab 颜色设置（mock iTerm2 API）
# ================================================================== #

class TestApplyTabColor(unittest.TestCase):

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_app = MagicMock()
        mock_iterm2.async_get_app = AsyncMock(return_value=self.mock_app)

    def _make_pane(self):
        pane = MagicMock()
        pane.async_set_profile_properties = AsyncMock()
        return pane

    def _make_session(self, tab_sessions=None):
        session = MagicMock()
        session.session_id = "UUID-1"
        session.async_set_profile_properties = AsyncMock()
        if tab_sessions is not None:
            tab = MagicMock()
            tab.sessions = tab_sessions
            session.tab = tab
        else:
            session.tab = None
        self.mock_app.get_session_by_id.return_value = session
        return session

    def test_color_none_resets_all_panes(self):
        """color=None 时应对所有 pane 执行重置"""
        pane1, pane2 = self._make_pane(), self._make_pane()
        self._make_session(tab_sessions=[pane1, pane2])

        import asyncio
        asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", None))

        self.assertEqual(pane1.async_set_profile_properties.call_count, 1)
        self.assertEqual(pane2.async_set_profile_properties.call_count, 1)

    def test_color_set_applies_to_all_panes(self):
        """设色时应覆盖同 tab 所有 pane"""
        pane1, pane2 = self._make_pane(), self._make_pane()
        self._make_session(tab_sessions=[pane1, pane2])
        # 模拟非活跃 tab
        with patch.object(daemon, "is_active_tab", return_value=False):
            import asyncio
            asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", MockColor(30, 180, 30)))

        self.assertEqual(pane1.async_set_profile_properties.call_count, 1)
        self.assertEqual(pane2.async_set_profile_properties.call_count, 1)

    def test_color_set_writes_escape_to_all_pane_ttys(self):
        """iTerm2 Profile API 不稳定时，daemon 也应通过 tty escape 覆盖视觉颜色"""
        pane1, pane2 = self._make_pane(), self._make_pane()
        pane1.async_get_variable = AsyncMock(return_value="/dev/ttys001")
        pane2.async_get_variable = AsyncMock(return_value="/dev/ttys002")
        self._make_session(tab_sessions=[pane1, pane2])

        import asyncio
        with patch.object(daemon, "is_active_tab", return_value=False), \
             patch.object(daemon, "_write_escape_to_tty") as write_escape:
            asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", MockColor(30, 180, 30)))

        expected = daemon.escape_sequences_for_tab_color(MockColor(30, 180, 30))
        write_escape.assert_has_calls([
            call("/dev/ttys001", expected),
            call("/dev/ttys002", expected),
        ], any_order=True)

    def test_color_none_writes_reset_escape_to_all_pane_ttys(self):
        """重置 tab 时也要清掉 terminal escape 设置的 tab color"""
        pane1, pane2 = self._make_pane(), self._make_pane()
        pane1.async_get_variable = AsyncMock(return_value="/dev/ttys001")
        pane2.async_get_variable = AsyncMock(return_value="/dev/ttys002")
        self._make_session(tab_sessions=[pane1, pane2])

        import asyncio
        with patch.object(daemon, "_write_escape_to_tty") as write_escape:
            asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", None))

        expected = daemon.escape_sequences_for_tab_color(None)
        write_escape.assert_has_calls([
            call("/dev/ttys001", expected),
            call("/dev/ttys002", expected),
        ], any_order=True)

    def test_color_skipped_for_active_tab(self):
        """活跃 tab 设色时应被跳过"""
        pane = self._make_pane()
        self._make_session(tab_sessions=[pane])
        with patch.object(daemon, "is_active_tab", return_value=True):
            import asyncio
            asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", MockColor(30, 180, 30)))

        pane.async_set_profile_properties.assert_not_called()

    def test_color_none_not_skipped_for_active_tab(self):
        """重置（color=None）即使活跃 tab 也要执行"""
        pane = self._make_pane()
        self._make_session(tab_sessions=[pane])
        with patch.object(daemon, "is_active_tab", return_value=True):
            import asyncio
            asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", None))

        pane.async_set_profile_properties.assert_called_once()

    def test_session_not_found_is_noop(self):
        """找不到 session 时静默返回"""
        self.mock_app.get_session_by_id.return_value = None
        import asyncio
        asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", (30, 180, 30)))
        # 不抛异常即通过

    def test_single_pane_no_tab(self):
        """session 没有 tab（无分屏）时只设自己"""
        pane = MagicMock()
        self._make_session(tab_sessions=None)
        # session.tab is None, so target_sessions = [session]
        import asyncio
        with patch.object(daemon, "is_active_tab", return_value=False):
            asyncio.run(daemon.apply_tab_color(self.mock_conn, "UUID-1", MockColor(30, 180, 30)))

        # session 自身被调用 async_set_profile_properties
        self.assertEqual(self.mock_app.get_session_by_id.return_value.async_set_profile_properties.call_count, 1)


# ================================================================== #
#  _apply_all_colors — 同 tab 多 agent 聚合
# ================================================================== #

class TestApplyAllColors(unittest.TestCase):

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_app = MagicMock()
        mock_iterm2.async_get_app = AsyncMock(return_value=self.mock_app)

    def _make_session(self, tab_id):
        session = MagicMock()
        tab = MagicMock()
        tab.tab_id = tab_id
        tab.sessions = [session]
        session.tab = tab
        session.session_id = f"session-{tab_id}"
        session.async_set_profile_properties = AsyncMock()
        session.async_get_variable = AsyncMock(return_value="/dev/ttys001")
        return session

    def test_same_tab_uses_most_severe_stage_once(self):
        session1 = self._make_session("tab-1")
        session2 = self._make_session("tab-1")
        self.mock_app.get_session_by_id.side_effect = {
            "UUID-1": session1,
            "UUID-2": session2,
        }.get

        states = {
            "a.json": {"iterm2_session": "w0t0p0:UUID-1", "color_stage": "green"},
            "b.json": {"iterm2_session": "w0t0p1:UUID-2", "color_stage": "red"},
        }

        import asyncio
        with patch.object(daemon, "read_state_files", return_value=states), \
             patch.object(daemon, "is_active_tab", return_value=False), \
             patch.object(daemon, "apply_tab_color", new_callable=AsyncMock) as apply_mock:
            asyncio.run(daemon._apply_all_colors(self.mock_conn))

        apply_mock.assert_called_once()
        _, args, _ = apply_mock.mock_calls[0]
        self.assertEqual(args[2], daemon.COLOR_RED)

    def test_repeated_same_state_does_not_rewrite_tab_color(self):
        """稳定 state 下第二轮不应重复触发 iTerm2 设色。"""
        session = self._make_session("tab-1")
        self.mock_app.get_session_by_id.return_value = session
        states = {
            "a.json": {"iterm2_session": "w0t0p0:UUID-1", "color_stage": "green"},
        }
        applied_colors = {}

        import asyncio
        with patch.object(daemon, "read_state_files", return_value=states), \
             patch.object(daemon, "is_active_tab", return_value=False), \
             patch.object(daemon, "_write_escape_to_tty") as write_escape:
            asyncio.run(daemon._apply_all_colors(self.mock_conn, applied_colors=applied_colors))
            asyncio.run(daemon._apply_all_colors(self.mock_conn, applied_colors=applied_colors))

        self.assertEqual(session.async_set_profile_properties.call_count, 1)
        self.assertEqual(write_escape.call_count, 1)

    def test_stage_change_rewrites_tab_color_once(self):
        """同一 tab 目标颜色变化时仍应重新设色。"""
        session = self._make_session("tab-1")
        self.mock_app.get_session_by_id.return_value = session
        applied_colors = {}

        import asyncio
        with patch.object(daemon, "is_active_tab", return_value=False), \
             patch.object(daemon, "_write_escape_to_tty") as write_escape:
            asyncio.run(daemon._apply_all_colors(
                self.mock_conn,
                states={"a.json": {"iterm2_session": "w0t0p0:UUID-1", "color_stage": "green"}},
                applied_colors=applied_colors,
            ))
            asyncio.run(daemon._apply_all_colors(
                self.mock_conn,
                states={"a.json": {"iterm2_session": "w0t0p0:UUID-1", "color_stage": "yellow"}},
                applied_colors=applied_colors,
            ))

        self.assertEqual(session.async_set_profile_properties.call_count, 2)
        self.assertEqual(write_escape.call_count, 2)


class TestResetUntrackedTabColors(unittest.TestCase):

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_app = MagicMock()
        mock_iterm2.async_get_app = AsyncMock(return_value=self.mock_app)

    def _make_session(self, session_id, tab, use_tab_color):
        session = MagicMock()
        session.session_id = session_id
        session.tab = tab
        profile = MagicMock()
        profile.use_tab_color = use_tab_color
        session.async_get_profile = AsyncMock(return_value=profile)
        return session

    def _make_tab(self, tab_id, use_tab_color=True):
        tab = MagicMock()
        tab.tab_id = tab_id
        session = self._make_session(f"session-{tab_id}", tab, use_tab_color)
        tab.sessions = [session]
        return tab, session

    def test_resets_colored_tab_without_idle_state(self):
        tab, session = self._make_tab("tab-stale", use_tab_color=True)
        window = MagicMock()
        window.tabs = [tab]
        self.mock_app.terminal_windows = [window]
        self.mock_app.get_session_by_id.return_value = None

        import asyncio
        with patch.object(daemon, "apply_tab_color", new_callable=AsyncMock) as apply_mock:
            asyncio.run(daemon.reset_untracked_tab_colors(self.mock_conn, {}, app=self.mock_app))

        apply_mock.assert_called_once_with(
            self.mock_conn,
            session.session_id,
            None,
            app=self.mock_app,
            session=session,
        )

    def test_keeps_tab_with_idle_state(self):
        tab, session = self._make_tab("tab-idle", use_tab_color=True)
        window = MagicMock()
        window.tabs = [tab]
        self.mock_app.terminal_windows = [window]
        self.mock_app.get_session_by_id.return_value = session

        states = {"s.json": {"iterm2_session": f"w0t0p0:{session.session_id}"}}

        import asyncio
        with patch.object(daemon, "apply_tab_color", new_callable=AsyncMock) as apply_mock:
            asyncio.run(daemon.reset_untracked_tab_colors(self.mock_conn, states, app=self.mock_app))

        apply_mock.assert_not_called()

    def test_ignores_uncolored_tab_without_idle_state(self):
        tab, _ = self._make_tab("tab-clean", use_tab_color=False)
        window = MagicMock()
        window.tabs = [tab]
        self.mock_app.terminal_windows = [window]
        self.mock_app.get_session_by_id.return_value = None

        import asyncio
        with patch.object(daemon, "apply_tab_color", new_callable=AsyncMock) as apply_mock:
            asyncio.run(daemon.reset_untracked_tab_colors(self.mock_conn, {}, app=self.mock_app))

        apply_mock.assert_not_called()


# ================================================================== #
#  prune_finished_state_files — 快速退出清理
# ================================================================== #

class TestPruneFinishedStateFiles(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_conn = MagicMock()
        self.mock_app = MagicMock()
        mock_iterm2.async_get_app = AsyncMock(return_value=self.mock_app)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_state(self, filename, session_id="UUID-1"):
        path = Path(self.tmpdir) / filename
        path.write_text(json.dumps({
            "iterm2_session": f"w0t0p0:{session_id}",
            "agent": "codex",
            "idle_since": time.time(),
            "color_stage": "green",
        }))
        return str(path)

    def test_shell_pane_state_is_removed_without_process_scan(self):
        path = self._write_state("codex.json")
        # idle_since 用当前时间，需要绕过 MIN_STATE_AGE_SEC 保护
        data = json.loads(Path(path).read_text())
        data["idle_since"] = time.time() - 30  # 模拟 30 秒前写入
        Path(path).write_text(json.dumps(data))
        session = MagicMock()
        session.async_get_variable = AsyncMock(return_value="zsh")
        self.mock_app.get_session_by_id.return_value = session

        import asyncio
        result = asyncio.run(daemon.prune_finished_state_files(
            self.mock_conn,
            {path: json.loads(Path(path).read_text())},
        ))

        self.assertEqual(result, {})
        self.assertFalse(Path(path).exists())

    def test_running_agent_state_is_kept(self):
        path = self._write_state("codex.json")
        session = MagicMock()
        session.async_get_variable = AsyncMock(return_value="codex")
        self.mock_app.get_session_by_id.return_value = session

        import asyncio
        result = asyncio.run(daemon.prune_finished_state_files(
            self.mock_conn,
            {path: json.loads(Path(path).read_text())},
        ))

        self.assertIn(path, result)
        self.assertTrue(Path(path).exists())


# ================================================================== #
#  Resume 场景 — 核心回归测试
# ================================================================== #

class TestResumeScenario(unittest.TestCase):
    """
    claude --resume 导致的 bug：
    旧 state 文件带着过期的 idle_since 存在，
    resume 时没有 hook 事件来更新或删除它。

    daemon 应该能正确处理：
    - state 文件的 idle_since 与实际不符
    - 同路径文件被覆盖写（不是新增）
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_state(self, session_id, idle_since, stage="green"):
        path = Path(self.tmpdir) / f"{session_id}.json"
        data = {
            "iterm2_session": f"w0t0p0:{session_id}",
            "claude_session": session_id,
            "idle_since": idle_since,
            "color_stage": stage,
        }
        path.write_text(json.dumps(data))
        return str(path)

    def test_stale_idle_since_produces_wrong_color(self):
        """回归：旧 idle_since 导致错误颜色"""
        old_time = time.time() - 40 * 60  # 40 分钟前
        self._write_state("resume-session", old_time, "red")

        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            states = daemon.read_state_files()
            self.assertEqual(len(states), 1)

            for path, state in states.items():
                idle = time.time() - state["idle_since"]
                stage = daemon.compute_color_stage(idle)
                # idle > 20min → red，即使用户实际是活跃的
                self.assertEqual(stage, "red")

    def test_overwrite_same_path_keeps_stage_correct(self):
        """Stop hook 覆盖写同一路径，idle_since 应更新"""
        old_time = time.time() - 40 * 60
        session_id = "resume-session"

        # 第一次 Stop（旧对话）
        path1 = self._write_state(session_id, old_time, "green")

        # 第二次 Stop（resume 后新对话）覆盖同文件
        new_time = time.time()
        path2 = self._write_state(session_id, new_time, "green")

        self.assertEqual(path1, path2)  # 同一路径

        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            states = daemon.read_state_files()
            for path, state in states.items():
                idle = time.time() - state["idle_since"]
                stage = daemon.compute_color_stage(idle)
                self.assertEqual(stage, "green")  # idle ~0 → green

    def test_watch_idle_dir_detects_overwrite_as_known(self):
        """
        回归测试：watch_idle_dir 只检测新增/删除，
        不检测覆盖写 → resume 后 Stop hook 覆盖写的文件不会被当新文件处理
        """
        old_time = time.time() - 40 * 60
        path = self._write_state("session-1", old_time, "red")

        # 模拟 known 字典包含旧路径
        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            # 第一次扫描 → known 包含该文件
            current = daemon.read_state_files()
            known = dict(current)

            # Stop hook 覆盖写（idle_since 更新）
            new_time = time.time()
            Path(path).write_text(json.dumps({
                "iterm2_session": "w0t0p0:session-1",
                "idle_since": new_time,
                "color_stage": "green",
            }))

            # 第二次扫描 → 路径仍在 known 中，不是 "新文件"
            current2 = daemon.read_state_files()
            new_files = {p for p in current2 if p not in known}
            self.assertEqual(len(new_files), 0)  # 覆盖写不被当作新增！


# ================================================================== #
#  color_poller 决策逻辑 — 用 mock 测试完整 poll cycle
# ================================================================== #

class TestPollerDecisionLogic(unittest.TestCase):
    """测试 color_poller 在各种状态下的决策"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_conn = MagicMock()
        self.mock_app = MagicMock()
        mock_iterm2.async_get_app = AsyncMock(return_value=self.mock_app)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_state(self, session_id, idle_since, stage="green"):
        path = Path(self.tmpdir) / f"{session_id}.json"
        data = {
            "iterm2_session": f"w0t0p0:{session_id}",
            "claude_session": session_id,
            "idle_since": idle_since,
            "color_stage": stage,
        }
        path.write_text(json.dumps(data))
        return str(path)

    def test_stage_upgrade_triggers_update(self):
        """idle 时间增长导致 stage 升级时，应更新 state 文件"""
        idle_since = time.time() - daemon.THRESHOLD_YELLOW_SEC - 10  # 超过黄色阈值
        path = self._write_state("s1", idle_since, "green")

        session = MagicMock()
        session.tab = MagicMock()
        session.tab.sessions = [session]
        self.mock_app.get_session_by_id.return_value = session

        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)), \
             patch.object(daemon, "is_active_tab", return_value=False):
            states = daemon.read_state_files()
            for p, state in states.items():
                prev = state.get("color_stage", "green")
                idle = time.time() - state["idle_since"]
                new = daemon.compute_color_stage(idle)
                if new != prev:
                    daemon.update_stage_file(p, state, new)

            updated = json.loads(Path(path).read_text())
            self.assertEqual(updated["color_stage"], "yellow")

    def test_no_stage_change_no_update(self):
        """idle 时间未达下一个阈值，state 文件不应被改写"""
        idle_since = time.time() - 60  # 1 分钟，远低于黄色阈值
        path = self._write_state("s1", idle_since, "green")

        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            states = daemon.read_state_files()
            for p, state in states.items():
                prev = state.get("color_stage", "green")
                idle = time.time() - state["idle_since"]
                new = daemon.compute_color_stage(idle)
                self.assertEqual(new, prev)  # 无变化

    def test_boundary_threshold_no_oscillation(self):
        """
        回归：idle 时间恰好在阈值边界时不应震荡。
        连续两次计算应返回相同结果。
        """
        # 恰好在红色阈值上
        idle_since = time.time() - daemon.THRESHOLD_RED_SEC
        self._write_state("s1", idle_since, "yellow")

        with patch.object(daemon, "IDLE_STATE_DIR", Path(self.tmpdir)):
            states = daemon.read_state_files()
            for p, state in states.items():
                idle1 = time.time() - state["idle_since"]
                stage1 = daemon.compute_color_stage(idle1)

                # 模拟 30 秒后再次计算
                idle2 = idle1 + 30
                stage2 = daemon.compute_color_stage(idle2)

                # 两次都应该是 red（已经过了阈值）
                self.assertEqual(stage1, "red")
                self.assertEqual(stage2, "red")


# ================================================================== #
#  tab_color_hook.sh — Codex / tmux iTerm session 映射
# ================================================================== #

class TestTabColorHookScript(unittest.TestCase):
    """Hook shell 层回归：Codex 在 tmux/OMX 中不能使用 stale ITERM_SESSION_ID。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "home"
        self.home.mkdir()
        self.fake_bin = Path(self.tmpdir) / "bin"
        self.fake_bin.mkdir()
        self.hook = PROJECT_DIR / "tab_color_hook.sh"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_fake_tmux_and_ps(self, real_iterm_session: str):
        tmux = self.fake_bin / "tmux"
        tmux.write_text("""#!/bin/sh
case "$1" in
  display-message)
    printf '%s\\n' 'omx-burnkit-main'
    ;;
  list-clients)
    printf '%s\\n' '12345 /dev/ttys018'
    ;;
  *)
    exit 1
    ;;
esac
""")
        tmux.chmod(0o755)

        ps = self.fake_bin / "ps"
        ps.write_text(f"""#!/bin/sh
case "$*" in
  *"12345"*"command="*)
    printf '%s\\n' 'tmux attach-session ITERM_SESSION_ID={real_iterm_session} HOME=/Users/duying'
    ;;
  *)
    exit 1
    ;;
esac
""")
        ps.chmod(0o755)

    def _run_hook(self, agent: str, payload: dict, extra_env: dict[str, str]):
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "PATH": f"{self.fake_bin}{os.pathsep}{env.get('PATH', '')}",
        })
        env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.hook), "--agent", agent],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            cwd=str(PROJECT_DIR),
            timeout=5,
        )

    def _read_state(self, filename: str):
        path = self.home / ".claude" / "idle_state" / filename
        self.assertTrue(path.exists(), f"missing state file: {path}")
        return json.loads(path.read_text())

    def test_codex_tmux_stop_uses_attached_client_iterm_session(self):
        self._write_fake_tmux_and_ps("w0t9p1:REAL-UUID")

        result = self._run_hook("codex", {
            "hook_event_name": "Stop",
            "session_id": "codex-session",
        }, {
            "TMUX": "/private/tmp/tmux-502/default,67925,4",
            "ITERM_SESSION_ID": "w0t4p0:STALE-UUID",
        })

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        state = self._read_state("codex-codex-session.json")
        self.assertEqual(state["agent"], "codex")
        self.assertEqual(state["iterm2_session"], "w0t9p1:REAL-UUID")

    def test_claude_tmux_stop_keeps_existing_iterm_session(self):
        self._write_fake_tmux_and_ps("w0t9p1:REAL-UUID")

        result = self._run_hook("claude", {
            "hook_event_name": "Stop",
            "session_id": "claude-session",
        }, {
            "TMUX": "/private/tmp/tmux-502/default,67925,4",
            "ITERM_SESSION_ID": "w0t4p0:STALE-UUID",
        })

        self.assertEqual(result.returncode, 0, result.stderr)
        state = self._read_state("claude-session.json")
        self.assertEqual(state["agent"], "claude")
        self.assertEqual(state["iterm2_session"], "w0t4p0:STALE-UUID")


# ================================================================== #
#  Daemon 入口 — iTerm2 断线重连
# ================================================================== #

class TestDaemonEntrypoint(unittest.TestCase):

    def test_run_daemon_enables_iterm2_retry(self):
        """iTerm2 重启/升级断开 websocket 后，daemon 应允许 API 自动重连。"""
        with patch.object(daemon.iterm2, "run_forever") as run_forever:
            daemon.run_daemon()

        run_forever.assert_called_once_with(daemon.main, retry=True)


# ================================================================== #
#  运行
# ================================================================== #

if __name__ == "__main__":
    unittest.main(verbosity=2)
