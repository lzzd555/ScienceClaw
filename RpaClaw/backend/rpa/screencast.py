"""Session-scoped CDP screencast streaming with active-tab switching."""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_DEFAULT_SCREENCAST_WIDTH = 1280
_DEFAULT_SCREENCAST_HEIGHT = 720
_INPUT_METRICS_REFRESH_INTERVAL_S = 0.25

_KEY_TO_VIRTUAL_KEY_CODE: Dict[str, int] = {
    "Backspace": 8,
    "Space": 32,
    "Tab": 9,
    "Enter": 13,
    "Escape": 27,
    "ArrowLeft": 37,
    "ArrowUp": 38,
    "ArrowRight": 39,
    "ArrowDown": 40,
    "Delete": 46,
}

_CODE_TO_VIRTUAL_KEY_CODE: Dict[str, int] = {
    "Space": 32,
    "Backquote": 192,
    "Minus": 189,
    "Equal": 187,
    "BracketLeft": 219,
    "BracketRight": 221,
    "Backslash": 220,
    "Semicolon": 186,
    "Quote": 222,
    "Comma": 188,
    "Period": 190,
    "Slash": 191,
}

_CODE_TO_UNMODIFIED_TEXT: Dict[str, str] = {
    "Space": " ",
    "Backquote": "`",
    "Minus": "-",
    "Equal": "=",
    "BracketLeft": "[",
    "BracketRight": "]",
    "Backslash": "\\",
    "Semicolon": ";",
    "Quote": "'",
    "Comma": ",",
    "Period": ".",
    "Slash": "/",
    "Enter": "\r",
    "NumpadEnter": "\r",
}


def _infer_virtual_key_code(key: str, code: str) -> int:
    if code.startswith("Key") and len(code) == 4:
        return ord(code[-1].upper())
    if code.startswith("Digit") and len(code) == 6 and code[-1].isdigit():
        return ord(code[-1])
    if code in _CODE_TO_VIRTUAL_KEY_CODE:
        return _CODE_TO_VIRTUAL_KEY_CODE[code]
    if key in _KEY_TO_VIRTUAL_KEY_CODE:
        return _KEY_TO_VIRTUAL_KEY_CODE[key]
    if len(key) == 1:
        return ord(key.upper())
    return 0


def _infer_unmodified_text(key: str, code: str) -> Optional[str]:
    if code.startswith("Key") and len(code) == 4:
        return code[-1].lower()
    if code.startswith("Digit") and len(code) == 6 and code[-1].isdigit():
        return code[-1]
    if code in _CODE_TO_UNMODIFIED_TEXT:
        return _CODE_TO_UNMODIFIED_TEXT[code]
    if key == "Enter":
        return "\r"
    return None


def _build_cdp_key_event(event: Dict[str, Any]) -> Dict[str, Any]:
    action = event.get("action", "keyDown")
    key = event.get("key", "") or ""
    code = event.get("code", "") or ""
    modifiers = event.get("modifiers", 0)
    text = event.get("text")
    vk_code = int(event.get("windowsVirtualKeyCode") or event.get("nativeVirtualKeyCode") or 0)
    if vk_code <= 0:
        vk_code = _infer_virtual_key_code(key, code)

    payload: Dict[str, Any] = {
        "type": action,
        "key": key,
        "code": code,
        "modifiers": modifiers,
    }
    if vk_code > 0:
        payload["windowsVirtualKeyCode"] = vk_code
        payload["nativeVirtualKeyCode"] = vk_code

    if action != "keyUp":
        has_text = False
        key_text: Optional[str] = None
        unmodified_text: Optional[str] = None
        if not (modifiers & 0b0111):
            if isinstance(text, str) and text:
                key_text = text
                unmodified_text = _infer_unmodified_text(key, code) or key_text
                has_text = True
            else:
                inferred_unmodified = _infer_unmodified_text(key, code)
                if inferred_unmodified is not None:
                    key_text = inferred_unmodified
                    unmodified_text = inferred_unmodified
                    has_text = True

        if has_text and key_text is not None and unmodified_text is not None:
            payload["type"] = "keyDown"
            payload["text"] = key_text
            payload["unmodifiedText"] = unmodified_text
        else:
            payload["type"] = "rawKeyDown"
    return payload


def _pick_dimension(*values: Any, default: int) -> int:
    for value in values:
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return default


async def _fetch_css_viewport_size(cdp: Any, fallback_width: int, fallback_height: int) -> tuple[int, int]:
    if not cdp:
        return fallback_width, fallback_height

    try:
        metrics = await cdp.send("Page.getLayoutMetrics", {})
    except Exception:
        return fallback_width, fallback_height

    css_viewport = metrics.get("cssVisualViewport", {}) if isinstance(metrics, dict) else {}
    width = _pick_dimension(css_viewport.get("clientWidth"), default=fallback_width)
    height = _pick_dimension(css_viewport.get("clientHeight"), default=fallback_height)
    return width, height


def _resolve_pointer_coordinates(event: Dict[str, Any], width: int, height: int) -> tuple[float, float]:
    coordinate_space = event.get("coordinateSpace", "css-pixel")
    x = float(event.get("x", 0) or 0)
    y = float(event.get("y", 0) or 0)
    if coordinate_space == "normalized":
        return x * width, y * height
    return x, y


class SessionScreencastController:
    """Streams the currently active tab and switches targets when active tab changes."""

    def __init__(
        self,
        page_provider: Callable[[], Any],
        tabs_provider: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._page_provider = page_provider
        self._tabs_provider = tabs_provider
        self._ws: Optional[WebSocket] = None
        self._running = False
        self._page = None
        self._cdp = None
        self._frame_width = _DEFAULT_SCREENCAST_WIDTH
        self._frame_height = _DEFAULT_SCREENCAST_HEIGHT
        self._input_width = _DEFAULT_SCREENCAST_WIDTH
        self._input_height = _DEFAULT_SCREENCAST_HEIGHT
        self._last_input_metrics_refresh = 0.0
        self._tabs_snapshot_json = ""
        self._last_preview_error = ""
        self._frames_sent = 0
        self._started_at = time.time()

    async def start(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._running = True
        logger.info("[Screencast] start controller")
        await self._emit_tabs_snapshot(force=True)
        await self._switch_page_if_needed(force=True)

        monitor_task = asyncio.create_task(self._monitor_loop())
        try:
            await self._recv_loop()
        except WebSocketDisconnect:
            logger.info("[Screencast] WebSocket disconnected")
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            await self.stop()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info(
            "[Screencast] stop controller frames_sent=%s uptime_s=%.2f",
            self._frames_sent,
            time.time() - self._started_at,
        )
        await self._detach_current_page()

    async def _monitor_loop(self) -> None:
        while self._running:
            await self._emit_tabs_snapshot()
            try:
                await self._switch_page_if_needed()
            except Exception as exc:
                logger.error(f"[Screencast] switch error: {exc}")
                await self._emit_preview_error(str(exc))
            await asyncio.sleep(0.2)

    async def _emit_tabs_snapshot(self, force: bool = False) -> None:
        if not self._ws:
            return
        tabs = self._tabs_provider()
        payload = json.dumps(tabs, ensure_ascii=False, sort_keys=True)
        if not force and payload == self._tabs_snapshot_json:
            return
        self._tabs_snapshot_json = payload
        await self._ws.send_json({"type": "tabs_snapshot", "tabs": tabs})

    async def _switch_page_if_needed(self, force: bool = False) -> None:
        next_page = self._page_provider()
        if next_page is None:
            if self._page is not None:
                logger.info("[Screencast] active page disappeared, detaching current page")
                await self._detach_current_page()
            return
        if not force and next_page is self._page:
            return

        await self._detach_current_page()
        last_error = None
        for attempt in range(2):
            try:
                self._page = next_page
                logger.info(
                    "[Screencast] attaching to page attempt=%s page_id=%s url=%s",
                    attempt + 1,
                    id(next_page),
                    getattr(next_page, "url", ""),
                )
                self._cdp = await next_page.context.new_cdp_session(next_page)
                self._cdp.on("Page.screencastFrame", self._on_frame)
                await self._cdp.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": 40,
                        "everyNthFrame": 1,
                    },
                )
                await self._refresh_input_metrics(force=True)
                self._last_preview_error = ""
                logger.info(
                    "[Screencast] attached to page page_id=%s url=%s",
                    id(next_page),
                    getattr(next_page, "url", ""),
                )
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[Screencast] failed to attach page attempt=%s page_id=%s url=%s error=%r",
                    attempt + 1,
                    id(next_page),
                    getattr(next_page, "url", ""),
                    exc,
                )
                await self._detach_current_page()
                if attempt == 0:
                    await asyncio.sleep(0.2)

        assert last_error is not None
        await self._emit_preview_error(f"preview switch failed: {last_error}")
        raise last_error

    async def _detach_current_page(self) -> None:
        if not self._cdp:
            return
        logger.info(
            "[Screencast] detaching page page_id=%s url=%s",
            id(self._page) if self._page is not None else None,
            getattr(self._page, "url", "") if self._page is not None else "",
        )
        try:
            await self._cdp.send("Page.stopScreencast", {})
        except Exception:
            pass
        try:
            await self._cdp.detach()
        except Exception:
            pass
        self._cdp = None
        self._page = None

    async def _emit_preview_error(self, message: str) -> None:
        if not self._ws or not self._running:
            return
        if message == self._last_preview_error:
            return
        self._last_preview_error = message
        try:
            await self._ws.send_json({"type": "preview_error", "message": message})
        except Exception:
            self._running = False

    async def send_monitor_log(self, level: str, message: str) -> None:
        """Send a monitor log event to the frontend via WebSocket."""
        if not self._ws or not self._running:
            return
        try:
            await self._ws.send_json({"type": "monitor_log", "level": level, "message": message})
        except Exception:
            pass

    async def _recv_loop(self) -> None:
        while self._running:
            try:
                raw = await asyncio.wait_for(self._ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await self._ws.send_json({"type": "ping", "ts": time.time()})
                except Exception:
                    break
                continue

            try:
                msg: Dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[Screencast] invalid JSON from client")
                continue

            msg_type = msg.get("type")
            if msg_type == "mouse":
                await self._dispatch_mouse(msg)
            elif msg_type == "keyboard":
                await self._dispatch_key(msg)
            elif msg_type == "wheel":
                await self._dispatch_wheel(msg)
            elif msg_type == "paste":
                await self._dispatch_paste(msg)

    async def _dispatch_paste(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        text = event.get("text", "")
        if not text:
            return
        try:
            await self._cdp.send("Input.insertText", {"text": text})
        except Exception as exc:
            logger.debug(f"[Screencast] paste dispatch error: {exc}")

    async def _refresh_input_metrics(self, force: bool = False) -> None:
        if not self._cdp:
            return
        now = time.time()
        if not force and now - self._last_input_metrics_refresh < _INPUT_METRICS_REFRESH_INTERVAL_S:
            return

        self._input_width, self._input_height = await _fetch_css_viewport_size(
            self._cdp,
            fallback_width=self._input_width,
            fallback_height=self._input_height,
        )
        self._last_input_metrics_refresh = now

    async def _on_frame(self, params: Dict[str, Any]) -> None:
        if not self._cdp or not self._ws or not self._running:
            return

        session_id = params.get("sessionId")
        if session_id:
            try:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

        metadata = params.get("metadata", {})
        device_w = metadata.get("deviceWidth")
        device_h = metadata.get("deviceHeight")
        self._frame_width = _pick_dimension(device_w, default=self._frame_width)
        self._frame_height = _pick_dimension(device_h, default=self._frame_height)
        await self._refresh_input_metrics()

        try:
            await self._ws.send_json(
                {
                    "type": "frame",
                    "data": params.get("data", ""),
                    "metadata": {
                        "width": self._frame_width,
                        "height": self._frame_height,
                        "frameWidth": self._frame_width,
                        "frameHeight": self._frame_height,
                        "inputWidth": self._input_width,
                        "inputHeight": self._input_height,
                        "timestamp": metadata.get("timestamp", time.time()),
                    },
                }
            )
            self._frames_sent += 1
            if self._frames_sent == 1:
                logger.info(
                    "[Screencast] first frame sent frame=%sx%s input=%sx%s",
                    self._frame_width,
                    self._frame_height,
                    self._input_width,
                    self._input_height,
                )
            self._last_preview_error = ""
        except Exception:
            self._running = False

    async def _dispatch_mouse(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x, y = _resolve_pointer_coordinates(event, self._input_width, self._input_height)
        params: Dict[str, Any] = {
            "type": event.get("action", "mouseMoved"),
            "x": x,
            "y": y,
            "button": event.get("button", "left") if event.get("action") != "mouseMoved" else "none",
            "clickCount": event.get("clickCount", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] mouse dispatch error: {exc}")

    async def _dispatch_key(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        params = _build_cdp_key_event(event)
        try:
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] key dispatch error: {exc}")

    async def _dispatch_wheel(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x, y = _resolve_pointer_coordinates(event, self._input_width, self._input_height)
        params: Dict[str, Any] = {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": event.get("deltaX", 0),
            "deltaY": event.get("deltaY", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] wheel dispatch error: {exc}")


class ScreencastService:
    """Backward-compatible single-page screencast service used by session preview routes."""

    def __init__(self, cdp_session: Any) -> None:
        self._cdp = cdp_session
        self._ws: Optional[WebSocket] = None
        self._running = False
        self._frame_width = _DEFAULT_SCREENCAST_WIDTH
        self._frame_height = _DEFAULT_SCREENCAST_HEIGHT
        self._input_width = _DEFAULT_SCREENCAST_WIDTH
        self._input_height = _DEFAULT_SCREENCAST_HEIGHT
        self._last_input_metrics_refresh = 0.0

    async def start(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._running = True
        self._cdp.on("Page.screencastFrame", self._on_frame)
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 40,
                "everyNthFrame": 1,
            },
        )
        await self._refresh_input_metrics(force=True)
        try:
            await self._recv_loop()
        except WebSocketDisconnect:
            logger.info("[Screencast] WebSocket disconnected")
        finally:
            await self.stop()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if not self._cdp:
            return
        try:
            await self._cdp.send("Page.stopScreencast", {})
        except Exception:
            pass

    async def _recv_loop(self) -> None:
        while self._running and self._ws:
            try:
                raw = await asyncio.wait_for(self._ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await self._ws.send_json({"type": "ping", "ts": time.time()})
                except Exception:
                    break
                continue

            try:
                msg: Dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[Screencast] invalid JSON from client")
                continue

            msg_type = msg.get("type")
            if msg_type == "mouse":
                await self._dispatch_mouse(msg)
            elif msg_type == "keyboard":
                await self._dispatch_key(msg)
            elif msg_type == "wheel":
                await self._dispatch_wheel(msg)
            elif msg_type == "paste":
                await self._dispatch_paste(msg)

    async def _dispatch_paste(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        text = event.get("text", "")
        if not text:
            return
        try:
            await self._cdp.send("Input.insertText", {"text": text})
        except Exception as exc:
            logger.debug(f"[Screencast] paste dispatch error: {exc}")

    async def _refresh_input_metrics(self, force: bool = False) -> None:
        if not self._cdp:
            return
        now = time.time()
        if not force and now - self._last_input_metrics_refresh < _INPUT_METRICS_REFRESH_INTERVAL_S:
            return

        self._input_width, self._input_height = await _fetch_css_viewport_size(
            self._cdp,
            fallback_width=self._input_width,
            fallback_height=self._input_height,
        )
        self._last_input_metrics_refresh = now

    async def _on_frame(self, params: Dict[str, Any]) -> None:
        if not self._cdp or not self._ws or not self._running:
            return

        session_id = params.get("sessionId")
        if session_id:
            try:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

        metadata = params.get("metadata", {})
        device_w = metadata.get("deviceWidth")
        device_h = metadata.get("deviceHeight")
        self._frame_width = _pick_dimension(device_w, default=self._frame_width)
        self._frame_height = _pick_dimension(device_h, default=self._frame_height)
        await self._refresh_input_metrics()

        try:
            await self._ws.send_json(
                {
                    "type": "frame",
                    "data": params.get("data", ""),
                    "metadata": {
                        "width": self._frame_width,
                        "height": self._frame_height,
                        "frameWidth": self._frame_width,
                        "frameHeight": self._frame_height,
                        "inputWidth": self._input_width,
                        "inputHeight": self._input_height,
                        "timestamp": metadata.get("timestamp", time.time()),
                    },
                }
            )
        except Exception:
            self._running = False

    async def _dispatch_mouse(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x, y = _resolve_pointer_coordinates(event, self._input_width, self._input_height)
        params: Dict[str, Any] = {
            "type": event.get("action", "mouseMoved"),
            "x": x,
            "y": y,
            "button": event.get("button", "left") if event.get("action") != "mouseMoved" else "none",
            "clickCount": event.get("clickCount", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] mouse dispatch error: {exc}")

    async def _dispatch_key(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        params = _build_cdp_key_event(event)
        try:
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] key dispatch error: {exc}")

    async def _dispatch_wheel(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x, y = _resolve_pointer_coordinates(event, self._input_width, self._input_height)
        params: Dict[str, Any] = {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": event.get("deltaX", 0),
            "deltaY": event.get("deltaY", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] wheel dispatch error: {exc}")
