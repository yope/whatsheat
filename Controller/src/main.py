from machine import Pin, I2C, ADC
from ssd1306 import SSD1306_I2C
import framebuf
import uasyncio as asyncio
import sys
import math

def adc2celsius(adc):
	return (1 / (math.log(1/(65535/adc - 1))/3950 + 1/298.15) - 273.15)

class UI:
	maxlines = 4
	debounce = 0.1
	def __init__(self):
		self.lines = ["" for i in range(self.maxlines)]
		self.oled = SSD1306_I2C(128, self.maxlines * 8, I2C(0))

	async def coro_init(self, loop):
		self.loop = loop
		self.text("Hello world", 0)
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
		print(f"Key UP {'pressed' if press else 'released'}")

	def key_down(self, press):
		print(f"Key Down {'pressed' if press else 'released'}")

	def key_ok(self, press):
		print(f"Key OK {'pressed' if press else 'released'}")

	def key_cancel(self, press):
		print(f"Key Cancel {'pressed' if press else 'released'}")

	def text(self, s, n=0, redraw=True):
		self.lines[n] = s
		if redraw:
			self.redraw()

	def clear(self):
		self.oled.fill(0)

	def redraw(self):
		self.clear()
		for i in range(self.maxlines):
			try:
				self.oled.text(self.lines[i], 0, i * 8)
			except IndexError:
				break
		self.oled.show()

class Application:
	def __init__(self):
		self.ui = UI()

	def handle_exception(self, loop, ctx):
		sys.print_exception(ctx["exception"])
		sys.exit()

	async def main(self):
		loop = asyncio.get_event_loop()
		loop.set_exception_handler(self.handle_exception)
		await self.ui.coro_init(loop)
		cnt = 0
		adc = ADC(27)
		acc = 0
		while True:
			acc += adc2celsius(adc.read_u16())
			cnt += 1
			if cnt >= 10:
				temp = acc / cnt
				self.ui.text(f"Count {cnt}", 1, redraw=False)
				self.ui.text(f"Temp: {temp:4.1f}", 2)
				cnt = acc = 0
			await asyncio.sleep(0.1)

	def run(self):
		try:
			asyncio.run(self.main())
		finally:
			asyncio.new_event_loop()

if __name__ == "__main__":
	app = Application()
	app.run()
