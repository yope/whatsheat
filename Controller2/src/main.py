#!/usr/bin/python3

# import setproctitle
from logging import debug, info, warning, error
import logging
from logging.handlers import SysLogHandler
import asyncio
import base_io
import ha
import os
import sys
import inspect
from time import monotonic, time, localtime
from dataclasses import dataclass, field, asdict
from pprint import pformat
import math
from enum import Enum
from webui import Server

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

	def get_value(self):
		return self.state

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
	setpoint_aux: SensorData = dfield(SensorData("Zolder Setoint"))

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
		if self.v0 is None:
			self.v0 = 0

	def handle(self):
		t1 = monotonic()
		dt = t1 - self.t0
		v1 = self.readfunc()
		if v1 is None:
			return
		if v1 < self.min_v or v1 > self.max_v:
			return
		dv = abs(v1 - self.v0)
		if dt > self.delta_t or dv > self.delta_v:
			self.writefunc(v1)
			self.t0 = t1
			self.v0 = v1

class Controller:
	PRICOM_PORT = "/dev/ttyS0"
	TEMP_LIMIT_IDLE = 30
	TEMP_LIMIT_COOL = 40
	TEMP_LIMIT_SOFT_SHUTDOWN = 57
	TEMP_LIMIT_EMERGENCY = 62
	TEMP_AUX_SWITCH_HIGH = 48
	TEMP_AUX_SWITCH_HYST = 7
	DELTA_TEMP_CV = 1
	MAX_ON_TIME_CV = 20*60
	MIN_OFF_TIME_CV = 10*60
	MIN_ON_TIME_CV = 2*60
	MINING_MQTT_TOPIC = "wmpower/kachel/whatsminer/mining"
	def __init__(self, mqtthost, manual_override):
		mqttuser = os.environ.get("KACHEL_MQTTUSER", None)
		mqttpasswd = os.environ.get("KACHEL_MQTTPASSWD", None)
		self.relay_water = base_io.Relay("water_pump")
		self.relay_cool = base_io.Relay("coolant_pump")
		self.relay_contactor = base_io.Relay("contactor")
		self.relay_cv_heat = base_io.Relay("cv_heat_on")
		self.relay_fan = base_io.Relay("fan")
		self.setpoint_main = 16.0
		self.setpoint_aux = 16.0
		self.best_main_temp = 0
		self.best_aux_temp = 0
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
		self.temp_in = base_io.Temperature(base_io.IioAdc(adcpath, 2), R25=50000, BETA=3850)
		self.temp_out = base_io.Temperature(base_io.IioAdc(adcpath, 3), R25=10000, BETA=4050)
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
		self.mqtt_number_setp_aux = self.ha.create_temperature_setpoint("number_temp_sp_aux", "Setpoint Temperature Zolder", self.setpoint_aux)
		self.mqtt_number_setp_aux.add_handler(self.mqtt_handle_number_setp_aux)
		self.ha.subscribe("wmpower/deadbeef/whatsminer/SENSOR", self.handle_mqtt_power_wm)
		self.sensors = Sensors()
		self.need_cooling = False
		self.want_main_heat = False
		self.want_aux_heat = False
		self.want_cv_heat = False
		self.prefer_aux = False
		self.manual_override = False
		self.set_manual_override(manual_override)
		self.can_cool = False
		self.manual_override_ts = 0
		self.miner_ok = True
		self.commanded_state = MinerStates.OFF
		self.state = MinerStates.OFF
		self.sj = base_io.SerialJSON(self.PRICOM_PORT, 115200)
		self.pricom_temp = self.sj.get_sensor("Temperature", 0.1)
		self.pricom_rh = self.sj.get_sensor("RH", 0.1)
		self.pricom_co2 = self.sj.get_sensor("CO2")
		self.pricom_pressure = self.sj.get_sensor("Pressure")
		self.pricom_amb_light = self.sj.get_sensor("AmbientLight")
		self.pricom_temp_sp = self.sj.get_sensor("TempSetpoint", 0.1)
		self.webserver = Server(self)

	def set_manual_override(self, val):
		if val and not self.manual_override:
			warning("Manual override activated!")
			self.manual_override_ts = monotonic()
		self.manual_override = val
		return True

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

	def mqtt_handle_number_setp_aux(self, val):
		self.setpoint_aux = val
		self._setsens(self.sensors.setpoint_aux, val)

	def _setsens(self, sensor, state, ts=None):
		if ts is None:
			ts = time()
		try:
			state = float(state)
		except TypeError:
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

	def cv_power_heat(self):
		return (self.sensors.power_cv.state > 68)

	def cv_power_water(self):
		return (not self.cv_power_idle() and not self.cv_power_heat())

	def can_dump_aux(self):
		if self.is_night_time():
			return False
		tlim = self.TEMP_AUX_SWITCH_HIGH
		if not self.is_valve_aux_active():
			tlim -= self.TEMP_AUX_SWITCH_HYST
		return (self.get_best_miner_temp() < tlim)

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

	async def set_valve_middle(self):
		if self.bidir_valve.get_position() != "middle":
			info("VALVE: Moving to mid position...")
			await self.bidir_valve.wait_middle()
			debug("VALVE: Movement finished")

	def is_valve_aux_active(self):
		vs = self.bidir_valve.get_status()
		vp = self.bidir_valve.get_position()
		return (vs == "right") or (vp in ("right", "middle"))

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
			ValuePacer(self.sensors.setpoint_aux.get_value, self.mqtt_number_setp_aux.mqtt_value, 2, 40, 0.2, 10),
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

	def get_best_miner_temp(self):
		s = self.sensors
		temp = s.temp_wm.state
		if s.temp_wm.age_online() < 15.0 and temp > 15.0:
			return temp
		temp = s.temp_out.state
		if s.temp_out.age_online() < 20.0 and temp > 15.0:
			return temp
		return max(s.temp_in.state, s.temp_out.state, s.temp_wm.state)

	def dump_heat(self, nc, wmh, wah, cvpw, cda):
		if cvpw and cda:
			# If CV is heating water and we can dump on aux, we must always do so.
			return "aux"
		if not nc:
			# No cooling required, miner off
			return "main" # FMIXE!!
		if cvpw and not cda:
			# If CV is heating water but we can't dump to aux, we must always stop.
			return "off"
		if not cda:
			# If we can't dump to AUX, then select main:
			return "main"
		# Can dump to aux, but should we?
		if not wah:
			return "main"
		if wah and wmh:
			return "middle" if self.prefer_aux else "main"
		if wah:
			return "aux"
		# Default is always "main", although we shouldn't get here.
		return "main"

	async def miner_control_loop(self):
		s = self.sensors
		# Valve position unknown at startup, put into known position:
		ts = monotonic() + 10
		while not self._timeout(ts):
			if s.power_cv.online:
				break
			await asyncio.sleep(1)
		if not self.manual_override:
			if self.cv_power_idle():
				await self.set_valve_main_circuit()
			else:
				await self.set_valve_aux_circuit()
		fants = monotonic()
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

			# If manual override is active, don't proceed.
			if self.manual_override:
				continue

			# 2. Decide where to dump heat:
			heatdest = self.dump_heat(self.need_cooling, self.want_main_heat, self.want_aux_heat,
									self.cv_power_water(), self.can_dump_aux())
			if heatdest == "main":
				await self.set_valve_main_circuit()
				self.can_cool = True
			elif heatdest == "aux":
				await self.set_valve_aux_circuit()
				self.can_cool = True
			elif heatdest == "middle":
				await self.set_valve_middle()
				self.can_cool = True
			else:
				info("Need cooling, but cannot dump heat. Pausing...")
				self.can_cool = False

			# 3. Decide whether pumps need to be running or not:
			if self.need_cooling and self.can_cool:
				# Easy, both pumps on.
				self.relay_cool.set_value(1)
				await asyncio.sleep(0.2)
				self.relay_water.set_value(1)
			else:
				# Handle water pump
				if not self.can_cool and not self.is_valve_aux_active():
					# If cooling isn't possible on main circuit, water pump must be off!
					self.relay_water.set_value(0)
				elif self.state == MinerStates.STOPPED and self.get_highest_temp() < self.TEMP_LIMIT_IDLE:
					self.relay_water.set_value(0)
				elif not self.need_cooling and self.cv_power_water():
					self.relay_water.set_value(0)
				else:
					self.relay_water.set_value(1)
				# Handle coolant pump, always on except if miner off and fully cooled down.
				if self.state == MinerStates.STOPPED and self.get_highest_temp() < self.TEMP_LIMIT_IDLE:
					self.relay_cool.set_value(0)
				else:
					self.relay_cool.set_value(1)

			# 4. Decide whether the fan must be running:
			if self.need_cooling and self.is_valve_aux_active():
				self.relay_fan.set_value(1)
				fants = monotonic() + 80
			elif self._timeout(fants):
				self.relay_fan.set_value(0)

			# 5. Check if we want heat but cannot dump it anywhere
			if not self.can_cool:
				self.commanded_state = MinerStates.IDLE

			# 6. Check for soft-shutdown limit:
			miner_ok = self.miner_ok
			t = self.get_highest_temp()
			if t > self.TEMP_LIMIT_SOFT_SHUTDOWN:
				warning(f"Highest temperature is {t} °C! Performing soft shutdown...")
				self.commanded_state = MinerStates.IDLE
				miner_ok = False

			# 7. Check for emergency shutdown limit:
			if t > self.TEMP_LIMIT_EMERGENCY:
				warning(f"Highest temperature is {t} °C! Performing emergency shutdown...")
				self.commanded_state = MinerStates.STOPPED
				miner_ok = False
				await self.emergency_shutdown()

			# 8. Check if we need to start the miner. Wait for hot water demand to
			# stop if it is active.
			if (self.want_main_heat or self.want_aux_heat) and self.can_cool and not self.cv_power_water():
				if miner_ok:
					self.commanded_state = MinerStates.RUNNING
				elif self.miner_ok:
					# Old state was ok, new state not ok, complain once.
					warning("Want miner heat, but miner not Ok.")
			# Store new state
			self.miner_ok = miner_ok

	async def miner_power_loop(self):
		MS = MinerStates
		cmd0 = self.commanded_state
		ts0 = monotonic() + 10
		# This is intentionally a polling loop, to avoid power chatter due to
		# software bugs or other unforeseen issues. Granularity is 5 seconds.
		while True:
			await asyncio.sleep(5)

			# Check for state inconsistencies, if miner is surprisingly running:
			if self.sensors.power_wm.state > 500 and self.state == MS.OFF:
				self.state = MS.RUNNING

			if self.manual_override:
				continue

			cmd = self.commanded_state
			st = self.state
			if cmd == cmd0 or st == cmd:
				# Nothing to do
				continue

			# Handle transitions from RUNNING to IDLE quickly
			if cmd == MS.IDLE and st == MS.RUNNING:
				await self.miner_idle_command()
				ts0 = monotonic() + 120 # Hold off 2 minutes at least.
				cmd0 = cmd
				continue

			# All other transitions in here shouldn't be done quickly.
			# Note: Emergency shutdown is handled directly by the control loop.
			if not self._timeout(ts0):
				continue

			if cmd == MS.RUNNING and st == MS.OFF:
				await self.start_main_power()
				ts0 = monotonic() + 60 # Hold off 60 seconds at least.
				cmd0 = cmd
				continue

			if cmd == MS.RUNNING and st == MS.IDLE:
				await self.miner_mining_command()
				ts0 = monotonic() + 60 # Hold off 60 seconds at least.
				cmd0 = cmd
				continue

			if cmd == MS.OFF and st == MS.RUNNING:
				await self.miner_idle_command()
				ts0 = monotonic() + 60 # One minute cool down time
				cmd0 = cmd
				continue

			if cmd == MS.OFF and st == MS.IDLE:
				await self.stop_main_power()
				ts0 = monotonic() + 300 # 5 Minutes cool down time
				cmd0 = cmd
				continue

	def _th_on(self, sp, v, hyst):
		return v < sp

	def _th_off(self, sp, v, hyst):
		return v > (sp + hyst)

	def is_night_time(self):
		h = localtime().tm_hour
		if h > 22 or h < 9:
			return True
		return False

	async def ambient_control_loop(self):
		s = self.sensors
		# Wait for sensors to fill with data...
		while True:
			await asyncio.sleep(1)
			#if not s.setpoint_tpo.online:
			#	continue
			if not s.temp_zone0.online or not s.temp_zone1.online:
				continue
			if not s.power_cv.online:
				continue
			#if not s.temp_tpo.online:
			#	continue
			break
		hyst = 1.0
		while True:
			await asyncio.sleep(4)
			if s.temp_tpo.online:
				sens_main = s.temp_tpo
			else:
				sens_main = s.temp_zone0
			sp = s.setpoint_tpo.state
			if sp < 16:
				sp = 18 # TPO probably offline
			spaux = self.setpoint_aux
			spcv = sp - self.DELTA_TEMP_CV
			ttpo = sens_main.state
			taux = s.temp_zone1.state
			wmh = self.want_main_heat
			wah = self.want_aux_heat
			wch = self.want_cv_heat
			self.setpoint_main = sp
			self.best_main_temp = ttpo
			self.best_aux_temp = taux

			# Want heat in main circuit
			if self._th_on(sp, ttpo, hyst):
				self.want_main_heat = True
			elif self._th_off(sp, ttpo, hyst):
				self.want_main_heat = False

			# Want heat in aux circuit, never during night hours.
			if self.is_night_time():
				self.want_aux_heat = False
			elif self._th_on(spaux, taux, hyst):
				self.want_aux_heat = True
			elif self._th_off(spaux, taux, hyst):
				self.want_aux_heat = False

			# Too cold, extra CV heat
			if self._th_on(spcv, ttpo, hyst):
				self.want_cv_heat = True
			elif self._th_off(spcv, ttpo, hyst):
				self.want_cv_heat = False

			if wmh != self.want_main_heat:
				info(f"MAIN HEAT: {'off' if wmh else 'on'}")

			if wah != self.want_aux_heat:
				info(f" AUX HEAT: {'off' if wah else 'on'}")

			if wch != self.want_cv_heat:
				info(f"  CV HEAT: {'off' if wch else 'on'}")

	async def cv_heat_control_loop(self):
		await asyncio.sleep(20) # Give sensors time to start up...
		cvh0 = None # Force initial state
		ts0 = monotonic() + self.MIN_OFF_TIME_CV
		tson = monotonic() + self.MIN_ON_TIME_CV
		# This is intentionally a polling loop, to avoid power chatter due to
		# software bugs or other unforeseen issues. Granularity is 5 seconds.
		while True:
			await asyncio.sleep(5)
			if self.manual_override:
				continue

			if self.relay_cv_heat.get_value() and self._timeout(tson):
				info("CV: duty off time")
				self.relay_cv_heat.set_value(0)
				ts0 = monotonic() + self.MIN_OFF_TIME_CV
				cvh0 = False # Force next cycle if heat still wanted.
			if self.want_cv_heat == cvh0:
				continue
			if not self._timeout(ts0):
				continue
			cvh0 = self.want_cv_heat
			if cvh0 and self.relay_cv_heat.get_value() == 0:
				info("CV: duty on time")
				self.relay_cv_heat.set_value(1)
				# CV heat minimum 2 minutes, maximum MAX_ON_TIME_CV
				ts0 = monotonic() + self.MIN_ON_TIME_CV
				tson = monotonic() + self.MAX_ON_TIME_CV
			elif self.relay_cv_heat.get_value() == 1:
				info("CV: Turn off")
				self.relay_cv_heat.set_value(0)
				# CV heat off cycle minimum 10 minutes
				ts0 = monotonic() + self.MIN_OFF_TIME_CV

	async def miner_idle_command(self):
		info("Miner idle command")
		self.ha.mqtt_pub(self.MINING_MQTT_TOPIC, "off")
		await asyncio.sleep(1)
		self.state = MinerStates.IDLE

	async def miner_mining_command(self):
		info("Miner mining command")
		self.ha.mqtt_pub(self.MINING_MQTT_TOPIC, "on")
		await asyncio.sleep(1)
		self.state = MinerStates.RUNNING

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
		may_need_enable = False
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
				if self.cv_power_water():
					info("Waiting for CV hot water to stop running.")
					continue
				t = self.get_highest_temp()
				if t > self.TEMP_LIMIT_SOFT_SHUTDOWN:
					warning(f"Temperature too high ({t} °C) while preparing to start! Waiting...")
					continue
				if self.state != MinerStates.OFF:
					warning(f"Trying to start miner while in {self.state.name} state! Waiting...")
					continue
				break
			if self._timeout(ts0):
				warning("Giving up preparing to start main power!")
				return -1
			info("Enable main power...")
			self.relay_contactor.set_value(1)
		else:
			info("Main power already enabled!")
			may_need_enable = True # Maybe we need to enable mining
		if self.state == MinerStates.RUNNING:
			info("Miner already in running state!")
			return -2
		elif self.state != MinerStates.OFF:
			warning(f"Miner wanted to start while in {self.state.name} state!")
			return -3
		self.state = MinerStates.STARTING
		info("Waiting for miner to boot...")
		await asyncio.sleep(10)
		if may_need_enable and 0 < self.sensors.power_wm.state < 500:
			await self.miner_mining_command()
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

	async def aux_main_toggle_loop(self):
		while True:
			await asyncio.sleep(10)
			dmain = self.setpoint_main - self.best_main_temp
			daux = self.setpoint_aux - self.best_aux_temp
			paux = (daux > dmain)
			if paux != self.prefer_aux:
				info(f"Prefer aux changed to {paux!r} Delta_aux: {daux:3.1f} Delta_main: {dmain:3.1f}")
				self.prefer_aux = paux
				# Don't make a new decision for the next 30 minutes.
				await asyncio.sleep(30 * 60)

	async def run(self):
		await self.webserver.startup()
		asyncio.create_task(self.sensor_updater())
		asyncio.create_task(self.miner_control_loop())
		asyncio.create_task(self.miner_power_loop())
		asyncio.create_task(self.ambient_control_loop())
		asyncio.create_task(self.cv_heat_control_loop())
		asyncio.create_task(self.aux_main_toggle_loop())
		await self.ha.run()

def main(args):
	"""
	Usage:
		kachel -h <host>

	Options:
		-h <host>       : Specify hostname/ip-address of miner
		-v              : Verbose mode. More logging output.
		-d              : Enable debug mode. Lot's of logging output!
		-s              : Enable logging to syslog.
		--help          : Show this message.

	Environment Variables to avoid leaking credentials to the command line:
		KACHEL_PASSWD  : Admin password.
		KACHEL_MQTTUSER, KACHEL_MQTTPASSWD : Likewise for MQTT broker access.
	"""
	# setproctitle.setproctitle("kachel")
	mqtthost = None
	debug = False
	verbose = False
	syslog = False
	manual = False
	while args:
		a = args.pop(0)
		if a == "-h":
			mqtthost = args.pop(0)
		elif a == "-v":
			verbose = True
		elif a == "-d":
			debug = True
		elif a == "-s":
			syslog = True
		elif a == "-m":
			manual = True
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
	if syslog:
		logger = logging.getLogger("root")
		sh = SysLogHandler(facility=SysLogHandler.LOG_DAEMON, address="/dev/log")
		logger.addHandler(sh)
	if mqtthost is None:
		print("ERROR: Use --help for usage.")
		return -1
	c = Controller(mqtthost, manual)
	asyncio.run(c.run())

if __name__ == "__main__":
	main(sys.argv[1:])
