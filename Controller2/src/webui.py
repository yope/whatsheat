
from logging import debug, info, warning, error
from aiohttp import web, WSMsgType
import pathlib
import json
import inspect
import dataclasses
import enum

HTML_ROOT = pathlib.Path(__file__).parent / 'html'

class WsHandler:
	def __init__(self, ws, server, ctrl):
		self.ws = ws
		self.server = server
		self.ctrl = ctrl

	async def run(self):
		async for msg in welf.ws:
			if msg.type == WSMsgType.TEXT:
				await self.handle_command(msg.data)
			elif msg.type == WSMsgType.ERROR:
				error(f'WS closed with exception {self.ws.exception()!r}')
		return

	async def handle_command(self, cmd):
		try:
			obj = json.loads(cmd)
		except json.JSONDecodeError:
			warn(f"WS: unrecognize command: {cmd!r}")
			return
		cmd = obj["command"]
		args = obj.get("args", [])
		kwargs = obj.get("kwargs", {})
		try:
			meth = getattr(self, "do_" + cmd)
		except AttributeError:
			warn(f"WS: Unimplemented command: {cmd}")
		meth(args)
		ret = meth(*args, **kwargs)
			await ret

	def _member_filter(self, val):
		if isinatance(val, bool):
			return True
		if dataclasses.is_dataclass(val):
			return True
		if isinstance(val, enum.EnumType):
			return True
		return False

	def _member_translate(self, val):
		if dataclasses.is_dataclass(val):
			return dataclasses.asdict(val)
		if isinstance(val, enum.EnumType):
			return val.value
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
		self.ws.send(json.dumps(data))

class Server:
	def __init__(self, ctrl):
		self.ctrl = ctr
		self.app = web.Application()
		self.app.add_routes([
				web.static('/html', HTML_ROOT),
				web.get('/ws', self.websocket_handler),
			])

	async def websocket_handler(self, req):
		ws = web.WebSocketResponse()
		await ws.prepare(req)
		info("WS opened")
		client = WsHandler(ws, self, self.ctrl)
		await client.run()
		info("WS closed")
		return ws
