
import gpiod
from time import monotonic
import asyncio
import os
import math
from logging import debug, info, warning, error
import serial
import json
from collections import deque

# Output GPIO names mapping:
outputs = {
	"contactor": "contactor",
	"water_pump": "water_pump",
	"coolant_pump": "coolant_pump",
	"tvalve_on": "tvalve_on",
	"tvalve_dir": "tvalve_dir",
	"cv_heat_on": "cv_heat_on",
	"fan": "fan",
}

def adc2celsius(adc, R25=50000, BETA=3950):
	if adc <= 1:
		return 0
	R1 = 100000
	Rt = 65535 / adc - 1
	if Rt <= 0:
		return 0
	v = math.log(R1 / Rt / R25) / BETA + 1.0 / 298.15
	if v == 0:
		return 0
	return 1 / v - 273.15

class Filter:
	def __init__(self, n, tmax):
		self.tmax = tmax
		self.queue = deque(maxlen=n)

	def read(self, vin):
		self.queue.append((monotonic(), vin))
		return self.wavg()

	def wavg(self):
		acc = 0
		wacc = 0
		ts0, v0 = self.queue[-1]
		for ts, val in self.queue:
			dt = ts0 - ts
			if dt > self.tmax:
				continue
			w = self.tmax - dt
			acc += w * val
			wacc += w
		if wacc == 0:
			return v0
		return acc / wacc

class Relay:
	def __init__(self, name, default=0):
		self.name = name
		gpioname = outputs.get(name, name)
		l = None
		for chip in gpiod.ChipIter():
			l = chip.find_line(gpioname)
			if l is not None:
				debug(f"Found pin {gpioname} on chip {chip!r}")
				break
		self.gpio = l
		self.gpio.request("kachel")
		if self.gpio.direction() != self.gpio.DIRECTION_OUTPUT:
			self.gpio.set_direction_output(default)

	def set_value(self, v):
		self.gpio.set_value(v)

	def get_value(self):
		return self.gpio.get_value()

class Bidir:
	def __init__(self, relay_on, relay_dir, dwell=10.0, midfrac=0.51):
		self.relay_on = relay_on
		self.relay_dir = relay_dir
		self.dwell = dwell
		# Ensure mid position is between 0.1 and 0.9
		self.midfrac = max(min(midfrac, 0.9), 0.1)
		self.position = None

	def turn_left(self):
		self.relay_dir.set_value(0)
		self.relay_on.set_value(1)

	def turn_right(self):
		self.relay_dir.set_value(1)
		self.relay_on.set_value(1)

	def turn_off(self):
		self.relay_on.set_value(0)
		self.relay_dir.set_value(0)

	def get_status(self):
		if not self.relay_on.get_value():
			return "off"
		if self.relay_dir.get_value():
			return "right"
		return "left"

	def get_position(self):
		return self.position

	async def wait_left(self):
		if self.position == "left":
			return
		self.turn_left()
		await asyncio.sleep(self.dwell)
		self.turn_off()
		self.position = "left"

	async def wait_right(self):
		if self.position == "right":
			return
		self.turn_right()
		await asyncio.sleep(self.dwell)
		self.turn_off()
		self.position = "right"

	async def wait_middle(self):
		if self.position == "middle":
			return
		if self.position == "right":
			await self.wait_left()
		self.turn_right()
		wait = self.midfrac * self.dwell
		await asyncio.sleep(wait)
		self.turn_off()
		self.position = "middle"

	async def nudge_right(self):
		if not self.position == "middle":
			return
		wait = self.dwell * 0.01
		self.turn_right()
		await asyncio.sleep(wait)
		self.turn_off()

	async def nudge_left(self):
		if not self.position == "middle":
			return
		wait = self.dwell * 0.01
		self.turn_left()
		await asyncio.sleep(wait)
		self.turn_off()

class sysfs:
	def __init__(self, path):
		self.path = path

	def sys_write(self, name, value):
		with open(os.path.join(self.path, name), "w") as f:
			f.write(str(value))

	def sys_read(self, name):
		with open(os.path.join(self.path, name), "r") as f:
			return f.read()

	def sys_read_int(self, name):
		return int(self.sys_read(name).strip(" \r\n"))

	def sys_read_float(self, name):
		return float(self.sys_read(name).strip(" \r\n"))

class Counter(sysfs):
	def __init__(self, path):
		super().__init__(path)
		self.running = self.sys_read_int("enable")

	def enable(self):
		self.sys_write("enable", 1)
		self.running = 1

	def disable(self):
		self.sys_write("enable", 0)
		self.running = 0

	def get_value(self):
		return self.sys_read_int("count")

	def is_running(self):
		return self.running == 1

class Frequency:
	def __init__(self, counter):
		self.counter = counter
		self._get_zero()

	def _get_zero(self):
		self.c0 = self.counter.get_value()
		self.t0 = monotonic()

	def start(self):
		self.counter.enable()
		self._get_zero()

	def stop(self):
		self.counter.disable()

	def _get_value(self):
		dc = self.counter.get_value() - self.c0
		dt = monotonic() - self.t0
		if dc < 0:
			self._get_zero()
			return None
		return dc / dt

	def get_value(self, reset=4.5):
		ret = self._get_value()
		if monotonic() - self.t0 > reset:
			self._get_zero()
		return ret

	async def measure(self, min_time=1.0):
		if not self.counter.is_running():
			self.counter.enable()
			disable = True
		else:
			disable = False
		self._get_zero()
		ret = None
		while ret is None:
			await asyncio.sleep(min_time)
			ret = self._get_value()
		if disable:
			self.counter.disable()
		return ret

class FlowRate:
	def __init__(self, freq, fact=6.6):
		self.freq = freq
		self.fact = fact
		self.freq.start()
		self.filter = Filter(10, 5)

	def get_value(self):
		return round(self.filter.read(self.freq.get_value() / self.fact), 2)

class IioAdc(sysfs):
	def __init__(self, path, channel):
		super().__init__(path)
		self.channel = channel
		self.scale = self.sys_read_float(f"in_voltage{self.channel}_scale")

	def get_value(self):
		return self.get_raw() * self.scale / 1000.0

	def get_raw(self):
		return self.sys_read_int(f"in_voltage{self.channel}_raw")

class Temperature:
	def __init__(self, adc, R25=50000, BETA=3950):
		self.R25 = R25
		self.BETA = BETA
		self.adc = adc
		self.filter = Filter(10, 10)

	def get_value(self):
		return round(self.filter.read(adc2celsius(65535 * self.adc.get_raw() / 3300 , R25=self.R25, BETA=self.BETA)), 2)

class SerialJSONSensor:
	def __init__(self, sj, field, scale=1):
		self.sj = sj
		self.field = field
		self.scale = scale

	def get_value(self):
		return self.sj.get_value(self.field, self.scale)

class SerialJSON:
	def __init__(self, port, baud):
		self.s = serial.Serial(port, baud, timeout=0)
		self.loop = None
		self.current = None
		self.rbuf = b''

	def ensure_reader(self):
		if self.loop is not None:
			return
		self.loop = asyncio.get_running_loop()
		self.loop.add_reader(self.s.fileno(), self._handle_read)

	def _handle_read(self):
		l = self.s.read_until()
		if len(l) == 0:
			return
		self.rbuf += l
		if not b'\n' in self.rbuf:
			return
		data, self.rbuf = self.rbuf.split(b'\n',1)
		data = data.strip(b' \r\n')
		try:
			obj = json.loads(data)
		except (json.JSONDecodeError, UnicodeDecodeError):
			info("SerialJSON: Got wrong JSON data: {data!r}")
		else:
			if self.current is None:
				self.current = obj
			else:
				self.current.update(obj)

	def get_value(self, field, scale=1):
		self.ensure_reader()
		if self.current is None:
			return None
		try:
			ret = self.current[field]
		except KeyError:
			ret = None
		return ret * scale

	def get_sensor(self, field, scale=1):
		return SerialJSONSensor(self, field, scale)
