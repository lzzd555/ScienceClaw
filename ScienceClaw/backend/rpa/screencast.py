"""ScreencastService — CDP screencast streaming + input injection.

Bridges a frontend WebSocket and a CDP browser session:
  Browser → Frontend: Page.startScreencast → screencastFrame → WS send (base64 jpeg)
  Frontend → Browser: WS receive → Input.dispatchMouseEvent / Input.dispatchKeyEvent
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# CDP mouse button name → buttonIndex mapping
_BUTTON_INDEX = {"left": 0, "middle": 1, "right": 2, "back": 3, "forward": 4}


class ScreencastService:
    """Streams CDP screencast frames to a WebSocket and injects input events."""

    def __init__(self, cdp_session) -> None:
        self._cdp = cdp_session
        self._ws: Optional[WebSocket] = None
        self._viewport_width: int = 1280
        self._viewport_height: int = 720
        self._running: bool = False

    # ── public API ──────────────────────────────────────────────────

    async def start(self, websocket: WebSocket) -> None:
        """Run the bidirectional screencast loop until stopped or disconnected."""
        self._ws = websocket
        self._running = True

        # Register CDP frame handler
        self._cdp.on("Page.screencastFrame", self._on_frame)

        # Start screencast
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 60,
                "maxWidth": self._viewport_width,
                "maxHeight": self._viewport_height,
            },
        )
        logger.info("[Screencast] started (jpeg q60)")

        try:
            await self._recv_loop()
        except WebSocketDisconnect:
            logger.info("[Screencast] WebSocket disconnected")
        except asyncio.CancelledError:
            logger.info("[Screencast] cancelled")
        except Exception as exc:
            logger.error(f"[Screencast] recv loop error: {exc}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the screencast stream."""
        if not self._running:
            return
        self._running = False
        try:
            await self._cdp.send("Page.stopScreencast", {})
            logger.info("[Screencast] stopped")
        except Exception as exc:
            logger.debug(f"[Screencast] stop error (ignored): {exc}")

    # ── receive loop ────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """Read messages from the WebSocket and dispatch input events."""
        while self._running:
            try:
                raw = await asyncio.wait_for(self._ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a keepalive ping so the connection stays open
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
            elif msg_type == "pong":
                pass  # keepalive response, ignore
            else:
                logger.debug(f"[Screencast] unknown msg type: {msg_type}")

    # ── CDP frame handler ───────────────────────────────────────────

    async def _on_frame(self, params: Dict[str, Any]) -> None:
        """Handle Page.screencastFrame: ack, update viewport, push to WS."""
        session_id = params.get("sessionId")
        if session_id:
            # Ack so CDP keeps sending frames
            try:
                await self._cdp.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            except Exception:
                pass

        # Update viewport from metadata
        metadata = params.get("metadata", {})
        device_w = metadata.get("deviceWidth")
        device_h = metadata.get("deviceHeight")
        if device_w and device_h:
            self._viewport_width = int(device_w)
            self._viewport_height = int(device_h)

        # Push frame to frontend
        if self._ws and self._running:
            frame_msg = {
                "type": "frame",
                "data": params.get("data", ""),
                "metadata": {
                    "width": self._viewport_width,
                    "height": self._viewport_height,
                    "timestamp": metadata.get("timestamp", time.time()),
                },
            }
            try:
                await self._ws.send_json(frame_msg)
            except Exception:
                # WebSocket gone — stop will be called by the recv loop
                self._running = False

    # ── input dispatchers ───────────────────────────────────────────

    async def _dispatch_mouse(self, event: Dict[str, Any]) -> None:
        """Convert normalised coords (0-1) to pixels and send CDP mouse event."""
        x = event.get("x", 0) * self._viewport_width
        y = event.get("y", 0) * self._viewport_height
        button = event.get("button", "left")

        params: Dict[str, Any] = {
            "type": event.get("action", "mouseMoved"),
            "x": x,
            "y": y,
            "button": button if event.get("action") != "mouseMoved" else "none",
            "clickCount": event.get("clickCount", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] mouse dispatch error: {exc}")

    async def _dispatch_key(self, event: Dict[str, Any]) -> None:
        """Send CDP keyboard event."""
        params: Dict[str, Any] = {
            "type": event.get("action", "keyDown"),
            "key": event.get("key", ""),
            "code": event.get("code", ""),
            "text": event.get("text", ""),
            "modifiers": event.get("modifiers", 0),
        }
        # CDP requires windowsVirtualKeyCode for some keys; omit if unknown
        try:
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] key dispatch error: {exc}")

    async def _dispatch_wheel(self, event: Dict[str, Any]) -> None:
        """Send CDP mouseWheel event."""
        x = event.get("x", 0) * self._viewport_width
        y = event.get("y", 0) * self._viewport_height

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
