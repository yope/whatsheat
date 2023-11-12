#!/usr/bin/env python3

import asyncio
import base_io
import ha
import os
import sys
import inspect
from time import monotonic

class ValuePacer:
	def __init__(self, readfunc, writefunc, delta_v, delta_t):
		self.readfunc = readfunc
		self.writefunc = writefunc
		self.delta_v = delta_v
		self.delta_t = delta_t
		self.t0 = monotonic()
		self.v0 = readfunc()

	def handle(self):
		t1 = monotonic()
		dt = t1 - self.t0
		v1 = self.readfunc()
		dv = v1 - self.v0
		if dt > self.delta_t or dv > self.delta_v:
			self.writefunc(v1)
			self.t0 = t1
			self.v0 = v1

class Controller:
	def __init__(self, mqtthost):
		mqttuser = os.environ.get("KACHEL_MQTTUSER", None)
		mqttpasswd = os.environ.get("KACHEL_MQTTPASSWD", None)
		self.relay_water = base_io.Relay("water_pump")
		self.relay_cool = base_io.Relay("coolant_pump")
		self.relay_contactor = base_io.Relay("contactor")
		self.relay_cv_heat = base_io.Relay("cv_heat_on")
		self.relay_fan = base_io.Relay("fan")
		self.ha = ha.HomeAssistant(mqtthost, mqttuser, mqttpasswd)
		self.mqtt_switch_water = self.ha.create_switch("switch_water_pump", "Kachel Water Pump", self.relay_water.get_value())
		self.mqtt_switch_water.add_handler(self.mqtt_handle_water_switch)
		self.mqtt_switch_cv_heat = self.ha.create_switch("switch_cv_heat", "Gas Kachel Heat", self.relay_cv_heat.get_value())
		self.mqtt_switch_cv_heat.add_handler(self.mqtt_handle_cv_heat_switch)
		self.mqtt_switch_cool = self.ha.create_switch("switch_coolant_pump", "Kachel Coolant Pump", self.relay_cool.get_value())
		self.mqtt_switch_cool.add_handler(self.mqtt_handle_cool_switch)
		self.mqtt_switch_main = self.ha.create_switch("switch_contactor", "Kachel Main Power", self.relay_contactor.get_value())
		self.mqtt_switch_main.add_handler(self.mqtt_handle_main_switch)
		self.mqtt_switch_fan = self.ha.create_switch("switch_fan", "Kachel Main Power", self.relay_fan.get_value())
		self.mqtt_switch_fan.add_handler(self.mqtt_handle_fan_switch)
		self.bidir_valve = base_io.Bidir(base_io.Relay("tvalve_on"), base_io.Relay("tvalve_dir"))
		counter0 = base_io.Counter("/sys/bus/counter/devices/counter0/count0/")
		counter1 = base_io.Counter("/sys/bus/counter/devices/counter1/count0/")
		self.freq0 = base_io.Frequency(counter0)
		self.freq1 = base_io.Frequency(counter1)
		adcpath = "/sys/bus/iio/devices/iio:device1/"
		self.temp_in = base_io.Temperature(base_io.IioAdc(adcpath, 0), R25=50000)
		self.temp_out = base_io.Temperature(base_io.IioAdc(adcpath, 1), R25=10000)
		self.flow_cool = base_io.FlowRate(self.freq0, 6.6)
		self.mqtt_sensor_temp_in = self.ha.create_temperature_sensor("sensor_temp_in", "Coolant inlet temperature")
		self.mqtt_sensor_temp_out = self.ha.create_temperature_sensor("sensor_temp_out", "Coolant outlet temperature")
		self.mqtt_sensor_flow_cool = self.ha.create_volume_sensor("sensor_flow_cool", "Coolant flow volume per minute")

	def mqtt_handle_main_switch(self, state):
		self.relay_contactor.set_value(state)

	def mqtt_handle_water_switch(self, state):
		self.relay_water.set_value(state)

	def mqtt_handle_cool_switch(self, state):
		self.relay_cool.set_value(state)

	def mqtt_handle_cv_heat_switch(self, state):
		self.relay_cv_heat.set_value(state)

	def mqtt_handle_fan_switch(self, state):
		self.relay_fan.set_value(state)

	async def sensor_updater(self):
		vps = [
			ValuePacer(self.temp_in.get_value, self.mqtt_sensor_temp_in.mqtt_value, 0.2, 10),
			ValuePacer(self.temp_out.get_value, self.mqtt_sensor_temp_out.mqtt_value, 0.2, 10),
			ValuePacer(self.flow_cool.get_value, self.mqtt_sensor_flow_cool.mqtt_value, 0.2, 10),
		]
		while True:
			await asyncio.sleep(1)
			for vp in vps:
				vp.handle()

	async def run(self):
		asyncio.create_task(self.sensor_updater())
		await self.ha.run()

def main(args):
	"""
	Usage:
		kachel -h <host>

	Options:
		-h <host>       : Specify hostname/ip-address of miner

	Environment Variables to avoid leaking credentials to the command line:
		KACHEL_PASSWD  : Admin password.
		KACHEL_MQTTUSER, KACHEL_MQTTPASSWD : Likewise for MQTT broker access.
	"""
	mqtthost = None
	while args:
		a = args.pop(0)
		if a == "-h":
			mqtthost = args.pop(0)
		elif a == "--help":
			print(inspect.cleandoc(main.__doc__))
			return 0
	if mqtthost is None:
		print("ERROR: Use --help for usage.")
		return -1
	c = Controller(mqtthost)
	asyncio.run(c.run())

if __name__ == "__main__":
	main(sys.argv[1:])
