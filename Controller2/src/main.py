#!/usr/bin/env python3

from logging import debug, info, warning, error
import logging
import asyncio
import base_io
import ha
import os
import sys
import inspect
from time import monotonic, time
from dataclasses import dataclass, field, asdict
from pprint import pformat

def dfield(mutable):
	return field(default_factory=lambda :mutable)

@dataclass
class SensorData:
	name: str
	ha_objid: str | None = None
	state: float = 0.0
	ts: float = 0.0
	online: bool = False

@dataclass
class Sensors:
	cvpower: SensorData = dfield(SensorData("CV mains power", "intergas_power"))
	tzone0: SensorData = dfield(SensorData("Temperature Living", "temperature_26"))
	tzone1: SensorData = dfield(SensorData("Temperature Zolder", "temperature_11"))
	pvpower: SensorData = dfield(SensorData("PV power output", "solaredge_i1_ac_power"))
	wmpower: SensorData = dfield(SensorData("Whatsminer Power"))
	hashrate: SensorData = dfield(SensorData("Whatsminer Hash-Rate"))
	wmtemp: SensorData = dfield(SensorData("Whatsminer Temperature"))

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
		self.ha.subscribe("wmpower/deadbeef/whatsminer/SENSOR", self.handle_mqtt_wmpower)
		self.sensors = Sensors()

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

	def _setsens(self, sensor, state, ts=None):
		if ts is None:
			ts = time()
		try:
			state = float(state)
		except ValueError:
			sensor.online = False
		else:
			sensor.state = state
			sensor.ts = ts
			sensor.online = True

	def handle_mqtt_wmpower(self, obj):
		if "Power" in obj:
			self._setsens(self.sensors.wmpower, obj["Power"])
		else:
			self.sensors.power.online = False
		if "HashRate" in obj:
			self._setsens(self.sensors.hashrate, obj["HashRate"])
		else:
			self.sensors.hashrate.online = False
		if "Temperature" in obj:
			self._setsens(self.sensors.wmtemp, obj["Temperature"])
		else:
			self.sensors.wmtemp.online = False

	def _timeout(self, ts):
		return (ts < monotonic())

	def cv_power_idle(self):
		return (self.sensors.cvpower.state < 5.0)

	async def set_valve_main_circuit(self):
		debug("VALVE: Moving to main circuit...")
		await self.bidir_valve.wait_left()
		debug("VALVE: Movement finished")

	async def set_valve_zolder_circuit(self):
		debug("VALVE: Moving to zolder circuit...")
		await self.bidir_valve.wait_right()
		debug("VALVE: Movement finished")

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
			for s in self.sensors.__dict__:
				sensor = getattr(self.sensors, s)
				if sensor.ha_objid is not None:
					state, ts = await self.ha.get_sensor_state_and_timestamp(sensor.ha_objid)
					if state is not None:
						self._setsens(sensor, state, ts)
					else:
						sensor.online = False

	async def control_loop(self):
		await self.set_valve_main_circuit()
		await self.start_main_power()
		while True:
			await asyncio.sleep(3.1415)
			igage = time() - self.sensors.cvpower.ts
			print(f"Intergas power: {round(self.sensors.cvpower.state, 2)}W Updated {round(igage, 2)} seconds ago")

	async def start_main_power(self):
		info("Preparing to start main power...")
		ts0 = monotonic() + 10
		while not self._timeout(ts0):
			await asyncio.sleep(1)
			if not self.sensors.cvpower.online:
				continue
			if not self.sensors.tzone1.online:
				continue
			if not self.sensors.tzone0.online:
				continue
			if not self.sensors.pvpower.online:
				continue
			if not self.cv_power_idle():
				info("Waiting for CV to stop running.")
			break
		info("Enable main power...")
		self.relay_contactor.set_value(1)
		info("Waiting for miner to boot...")
		await asyncio.sleep(10)
		while True:
			await asyncio.sleep(2)
			debug(f"WM power: {self.sensors.wmpower.state}")
			if self.sensors.wmpower.state > 500.0:
				break
		info("Miner hashing.")
		return 0

	async def run(self):
		asyncio.create_task(self.sensor_updater())
		asyncio.create_task(self.control_loop())
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
	debug = False
	verbose = False
	while args:
		a = args.pop(0)
		if a == "-h":
			mqtthost = args.pop(0)
		elif a == "-v":
			verbose = True
		elif a == "-d":
			debug = True
		elif a == "--help":
			print(inspect.cleandoc(main.__doc__))
			return 0
	if debug:
		loglevel = logging.DEBUG
	elif verbose:
		loglevel = logging.INFO
	else:
		loglevel = logging.WARNING
	logging.basicConfig(level=loglevel)
	if mqtthost is None:
		print("ERROR: Use --help for usage.")
		return -1
	c = Controller(mqtthost)
	asyncio.run(c.run())

if __name__ == "__main__":
	main(sys.argv[1:])
