
from logging import debug, info, warning, error
from aiohttp import web, WSMsgType
import pathlib
import json
import inspect
import dataclasses
import enum
import base_io
import asyncio

HTML_ROOT = pathlib.Path(__file__).parent.parent / 'html'

class WsHandler:
	def __init__(self, ws, server, ctrl):
		self.ws = ws
		self.server = server
		self.ctrl = ctrl

	async def run(self):
		async for msg in self.ws:
			if msg.type == WSMsgType.TEXT:
				await self.handle_command(msg.data)
			elif msg.type == WSMsgType.ERROR:
				error(f'WS closed with exception {self.ws.exception()!r}')
		return

	async def handle_command(self, cmd):
		try:
			obj = json.loads(cmd)
		except json.JSONDecodeError:
			warning(f"WS: unrecognize command: {cmd!r}")
			return
		cmd = obj["command"]
		args = obj.get("args", [])
		kwargs = obj.get("kwargs", {})
		seq = obj.get("sequence", 0)
		try:
			meth = getattr(self, "do_" + cmd)
		except AttributeError:
			warning(f"WS: Unimplemented command: {cmd}")
			ret = None
		else:
			ret = meth(*args, **kwargs)
			if inspect.isawaitable(ret):
				ret = await ret
		resp = {
			"type": "response",
			"command": cmd,
			"sequence": seq,
			"return": ret
		}
		await self.ws.send_json(resp)

	def _member_filter(self, val):
		if isinstance(val, bool):
			return True
		if dataclasses.is_dataclass(val):
			return True
		if isinstance(val, enum.Enum):
			return True
		if isinstance(val, base_io.Relay):
			return True
		if isinstance(val, base_io.Bidir):
			return True
		return False

	def _member_translate(self, val):
		if dataclasses.is_dataclass(val):
			return dataclasses.asdict(val)
		if isinstance(val, enum.Enum):
			return val.name
		if isinstance(val, base_io.Relay):
			return {"class": "Relay", "value": val.get_value()}
		if isinstance(val, base_io.Bidir):
			return {"class": "Bidir", "position": val.get_position(), "status": val.get_status()}
		return val

	def do_get(self, item=None):
		data = {}
		if item is None:
			for key, val in inspect.getmembers_static(self.ctrl, self._member_filter):
				if key.startswith("_"):
					continue
				data[key] = self._member_translate(val)
		else:
			data[item] = self._member_translate(getattr(self.ctrl, item))
		return data

	def do_click(self, elem, arg=None):
		if elem == "manual_override":
			return self.ctrl.set_manual_override(not self.ctrl.manual_override)
		if elem == "enable_power_control":
			return self.ctrl.set_enable_power_control()
		if elem == "nudge_valve_aux":
			asyncio.create_task(self.ctrl.nudge_valve_aux_circuit())
			return True
		if elem == "nudge_valve_main":
			asyncio.create_task(self.ctrl.nudge_valve_main_circuit())
			return True
		if not self.ctrl.manual_override:
			warning(f"Button {elem!r} clicked, but manual override is OFF")
			return False
		if hasattr(self.ctrl, elem):
			r = getattr(self.ctrl, elem)
			if isinstance(r, base_io.Relay):
				info(f"UI: click relay {elem!r}")
				r.set_value(not r.get_value())
			elif isinstance(r, base_io.Bidir):
				st = r.get_status()
				p = r.get_position()
				info(f"UI: click bidir {elem!r} arg: {arg!r}, status: {st}, pos: {p}")
				if arg == "toggle":
					if st == "off" and p in (None, "right", "middle"):
						coro = r.wait_left()
					elif st == "off" and p == "left":
						coro = r.wait_right()
					else:
						coro = None
				elif arg == "middle":
					if st == "off" and p != "middle":
						coro = r.wait_middle()
					else:
						coro = None
				else:
					coro = None
				if coro is not None:
					asyncio.create_task(coro)
		return True

class Server:
	def __init__(self, ctrl):
		self.ctrl = ctrl
		self.app = web.Application()
		self.app.add_routes([
				web.static('/html', HTML_ROOT),
				web.get('/ws', self.websocket_handler),
			])

	async def startup(self):
		runner = web.AppRunner(self.app)
		await runner.setup()
		site = web.TCPSite(runner, None, 8080)
		await site.start()
		info("Web server started")

	async def websocket_handler(self, req):
		ws = web.WebSocketResponse()
		await ws.prepare(req)
		info("WS opened")
		client = WsHandler(ws, self, self.ctrl)
		await client.run()
		info("WS closed")
		return ws
