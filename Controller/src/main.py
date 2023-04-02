
from mqtt_as import MQTTClient
from mqtt_local import config
from machine import Pin, I2C, ADC
import rp2
from ssd1306 import SSD1306_I2C
import framebuf
import uasyncio as asyncio
import sys
import math
from time import ticks_ms
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

@rp2.asm_pio()
def PIO_counter():
	set(x, 0)
	wrap_target()
	label('inner')
	wait(1, pin, 0)
	wait(0, pin, 0)
	jmp(x_dec, 'inner')
	wrap()


class FreqCounter:
	def __init__(self, npin):
		self.sm = rp2.StateMachine(0)
		self.sm.init(PIO_counter, freq=125000000, in_base=Pin(npin, Pin.IN, Pin.PULL_UP))
		self.sm.active(1)
		self.ts0 = ticks_ms()
		self.count0 = 0

	def read(self):
		self.sm.exec('mov(isr, x)')
		self.sm.exec('push()')
		count = (0x100000000 - self.sm.get()) & 0xffffffff
		ts = ticks_ms()
		dt = ts - self.ts0
		dc = count - self.count0
		self.ts0 = ts
		self.count0 = count
		if dc < 0 or dt <= 0:
			return 0 # Invalid, retry
		return (dc * 1000) / dt

class UI:
	maxlines = 4
	debounce = 0.1
	def __init__(self):
		self.lines = ["" for i in range(self.maxlines)]
		self.oled = SSD1306_I2C(128, self.maxlines * 8, I2C(0))
		self.clear_buttons()

	def clear_buttons(self):
		self.btns = []
		self.btn_selected = None

	async def coro_init(self, loop):
		self.loop = loop
		self.redraw()
		self.loop.create_task(self.coro_keys())
		print("Init")

	async def coro_keys(self):
		keys = (
			Pin(12, Pin.IN, Pin.PULL_UP),
			Pin(15, Pin.IN, Pin.PULL_UP),
			Pin(19, Pin.IN, Pin.PULL_UP),
			Pin(16, Pin.IN, Pin.PULL_UP)
		)
		values = list(False for i in range(len(keys)))
		handlers = (
			self.key_up,
			self.key_down,
			self.key_ok,
			self.key_cancel
		)
		while True:
			new = list(not x.value() for x in keys)
			for i in range(len(keys)):
				if new[i] and not values[i]:
					handlers[i](True)
				if values[i] and not new[i]:
					handlers[i](False)
			values = new
			await asyncio.sleep(self.debounce)

	def key_up(self, press):
		#print(f"Key UP {'pressed' if press else 'released'}")
		if self.btn_selected and press:
			self.btn_selected = max(0, self.btn_selected - 1)
			self.redraw()

	def key_down(self, press):
		#print(f"Key Down {'pressed' if press else 'released'}")
		if self.btn_selected is not None and press:
			self.btn_selected = min(self.btn_selected + 1, len(self.btns) - 1)
			self.redraw()

	def key_ok(self, press):
		#print(f"Key OK {'pressed' if press else 'released'}")
		if self.btn_selected is None or not press:
			return
		h = self.btns[self.btn_selected][3]
		if h is not None:
			h()

	def key_cancel(self, press):
		print(f"Key Cancel {'pressed' if press else 'released'}")

	def text(self, s, n=0, redraw=True):
		self.lines[n] = s
		if redraw:
			self.redraw()

	def button(self, x, y, s, handler):
		self.btns.append([x, y, s, handler])
		return len(self.btns) - 1

	def set_button_text(self, idx, s):
		self.btns[idx][2] = s

	def clear(self):
		self.oled.fill(0)

	def redraw(self):
		self.clear()
		for i in range(self.maxlines):
			try:
				self.oled.text(self.lines[i], 0, i * 8)
			except IndexError:
				break
		for i, b in enumerate(self.btns):
			x, y, s, _ = b
			w = 8 * (len(s) + 1)
			h = 8
			self.oled.rect(x, y, w, h, 1, True)
			self.oled.text(s, x + 4, y, 0)
			if self.btn_selected == i:
				for i in range(3):
					self.oled.vline(x + i + 1, y + i + 1, 6 - i * 2, 0)
					self.oled.vline(x + w - i - 2, y + i + 1, 6 - i * 2, 0)
		self.oled.show()

class Application:
	def __init__(self):
		self.ui = UI()
		self.fc = FreqCounter(2)
		config["queue_len"] = 1 # MQTT Event interface
		self.mqtt = MQTTClient(config)
		self.temp_df = 0
		self.temp_w = 0
		self.flow_df = 0
		self.pump_df_on = False
		self.pump_w_on = False

	def screen_main_setup(self):
		self.ui.button(8, 0, "start", self.btn_start)
		self.ui.button(80, 0, "stop", self.btn_stop)
		self.ui.button(96, 8, "off", self.btn_pump_df)
		self.ui.button(96, 16, "off", self.btn_pump_w)
		self.ui.button(104, 24, "->", self.btn_next)
		self.ui.btn_selected = 0

	def screen_main_redraw(self):
		self.ui.text(f"Df: {self.temp_df:4.1f} C", 1, False)
		self.ui.text(f"W:  {self.temp_w:4.1f} C", 2, False)
		self.ui.text(f"fr: {self.flow_df:4.1f} lpm", 3)

	def btn_start(self):
		print("Start system")

	def btn_stop(self):
		print("Stop system")

	def btn_pump_df(self):
		self.pump_df_on = not self.pump_df_on
		self._set_btn_on_off(2, self.pump_df_on)

	def btn_pump_w(self):
		self.pump_w_on = not self.pump_w_on
		self._set_btn_on_off(3, self.pump_w_on)

	def btn_next(self):
		print("Next screen")

	def _set_btn_on_off(self, idx, val):
		s = "on " if val else "off"
		self.ui.set_button_text(idx, s)
		self.ui.redraw()

	def handle_exception(self, loop, ctx):
		sys.print_exception(ctx["exception"])
		sys.exit()

	async def main(self):
		loop = asyncio.get_event_loop()
		loop.set_exception_handler(self.handle_exception)
		await self.ui.coro_init(loop)
		print("Connecting to WiFi and broker...")
		await self.mqtt.connect()
		asyncio.create_task(self.mqtt_up())
		asyncio.create_task(self.mqtt_down())
		asyncio.create_task(self.mqtt_messages())
		cnt = 0
		adc = ADC(27)
		acc = 0
		flow = 0.0
		self.screen_main_setup()
		while True:
			acc += adc2celsius(adc.read_u16())
			cnt += 1
			if cnt >= 10:
				self.temp_df = acc / cnt
				self.flow_df = self.fc.read() / 6.6
				cnt = acc = 0
				self.screen_main_redraw()
			await asyncio.sleep(0.1)

	async def mqtt_up(self):
		while True:
			await self.mqtt.up.wait()
			self.mqtt.up.clear()
			print('Connected to broker. Subscribing...')
			await self.mqtt.subscribe("wmpower/#", 1)

	async def mqtt_down(self):
		while True:
			await self.mqtt.down.wait()
			self.mqtt.down.clear()
			print('Connection to broker failed')

	async def mqtt_messages(self):
		async for topic, msg, retained in self.mqtt.queue:
			print(f"MQTT topic: {topic.decode()} message: {msg.decode()}")

	def run(self):
		try:
			asyncio.run(self.main())
		finally:
			self.mqtt.close()
			asyncio.new_event_loop()

if __name__ == "__main__":
	app = Application()
	app.run()
