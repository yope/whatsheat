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
import math
from enum import Enum

def dfield(mutable):
	return field(default_factory=lambda :mutable)

@dataclass
class SensorData:
	name: str
	ha_objid: str | None = None
	state: float = 0.0
	ts: float = 0.0
	online: bool = False

	def age(self):
		return time() - self.ts

	def age_online(self):
		if not self.online:
			return time()
		return self.age()

@dataclass
class Sensors:
	power_cv: SensorData = dfield(SensorData("CV mains power", "intergas_power"))
	temp_zone0: SensorData = dfield(SensorData("Temperature Living", "temperature_26"))
	temp_zone1: SensorData = dfield(SensorData("Temperature Zolder", "temperature_11"))
	power_pv: SensorData = dfield(SensorData("PV power output", "solaredge_i1_ac_power"))
	power_wmp: SensorData = dfield(SensorData("WMPower meter power", "wmpower_energy_power"))
	power_wm: SensorData = dfield(SensorData("Whatsminer Power"))
	hashrate_wm: SensorData = dfield(SensorData("Whatsminer Hash-Rate"))
	temp_wm: SensorData = dfield(SensorData("Whatsminer Temperature"))
	temp_in: SensorData = dfield(SensorData("Coolant temperature in"))
	temp_out: SensorData = dfield(SensorData("Coolant temperature out"))
	flowrate_cool: SensorData = dfield(SensorData("Coolant flow rate"))
	temp_tpo: SensorData = dfield(SensorData("Pricom Temperature"))
	setpoint_tpo: SensorData = dfield(SensorData("Pricom Setoint"))

class MinerStates(Enum):
	OFF = 0
	STARTING = 1
	RUNNING = 2
	IDLE = 3
	STOPPING = 4
	STOPPED = 5

class ValuePacer:
	def __init__(self, readfunc, writefunc, min_v, max_v, delta_v, delta_t):
		self.readfunc = readfunc
		self.writefunc = writefunc
		self.delta_v = delta_v
		self.delta_t = delta_t
		self.min_v = min_v
		self.max_v = max_v
		self.t0 = monotonic()
		self.v0 = readfunc()

	def handle(self):
		t1 = monotonic()
		dt = t1 - self.t0
		v1 = self.readfunc()
		if v1 < self.min_v or v1 > self.max_v:
			return
		dv = v1 - self.v0
		if dt > self.delta_t or dv > self.delta_v:
			self.writefunc(v1)
			self.t0 = t1
			self.v0 = v1

class Controller:
	PRICOM_PORT = "/dev/ttyS0"
	TEMP_LIMIT_IDLE = 30
	TEMP_LIMIT_COOL = 45
	TEMP_LIMIT_SOFT_SHUTDOWN = 50
	TEMP_LIMIT_EMERGENCY = 55
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
		self.mqtt_sensor_temp_tpo = self.ha.create_temperature_sensor("sensor_temp_tpo", "Pricom Temperature")
		self.mqtt_sensor_flow_cool = self.ha.create_volume_sensor("sensor_flow_cool", "Coolant flow volume per minute")
		self.mqtt_sensor_co2_tpo = self.ha.create_co2_sensor("sensor_co2_tpo", "Pricom CO2 level")
		self.mqtt_sensor_pressure_tpo = self.ha.create_pressure_sensor("sensor_pressure_tpo", "Pricom Atmospheric Pressure")
		self.mqtt_sensor_illuminance_tpo = self.ha.create_illuminance_sensor("sensor_illuminance_tpo", "Pricom Ambient Light Level")
		self.mqtt_sensor_humidity_tpo = self.ha.create_humidity_sensor("sensor_humidity_tpo", "Pricom RH")
		self.mqtt_sensor_setp_tpo = self.ha.create_temperature_sensor("sensor_temp_sp_tpo", "Pricom Temperature Setpoint")
		self.ha.subscribe("wmpower/deadbeef/whatsminer/SENSOR", self.handle_mqtt_power_wm)
		self.sensors = Sensors()
		self.need_cooling = False
		self.want_main_heat = False
		self.want_aux_heat = False
		self.want_cv_heat = False
		self.commanded_state = MinerStates.OFF
		self.state = MinerStates.OFF
		self.sj = base_io.SerialJSON(self.PRICOM_PORT, 115200)
		self.pricom_temp = self.sj.get_sensor("Temperature", 0.1)
		self.pricom_rh = self.sj.get_sensor("RH", 0.1)
		self.pricom_co2 = self.sj.get_sensor("CO2")
		self.pricom_pressure = self.sj.get_sensor("Pressure")
		self.pricom_amb_light = self.sj.get_sensor("AmbientLight")
		self.pricom_temp_sp = self.sj.get_sensor("TempSetpoint", 0.1)

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
			debug(f"SENSOR: {sensor.name} is Offline!")
		else:
			sensor.state = state
			sensor.ts = ts
			sensor.online = True
			debug(f"SENSOR: {sensor.name} state {state} age {sensor.age()} seconds")

	def handle_mqtt_power_wm(self, obj):
		debug(f"Got MQTT power WM:{obj!r}")
		if "Power" in obj:
			self._setsens(self.sensors.power_wm, obj["Power"])
		else:
			self.sensors.power.online = False
		if "HashRate" in obj:
			self._setsens(self.sensors.hashrate_wm, obj["HashRate"])
		else:
			self.sensors.hashrate_wm.online = False
		if "Temperature" in obj:
			self._setsens(self.sensors.temp_wm, obj["Temperature"])
		else:
			self.sensors.temp_wm.online = False

	def _timeout(self, ts):
		return (ts < monotonic())

	def cv_power_idle(self):
		return (self.sensors.power_cv.state < 5.0)

	async def set_valve_main_circuit(self):
		if self.bidir_valve.get_position() != "left":
			info("VALVE: Moving to main circuit...")
			await self.bidir_valve.wait_left()
			debug("VALVE: Movement finished")

	async def set_valve_aux_circuit(self):
		if self.bidir_valve.get_position() != "right":
			info("VALVE: Moving to aux circuit...")
			await self.bidir_valve.wait_right()
			debug("VALVE: Movement finished")

	async def sensor_updater(self):
		vps = [
			ValuePacer(self.temp_in.get_value, self.mqtt_sensor_temp_in.mqtt_value, 2, 120, 0.2, 10),
			ValuePacer(self.temp_out.get_value, self.mqtt_sensor_temp_out.mqtt_value, 2, 120, 0.2, 10),
			ValuePacer(self.pricom_temp.get_value, self.mqtt_sensor_temp_tpo.mqtt_value, 2, 50, 0.2, 10),
			ValuePacer(self.flow_cool.get_value, self.mqtt_sensor_flow_cool.mqtt_value, 0, 50, 0.2, 10),
			ValuePacer(self.pricom_temp_sp.get_value, self.mqtt_sensor_setp_tpo.mqtt_value, 2, 50, 0.2, 10),
			ValuePacer(self.pricom_rh.get_value, self.mqtt_sensor_humidity_tpo.mqtt_value, 1, 100, 0.2, 10),
			ValuePacer(self.pricom_amb_light.get_value, self.mqtt_sensor_illuminance_tpo.mqtt_value, 0, 10000, 0.2, 10),
			ValuePacer(self.pricom_pressure.get_value, self.mqtt_sensor_pressure_tpo.mqtt_value, 500, 2000, 0.2, 10),
			ValuePacer(self.pricom_co2.get_value, self.mqtt_sensor_co2_tpo.mqtt_value, 300, 10000, 0.2, 10),
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
			self._setsens(self.sensors.flowrate_cool, self.flow_cool.get_value())
			self._setsens(self.sensors.temp_in, self.temp_in.get_value())
			self._setsens(self.sensors.temp_out, self.temp_out.get_value())
			self._setsens(self.sensors.temp_tpo, self.pricom_temp.get_value())
			self._setsens(self.sensors.setpoint_tpo, self.pricom_temp_sp.get_value())

	def get_any_power(self):
		s = self.sensors
		wmage = s.power_wm.age_online()
		wmpage = s.power_wmp.age_online()
		if wmage > 120 and wmpage > 120:
			warning(f"Both {s.power_wm.name} and {s.power_wmp.name} are offline!!")
		if wmage < wmpage:
			return s.power_wm.state
		else:
			return s.power_wmp.state

	def get_highest_temp(self):
		s = self.sensors
		return max(s.temp_in.state, s.temp_out.state, s.temp_wm.state)

	async def miner_control_loop(self):
		s = self.sensors
		# Valve position unknown at startup, put into known position:
		ts = monotonic() + 10
		while not self._timeout(ts):
			if s.power_cv.online:
				break
			await asyncio.sleep(1)
		if self.cv_power_idle():
			await self.set_valve_main_circuit()
		else:
			await self.set_valve_aux_circuit()
		await self.start_main_power()
		while True:
			await asyncio.sleep(3.1415)
			# 1. Check if something needs cooling:
			if s.power_wm.age_online() < 60 and s.power_wm.state > 300:
				self.need_cooling = True
			elif s.hashrate_wm.age_online() < 60 and s.hashrate_wm.state > 0:
				self.need_cooling = True
			elif s.power_wmp.age_online() < 120 and s.power_wmp.state > 200:
				self.need_cooling = True
			elif self.get_highest_temp() > self.TEMP_LIMIT_COOL:
				self.need_cooling = True
			else:
				self.need_cooling = False

			# 2. Decide whether pumps need to be running or not:
			if self.need_cooling:
				self.relay_cool.set_value(1)
				await asyncio.sleep(0.2)
				self.relay_water.set_value(1)
			elif self.get_highest_temp() < self.TEMP_LIMIT_IDLE:
				self.relay_water.set_value(0)
			if not self.need_cooling and self.state == MinerStates.STOPPED:
				self.relay_cool.set_value(0)

			# 3. Decide where to dump heat:
			if self.need_cooling and not self.cv_power_idle():
				# Need to dump heat to liquid to air heat exchanger...
				await self.set_valve_aux_circuit()
				self.relay_fan.set_value(1)
			else:
				await self.set_valve_main_circuit()
				self.relay_fan.set_value(0)

			# 4. Check for soft-shutdown limit:
			miner_ok = True
			t = self.get_highest_temp()
			if t > self.TEMP_LIMIT_SOFT_SHUTDOWN:
				warning(f"Highest temperature is {t} °C! Performing soft shutdown...")
				self.commanded_state = MinerStates.IDLE
				miner_ok = False

			# 5. Check for emergency shutdown limit:
			if t > self.TEMP_LIMIT_EMERGENCY:
				warning(f"Highest temperature is {t} °C! Performing emergency shutdown...")
				self.commanded_state = MinerStates.STOPPED
				miner_ok = False
				await self.emergency_shutdown()

			# 6. Check if we need to start the miner:
			if self.want_main_heat or self.want_aux_heat:
				if miner_ok:
					self.commanded_state = MinerStates.RUNNING
				else:
					warning("Want miner heat, but miner not Ok.")

	async def miner_power_loop(self):
		MS = MinerStates
		cmd0 = self.commanded_state
		ts0 = monotonic() + 10
		# This is intentionally a polling loop, to avoid power chatter due to
		# software bugs or other unforeseen issues. Granularity is 5 seconds.
		while True:
			await asyncio.sleep(5)
			cmd = self.commanded_state
			st = self.state
			if cmd == cmd0 or st == cmd:
				# Nothing to do
				continue

			# Handle transitions from RUNNING to IDLE quickly
			if cmd == MS.IDLE and st == MS.RUNNING:
				await self.miner_idle_command()
				ts0 = monotonic() + 120 # Hold off 2 minutes at least.
				continue

			# All other transitions in here shouldn't be done quickly.
			# Note: Emergency shutdown is handled directly by the control loop.
			if not self._timeout(ts0):
				continue

			if cmd == MS.RUNNING and st == MS.OFF:
				await self.start_main_power()
				ts0 = monotonic() + 60 # Hold off 60 seconds at least.
				continue

			if cmd == MS.RUNNING and st == MS.IDLE:
				await self.miner_mining_command()
				ts0 = monotonic() + 60 # Hold off 60 seconds at least.
				continue

			if cmd == MS.OFF and st == MS.RUNNING:
				await self.miner_idle_command()
				ts0 = monotonic() + 60 # One minute cool down time
				continue

			if cmd == MS.OFF and st == MS.IDLE:
				await self.stop_main_power()
				ts0 = monotonic() + 300 # 5 Minutes cool down time
				continue

	async def miner_idle_command(self):
		print("TODO: miner idle command")
		await asyncio.sleep(1)
		self.state = MinerStates.IDLE

	async def miner_mining_command(self):
		print("TODO: miner mining command")
		await asyncio.sleep(1)
		self.state = MinerStates.RUNNING

	async def soft_shutdown(self):
		print("TODO: Soft shutdown")
		await asyncio.sleep(1)
		self.state = MinerStates.IDLE

	async def emergency_shutdown(self):
		warning("Emergency shutdown: Power OFF!!")
		self.relay_contactor.set_value(0)
		self.state = MinerStates.STOPPING
		await asyncio.sleep(20)
		warning("Emergency shutdown: Water pump off.")
		self.relay_water.set_value(0)
		await asyncio.sleep(20)
		warning("Emergency shutdown: Coolant pump off.")
		self.relay_cool.set_value(0)
		await asyncio.sleep(10)
		warning("Emergency shutdown: Done. Everything stopped.")
		self.state = MinerStates.STOPPED

	async def start_main_power(self):
		if self.relay_contactor.get_value() == 0:
			info("Preparing to start main power...")
			ts0 = monotonic() + 10
			while not self._timeout(ts0):
				await asyncio.sleep(2)
				if not self.sensors.power_cv.online:
					continue
				if not self.sensors.temp_zone1.online:
					continue
				if not self.sensors.temp_zone0.online:
					continue
				if not self.sensors.power_pv.online:
					continue
				if not self.cv_power_idle():
					info("Waiting for CV to stop running.")
					continue
				t = self.get_highest_temp()
				if t > self.TEMP_LIMIT_SOFT_SHUTDOWN:
					warning(f"Temperature too high ({t} °C) while preparing to start! Waiting...")
					continue
				if self.state != MinerStates.OFF:
					warning(f"Trying to start miner while in {self.state.name} state! Waiting...")
					continue
				break
			if self.timeout(ts0):
				warning("Giving up preparing to start main power!")
				return -1
			info("Enable main power...")
			self.relay_contactor.set_value(1)
		else:
			info("Main power already enabled!")
		if self.state == MinerStates.RUNNING:
			info("Miner already in running state!")
			return -2
		elif self.state != MinerStates.OFF:
			warning(f"Miner wanted to start while in {self.state.name} state!")
			return -3
		self.state = MinerStates.STARTING
		info("Waiting for miner to boot...")
		await asyncio.sleep(10)
		while True:
			await asyncio.sleep(2)
			if self.state != MinerStates.STARTING:
				warning(f"Miner state went to {self.state.name} while waiting to boot")
				return -4
			p = self.get_any_power()
			debug(f"Miner power: {p} Watt")
			if p > 500:
				break
		self.state = MinerStates.RUNNING
		info("Miner hashing.")
		return 0

	async def stop_main_power(self):
		if self.relay_contactor.get_value() == 0:
			info("Wanted to stop power, but it was already off.")
			return 0
		if self.state == MinerStates.RUNNING:
			warning("Stopping main power while RUNNING!!")
			await self.miner_idle_command()
			await asyncio.sleep(1)
		self.relay_contactor.set_value(0)
		self.state = MinerStates.OFF

	async def run(self):
		asyncio.create_task(self.sensor_updater())
		asyncio.create_task(self.miner_control_loop())
		asyncio.create_task(self.miner_power_loop())
		await self.ha.run()

def main(args):
	"""
	Usage:
		kachel -h <host>

	Options:
		-h <host>       : Specify hostname/ip-address of miner
		-v              : Verbose mode. More logging output.
		-d              : Enable debug mode. Lot's of logging output!
		--help          : Show this message.

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
