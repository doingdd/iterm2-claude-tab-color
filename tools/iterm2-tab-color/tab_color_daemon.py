#!/usr/bin/env python3
"""
iTerm2 Tab Color Daemon
======================
守护进程脚本，通过 launchd 在登录时自动启动。

架构：单一写入者
  - watch (500ms)：唯一负责写 tab 颜色的循环
    读 state 文件 → 轻量清理已回到 shell 的 pane → 应用颜色
  - poller (30s)：只更新 state 文件的元数据
    孤儿清理（session 不存在 或 agent 进程已退出）、同 tab 聚合、色阶升级
    绝不直接操作 iTerm2 API

配置：同目录 config.sh
"""

import asyncio
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

import iterm2

# ------------------------------------------------------------------ #
#  配置加载
# ------------------------------------------------------------------ #

def load_config() -> dict:
    defaults = {
        "THRESHOLD_YELLOW": 10,
        "THRESHOLD_RED": 20,
        "COLOR_GREEN_R": 30,    "COLOR_GREEN_G": 180, "COLOR_GREEN_B": 30,
        "COLOR_YELLOW_R": 220,  "COLOR_YELLOW_G": 160, "COLOR_YELLOW_B": 0,
        "COLOR_RED_R": 200,     "COLOR_RED_G": 40,    "COLOR_RED_B": 40,
        "POLL_INTERVAL": 30,
        "IDLE_STATE_DIR": str(Path.home() / ".claude" / "idle_state"),
        "CONCURRENT_TARGET": 3,
    }
    script_path = Path(os.path.realpath(__file__))
    config_path = script_path.parent / "config.sh"
    if not config_path.exists():
        return defaults
    cfg = dict(defaults)
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.split("#")[0].strip().strip('"').strip("'")
            val = val.replace("$HOME", str(Path.home()))
            if key in cfg:
                try:
                    cfg[key] = int(val)
                except ValueError:
                    cfg[key] = val
    return cfg


CFG = load_config()

THRESHOLD_YELLOW_SEC = CFG["THRESHOLD_YELLOW"] * 60
THRESHOLD_RED_SEC    = CFG["THRESHOLD_RED"] * 60
POLL_INTERVAL        = CFG["POLL_INTERVAL"]
IDLE_STATE_DIR       = Path(CFG["IDLE_STATE_DIR"])
CONCURRENT_TARGET    = CFG["CONCURRENT_TARGET"]
FAST_EXIT_CHECK_INTERVAL = 1.0
# state 文件最小存活时间：新写入的文件在此时长内不会被快速清理删除。
# 防止 agent 刚回复完（Stop hook 写 state）→ jobName 短暂变回 shell → 被误删。
MIN_STATE_AGE_SEC = 10

COLOR_GREEN  = iterm2.Color(CFG["COLOR_GREEN_R"],  CFG["COLOR_GREEN_G"],  CFG["COLOR_GREEN_B"])
COLOR_YELLOW = iterm2.Color(CFG["COLOR_YELLOW_R"], CFG["COLOR_YELLOW_G"], CFG["COLOR_YELLOW_B"])
COLOR_RED    = iterm2.Color(CFG["COLOR_RED_R"],    CFG["COLOR_RED_G"],    CFG["COLOR_RED_B"])
STAGE_RANK = {"green": 0, "yellow": 1, "red": 2}

AGENT_PROCESS_MARKERS = {
    "claude": {
        "commands": {"claude"},
        "arg_markers": {"@anthropic-ai/claude-code"},
    },
    "codex": {
        "commands": {"codex"},
        "arg_markers": {"@openai/codex"},
    },
}


# ------------------------------------------------------------------ #
#  辅助函数
# ------------------------------------------------------------------ #

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[tab-color-daemon] {ts}  {msg}", flush=True)


def extract_uuid(iterm2_session_id: str) -> str:
    """'w0t1p2:UUID' → 'UUID'，纯 UUID 原样返回。"""
    return iterm2_session_id.split(":")[-1] if ":" in iterm2_session_id else iterm2_session_id


def extract_tab_prefix(iterm2_session_id: str) -> str:
    """'w0t1p2:UUID' → 'w0t1'；无法解析时返回空字符串。"""
    prefix = iterm2_session_id.split(":", 1)[0]
    return prefix.rsplit("p", 1)[0] if "p" in prefix else ""


def is_active_tab(app, session) -> bool:
    """判断 session 所在 tab 是否为当前活跃 tab。"""
    try:
        window = session.tab.window if session.tab else None
        if window is None:
            return False
        active_tab = window.current_tab
        return active_tab is not None and active_tab.tab_id == session.tab.tab_id
    except Exception:
        return False


def _is_shell_job(job_name: str) -> bool:
    shells = {"bash", "fish", "sh", "tcsh", "zsh", "-bash", "-fish", "-sh", "-tcsh", "-zsh"}
    return Path(job_name).name.lower() in shells


async def session_foreground_is_shell(session) -> bool:
    """快速判断 pane 前台是否已回到 shell。失败时返回 False，交给 poller 保守处理。"""
    try:
        job_name = await session.async_get_variable("jobName")
        return isinstance(job_name, str) and _is_shell_job(job_name)
    except Exception:
        return False


def _normalize_agent(agent: Optional[str]) -> str:
    return agent if agent in AGENT_PROCESS_MARKERS else "claude"


def agent_from_state(state: dict) -> str:
    """旧 state 没有 agent 字段，默认按 Claude 处理以兼容升级。"""
    return _normalize_agent(state.get("agent"))


def _command_matches_agent(comm: str, args: str, agent: str) -> bool:
    spec = AGENT_PROCESS_MARKERS[_normalize_agent(agent)]
    comm_name = Path(comm).name.lower()
    if comm_name in spec["commands"]:
        return True

    try:
        argv0 = shlex.split(args)[0] if args else ""
    except ValueError:
        argv0 = args.split(maxsplit=1)[0] if args else ""
    argv0_name = Path(argv0).name.lower()
    if argv0_name in spec["commands"]:
        return True

    args_lower = args.lower()
    return any(marker in args_lower for marker in spec["arg_markers"])


def _tty_has_agent_process(tty: str, agent: str) -> Optional[bool]:
    """扫描 tty 上的进程；None 表示无法判断。"""
    tty_name = tty.replace("/dev/", "")
    if not tty_name:
        return None

    try:
        result = subprocess.run(
            ["ps", "-t", tty_name, "-o", "comm=,args="],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            comm, _, args = line.partition(" ")
            if _command_matches_agent(comm, args.strip(), agent):
                return True
        return False
    except Exception:
        return None


def _process_tree_has_agent(root_pid: int, agent: str) -> Optional[bool]:
    """从 root_pid 向下扫描进程树；None 表示无法判断。"""
    try:
        pending = [str(root_pid)]
        seen = set()
        while pending:
            parent = pending.pop()
            if parent in seen:
                continue
            seen.add(parent)

            cmd_result = subprocess.run(
                ["ps", "-p", parent, "-o", "comm=,args="],
                capture_output=True, text=True, timeout=3
            )
            if cmd_result.returncode == 0:
                line = cmd_result.stdout.strip()
                comm, _, args = line.partition(" ")
                if _command_matches_agent(comm, args.strip(), agent):
                    return True

            result = subprocess.run(
                ["pgrep", "-P", parent],
                capture_output=True, text=True, timeout=3
            )
            pending.extend(pid for pid in result.stdout.split() if pid)
        return False
    except Exception:
        return None


def _find_tmux_bin() -> str:
    """查找 tmux 可执行文件路径。launchd 环境 PATH 最小，需用绝对路径。"""
    for candidate in [
        "/opt/homebrew/bin/tmux",
        "/usr/local/bin/tmux",
        "/usr/bin/tmux",
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # fallback: 依赖 PATH
    return "tmux"


_TMUX_BIN: Optional[str] = None


def _tmux_bin() -> str:
    global _TMUX_BIN
    if _TMUX_BIN is None:
        _TMUX_BIN = _find_tmux_bin()
    return _TMUX_BIN


def _tmux_session_for_tty(tty: str) -> Optional[str]:
    """给定 /dev/ttysXXX，返回该 tty 上 tmux client 所属的 session 名称。"""
    if not tty:
        return None
    try:
        result = subprocess.run(
            [_tmux_bin(), "list-clients", "-F", "#{client_tty} #{session_name}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0] == tty:
                return parts[1]
        return None
    except Exception:
        return None


def _tmux_session_has_agent(tmux_session: str, agent: str) -> Optional[bool]:
    """检查 tmux session 任意 pane 的进程树中是否有 agent。"""
    try:
        result = subprocess.run(
            [_tmux_bin(), "list-panes", "-t", tmux_session, "-a", "-F", "#{pane_pid}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        for pane_pid in result.stdout.splitlines():
            pane_pid = pane_pid.strip()
            if not pane_pid:
                continue
            tree_result = _process_tree_has_agent(int(pane_pid), agent)
            if tree_result is True:
                return True
        return False
    except Exception:
        return None


async def is_agent_running(session, agent: str) -> bool:
    """检测 iTerm2 pane 内是否还有指定 AI CLI 进程。

    优先使用 iTerm2 变量中的 tty 扫描真实终端进程。旧实现依赖
    session.server_pid，但该字段在部分 iTerm2 版本中为 None，会导致
    已退出的 agent pane 被误判为仍在运行。

    当 iTerm2 pane 前台是 shell 时，额外检查该 tty 是否有 tmux client —
    如果是，穿透 tmux session 检查 pane 进程树中的 agent。

    检测完全失败时返回 True（宁可不删，避免误清理）。
    """
    agent = _normalize_agent(agent)

    # 优先检查 tmux 穿透：无论 jobName 是 shell 还是 tmux，
    # 只要 tty 上有 tmux client，agent 可能藏在 tmux 进程树内。
    try:
        tty = await session.async_get_variable("tty")
        if isinstance(tty, str):
            tmux_session = _tmux_session_for_tty(tty)
            if tmux_session is not None:
                tmux_result = _tmux_session_has_agent(tmux_session, agent)
                if tmux_result is True:
                    return True
                if tmux_result is False:
                    return False
                # None = 无法判断，保守认为仍在运行
                return True
    except Exception:
        pass

    if await session_foreground_is_shell(session):
        return False

    try:
        tty = await session.async_get_variable("tty")
        if isinstance(tty, str):
            tty_result = _tty_has_agent_process(tty, agent)
            if tty_result is not None:
                return tty_result
    except Exception:
        pass

    root_pid = getattr(session, "server_pid", None)
    if not root_pid:
        try:
            root_pid = await session.async_get_variable("pid")
        except Exception:
            root_pid = None

    if root_pid:
        tree_result = _process_tree_has_agent(int(root_pid), agent)
        if tree_result is not None:
            return tree_result

    return True


async def is_claude_running(session) -> bool:
    """兼容旧测试/调用点。"""
    return await is_agent_running(session, "claude")


async def apply_tab_color(
    connection,
    iterm2_session_id: str,
    color,
    *,
    app=None,
    session=None,
    applied_colors: Optional[dict] = None,
):
    """
    给同一 tab 下所有 pane 都设 tab color。
    color=None 表示重置（恢复默认色）。
    活跃 tab 上色时跳过（用户已经在看了）。
    """
    uuid = extract_uuid(iterm2_session_id)
    if app is None or session is None:
        app = await iterm2.async_get_app(connection)
        session = app.get_session_by_id(uuid)
    if session is None:
        return

    if color is not None and is_active_tab(app, session):
        return

    target_tab = session.tab
    target_sessions = [session] if target_tab is None else list(target_tab.sessions)
    tab_id = session.session_id if target_tab is None else target_tab.tab_id
    desired_state = _applied_tab_color_state(target_sessions, color)

    if applied_colors is not None and applied_colors.get(tab_id) == desired_state:
        return

    for s in target_sessions:
        change = iterm2.LocalWriteOnlyProfile()
        if color is None:
            change.set_use_tab_color(False)
        else:
            change.set_tab_color(color)
            change.set_use_tab_color(True)
        await s.async_set_profile_properties(change)
        await write_tab_color_escape(s, color)

    if applied_colors is not None:
        applied_colors[tab_id] = desired_state


def compute_color_stage(idle_seconds: float) -> str:
    if idle_seconds >= THRESHOLD_RED_SEC:
        return "red"
    elif idle_seconds >= THRESHOLD_YELLOW_SEC:
        return "yellow"
    return "green"


def color_for_stage(stage: str) -> iterm2.Color:
    return {"green": COLOR_GREEN, "yellow": COLOR_YELLOW, "red": COLOR_RED}[stage]


def max_stage(left: str, right: str) -> str:
    return left if STAGE_RANK.get(left, 0) >= STAGE_RANK.get(right, 0) else right


def _color_component(value) -> int:
    return max(0, min(255, int(round(value))))


def _color_attr(color, primary: str, fallback: str):
    if hasattr(color, primary):
        return getattr(color, primary)
    return getattr(color, fallback)


def _tab_color_cache_value(color: Optional[iterm2.Color]):
    if color is None:
        return ("default",)
    red = _color_attr(color, "red", "r")
    green = _color_attr(color, "green", "g")
    blue = _color_attr(color, "blue", "b")
    return ("rgb", _color_component(red), _color_component(green), _color_component(blue))


def _applied_tab_color_state(target_sessions: list, color: Optional[iterm2.Color]):
    session_ids = tuple(str(getattr(session, "session_id", "")) for session in target_sessions)
    return session_ids, _tab_color_cache_value(color)


def escape_sequences_for_tab_color(color: Optional[iterm2.Color]) -> list[str]:
    if color is None:
        return ["\033]6;1;bg;*;default\a"]
    red = _color_attr(color, "red", "r")
    green = _color_attr(color, "green", "g")
    blue = _color_attr(color, "blue", "b")
    return [
        f"\033]6;1;bg;red;brightness;{_color_component(red)}\a",
        f"\033]6;1;bg;green;brightness;{_color_component(green)}\a",
        f"\033]6;1;bg;blue;brightness;{_color_component(blue)}\a",
    ]


def _write_escape_to_tty(tty: str, sequences: list[str]):
    if not isinstance(tty, str) or not tty.startswith("/dev/tty"):
        return
    try:
        with open(tty, "w") as f:
            for seq in sequences:
                f.write(seq)
    except OSError:
        pass


async def write_tab_color_escape(session, color: Optional[iterm2.Color]):
    try:
        tty = await session.async_get_variable("tty")
    except Exception:
        return
    _write_escape_to_tty(tty, escape_sequences_for_tab_color(color))


def _profile_value(profile, name: str):
    value = getattr(profile, name, None)
    return value() if callable(value) else value


async def session_uses_tab_color(session) -> bool:
    try:
        profile = await session.async_get_profile()
        return bool(_profile_value(profile, "use_tab_color"))
    except Exception:
        return False


async def reset_untracked_tab_colors(connection, states: dict[str, dict], *, app=None):
    """重置没有 idle state、但仍残留 tab color 的 tab。"""
    if app is None:
        app = await iterm2.async_get_app(connection)

    idle_tab_ids = set()
    for state in states.values():
        sid = state.get("iterm2_session", "")
        if not sid:
            continue
        session = app.get_session_by_id(extract_uuid(sid))
        if session is not None and session.tab is not None:
            idle_tab_ids.add(session.tab.tab_id)

    for window in app.terminal_windows or []:
        for tab in window.tabs:
            if tab.tab_id in idle_tab_ids:
                continue
            sessions = list(tab.sessions)
            if not sessions:
                continue
            if not any([await session_uses_tab_color(s) for s in sessions]):
                continue
            log(f"清理残留颜色: tab {tab.tab_id} 没有 idle state → 重置颜色")
            await apply_tab_color(connection, sessions[0].session_id, None, app=app, session=sessions[0])


def read_state_files() -> dict[str, dict]:
    """读取所有 idle_state/*.json，返回 {文件路径: state_dict}。"""
    result = {}
    if not IDLE_STATE_DIR.exists():
        return result
    for f in IDLE_STATE_DIR.glob("*.json"):
        try:
            result[str(f)] = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return result


def update_stage_file(path: str, state: dict, stage: str):
    state["color_stage"] = stage
    try:
        Path(path).write_text(json.dumps(state))
    except OSError:
        pass


async def prune_finished_state_files(connection, states: dict[str, dict], *, app=None) -> dict[str, dict]:
    """轻量清理已退出的 state，只用 iTerm2 session/jobName，不做 ps/pgrep 扫描。"""
    if not states:
        return states

    if app is None:
        app = await iterm2.async_get_app(connection)
    remaining = dict(states)

    now = time.time()
    for path, state in list(states.items()):
        sid = state.get("iterm2_session", "")
        if not sid:
            continue

        # 新写入的 state 文件在 MIN_STATE_AGE_SEC 内不清理，
        # 防止 Stop hook 刚写完就被 jobName 瞬变误删。
        idle_since = state.get("idle_since", 0)
        if now - idle_since < MIN_STATE_AGE_SEC:
            continue

        uuid = extract_uuid(sid)
        session = app.get_session_by_id(uuid)
        reason = ""

        if session is None:
            reason = "iTerm2 session 已不存在"
        else:
            # tmux 穿透：无论 jobName 是 shell 还是 tmux，
            # 只要 tty 上有 tmux client，agent 可能藏在 tmux 内，跳过快速清理。
            try:
                tty = await session.async_get_variable("tty")
                if isinstance(tty, str) and _tmux_session_for_tty(tty) is not None:
                    continue
            except Exception:
                pass
            if await session_foreground_is_shell(session):
                reason = "pane 已回到 shell"

        if not reason:
            continue

        log(f"快速清理: {uuid[:8]}… {reason}")
        try:
            Path(path).unlink()
        except OSError:
            pass
        remaining.pop(path, None)

    return remaining


# ------------------------------------------------------------------ #
#  watch：唯一的颜色写入者（500ms 周期）
# ------------------------------------------------------------------ #

async def watch_idle_dir(connection):
    """
    每 500ms 扫描，唯一负责 iTerm2 API 调用的循环。

    逻辑：
    - 有 state 文件 + 非活跃 tab → 按 color_stage 上色
    - 有 state 文件 + 活跃 tab   → 重置（白色）
    - state 对应 pane 回到 shell → 轻量删除 state
    - 无 state 文件（刚删除）     → 重置（白色）
    """
    known: dict[str, dict] = {}
    applied_colors: dict = {}
    last_exit_check = 0.0

    # 启动恢复：对所有已有文件按当前空闲时长设色
    now = time.time()
    for path, state in read_state_files().items():
        sid = state.get("iterm2_session", "")
        if not sid:
            continue
        idle_seconds = now - state.get("idle_since", now)
        stage = compute_color_stage(idle_seconds)
        update_stage_file(path, state, stage)
        log(f"启动恢复: session {extract_uuid(sid)[:8]}… idle {idle_seconds/60:.1f}min → {stage}")
        known[path] = state

    # 启动后立即做一次全量上色
    await _apply_all_colors(connection, applied_colors=applied_colors)
    await reset_untracked_tab_colors(connection, known)

    while True:
        await asyncio.sleep(0.5)
        try:
            current = read_state_files()

            now = time.monotonic()
            app = None
            if now - last_exit_check >= FAST_EXIT_CHECK_INTERVAL:
                app = await iterm2.async_get_app(connection)
                if current:
                    current = await prune_finished_state_files(connection, current, app=app)
                await reset_untracked_tab_colors(connection, current, app=app)
                last_exit_check = now

            # 消失的文件 → 记住 session 以便重置
            disappeared = []
            for path, state in known.items():
                if path not in current:
                    sid = state.get("iterm2_session", "")
                    if sid:
                        log(f"session {extract_uuid(sid)[:8]}… 恢复活跃 → 重置颜色")
                        disappeared.append(sid)

            known = current

            # 全量刷新颜色：每个 state 文件根据 活跃/非活跃 决定颜色
            await _apply_all_colors(connection, states=current, app=app, applied_colors=applied_colors)

            current_tab_prefixes = {
                prefix
                for s in current.values()
                if (prefix := extract_tab_prefix(s.get("iterm2_session", "")))
            }

            # 重置已消失的 session；同 tab 仍有其他 idle state 时不能把整 tab 变白
            for sid in disappeared:
                if extract_tab_prefix(sid) not in current_tab_prefixes:
                    await apply_tab_color(connection, sid, None, applied_colors=applied_colors)

        except Exception as e:
            log(f"watch 出错: {e}")
            if "close" in str(e).lower() or "connection" in str(e).lower():
                log("连接已断，退出等待 launchd 重启")
                return


async def _apply_all_colors(connection, *, states=None, app=None, applied_colors=None):
    """读所有 state 文件，根据 color_stage + is_active 统一应用颜色。"""
    if states is None:
        states = read_state_files()
    if not states:
        return

    if app is None:
        app = await iterm2.async_get_app(connection)
    tabs: dict[str, dict] = {}

    for path, state in states.items():
        sid = state.get("iterm2_session", "")
        if not sid:
            continue
        uuid = extract_uuid(sid)
        session = app.get_session_by_id(uuid)
        if session is None:
            continue

        stage = state.get("color_stage", "green")
        tab_id = session.tab.tab_id if session.tab is not None else session.session_id
        tab_state = tabs.setdefault(tab_id, {
            "sid": sid,
            "session": session,
            "stage": stage,
            "active": False,
        })
        tab_state["stage"] = max_stage(tab_state["stage"], stage)
        tab_state["active"] = tab_state["active"] or is_active_tab(app, session)

    for tab_state in tabs.values():
        if tab_state["active"]:
            await apply_tab_color(
                connection,
                tab_state["sid"],
                None,
                app=app,
                session=tab_state["session"],
                applied_colors=applied_colors,
            )
        else:
            color = color_for_stage(tab_state["stage"])
            await apply_tab_color(
                connection,
                tab_state["sid"],
                color,
                app=app,
                session=tab_state["session"],
                applied_colors=applied_colors,
            )


# ------------------------------------------------------------------ #
#  poller：只更新 state 文件，不碰颜色（30s 周期）
# ------------------------------------------------------------------ #

async def color_poller(connection):
    """
    每 POLL_INTERVAL 秒更新 state 文件元数据：
    - 清理孤儿 state 文件（iTerm2 session 已不存在 或 agent 进程已退出）
    - 同 tab 多 session 聚合（全部退出/恢复活跃后才重置）
    - 升级 color_stage（green → yellow → red）

    绝不直接调用 iTerm2 API 设色 —— 那是 watch 的职责。
    """
    log(f"守护进程启动 ✅  监听间隔=0.5s  快速清理间隔={FAST_EXIT_CHECK_INTERVAL:.1f}s  "
        f"轮询间隔={POLL_INTERVAL}s  "
        f"黄色阈值={CFG['THRESHOLD_YELLOW']}min  红色阈值={CFG['THRESHOLD_RED']}min")

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            now = time.time()
            states = read_state_files()
            app = await iterm2.async_get_app(connection)

            # ---- 1. 清理孤儿文件 ----
            for path, state in list(states.items()):
                sid = state.get("iterm2_session", "")
                if not sid:
                    continue
                uuid = extract_uuid(sid)
                session = app.get_session_by_id(uuid)
                if session is None:
                    log(f"清理孤儿: {uuid[:8]}… iTerm2 session 已不存在")
                    try:
                        Path(path).unlink()
                    except OSError:
                        pass
                    states.pop(path, None)
                else:
                    agent = agent_from_state(state)
                    if await is_agent_running(session, agent):
                        continue
                    log(f"清理孤儿: {uuid[:8]}… {agent} 进程已退出")
                    try:
                        Path(path).unlink()
                    except OSError:
                        pass
                    states.pop(path, None)

            # ---- 2. 升级 color_stage（只改文件，不改颜色）----
            for path, state in states.items():
                idle_since = state.get("idle_since", now)
                prev_stage = state.get("color_stage", "green")
                idle_seconds = now - idle_since
                new_stage = compute_color_stage(idle_seconds)

                if new_stage != prev_stage:
                    log(f"session {extract_uuid(state.get('iterm2_session',''))[:8]}… "
                        f"idle {idle_seconds/60:.1f}min → {prev_stage} → {new_stage}")
                    update_stage_file(path, state, new_stage)

            idle_tabs = {
                prefix
                for state in states.values()
                if (prefix := extract_tab_prefix(state.get("iterm2_session", "")))
            }
            idle_count = len(idle_tabs) or len(states)
            if 0 < idle_count < CONCURRENT_TARGET:
                log(f"提示：当前 {idle_count} 个 tab 等待中，"
                    f"目标并发 {CONCURRENT_TARGET}，可以多开任务 💪")

        except Exception as e:
            log(f"poll 出错: {e}")
            if "close" in str(e).lower() or "connection" in str(e).lower():
                log("连接已断，退出等待 launchd 重启")
                return


# ------------------------------------------------------------------ #
#  入口
# ------------------------------------------------------------------ #

async def main(connection):
    asyncio.create_task(watch_idle_dir(connection))
    asyncio.create_task(color_poller(connection))
    await asyncio.Event().wait()


def run_daemon():
    iterm2.run_forever(main, retry=True)


if __name__ == "__main__":
    run_daemon()
