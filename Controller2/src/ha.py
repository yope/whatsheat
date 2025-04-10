
import asyncio
import gmqtt
import json
import uuid
from collections import deque
import aiohttp
import os
from datetime import datetime
from time import time
from logging import debug, info, warning, error

class HABase:
	def __init__(self, ha, uid, objid, name):
		self.ha = ha
		self.uid = uid
		self.objid = objid
		self.name = name
		self.topicbase = self.ha.topicbase_stem + objid

	def mqtt_connect(self):
		self.send_mqtt_config()

	def send_mqtt_config(self):
		self.ha.mqtt_pub(self.config_topic, self.config_message, retain=False, qos=1)

	def mqtt_disconnect(self):
		pass

class HASwitch(HABase):
	def __init__(self, ha, uid, objid, name, state):
		super().__init__(ha, uid, objid, name)
		self.state = state
		self.handler = None
		self.config_message = {
			"name": self.name,
			"object_id": self.objid,
			"unique_id": self.uid,
			"~": self.topicbase,
			"cmd_t": f"~/COMMAND",
			"stat_t": f"~/STATE",
		}
		self.config_topic = f"homeassistant/switch/{self.uid}/config"

	def mqtt_connect(self):
		super().mqtt_connect()
		self.ha.subscribe(f"{self.topicbase}/COMMAND", self.mqtt_message, fmt="utf-8")
		self.mqtt_state()

	def mqtt_message(self, msg):
		if self.handler == None:
			warning(f"HA Switch: no handler for message: {msg!r}")
			return
		if msg.lower() == "on":
			self.handler(1)
			self.mqtt_state(1)
		elif msg.lower() == "off":
			self.handler(0)
			self.mqtt_state(0)
		else:
			error(f"HA Switch ERROR: Don't understand message: {msg!r}")

	def add_handler(self, h):
		self.handler = h

	def mqtt_state(self, state=None):
		if state is None:
			state = self.state
		else:
			self.state = state
		msg = "ON" if state else "OFF"
		self.ha.mqtt_pub(f"{self.topicbase}/STATE", msg)

class HASensor(HABase):
	def __init__(self, ha, uid, objid, name, devclass, unit):
		super().__init__(ha, uid, objid, name)
		self.unit = unit
		self.devclass = devclass
		self.field = devclass.capitalize()
		self.config_message = {
			"name": self.name,
			"object_id": self.objid,
			"unique_id": self.uid,
			"~": self.topicbase,
			"cmd_t": f"~/COMMAND",
			"stat_t": f"~/SENSOR",
			"unit_of_measurement": unit,
			"device_class": devclass,
			"value_template": "{{ value_json."+self.field+" }}"
		}
		self.config_topic = f"homeassistant/sensor/{self.uid}/config"

	def mqtt_value(self, value):
		self.ha.mqtt_pub(f"{self.topicbase}/SENSOR", {self.field: value}, content_type="json")

class HATemperatureSensor(HASensor):
	def __init__(self, ha, uid, objid, name):
		super().__init__(ha, uid, objid, name, "temperature", "°C")

class HAVolumeSensor(HASensor):
	def __init__(self, ha, uid, objid, name):
		super().__init__(ha, uid, objid, name, "volume", "l")

class HACO2Sensor(HASensor):
	def __init__(self, ha, uid, objid, name):
		super().__init__(ha, uid, objid, name, "carbon_dioxide", "ppm")

class HAHumiditySensor(HASensor):
	def __init__(self, ha, uid, objid, name):
		super().__init__(ha, uid, objid, name, "humidity", "%")

class HAIlluminanceSensor(HASensor):
	def __init__(self, ha, uid, objid, name):
		super().__init__(ha, uid, objid, name, "illuminance", "lx")

class HAPressureSensor(HASensor):
	def __init__(self, ha, uid, objid, name):
		super().__init__(ha, uid, objid, name, "pressure", "hPa")

class HANumber(HABase):
	def __init__(self, ha, uid, objid, name, devclass, unit, state, minval, maxval):
		super().__init__(ha, uid, objid, name)
		self.unit = unit
		self.devclass = devclass
		self.field = devclass.capitalize()
		self.state = state
		self.minval = minval
		self.maxval = maxval
		self.handler = None
		self.config_message = {
			"name": self.name,
			"object_id": self.objid,
			"unique_id": self.uid,
			"~": self.topicbase,
			"cmd_t": f"~/COMMAND",
			"stat_t": f"~/STATE",
			"min": self.minval,
			"max": self.maxval,
			"step": 0.1, # FIXME: Make this also configurable?
			"unit_of_measurement": unit,
			"device_class": devclass,
		}
		self.config_topic = f"homeassistant/number/{self.uid}/config"

	def mqtt_connect(self):
		super().mqtt_connect()
		self.ha.subscribe(f"{self.topicbase}/COMMAND", self.mqtt_message, fmt="utf-8")
		self.mqtt_value(self.state)

	def mqtt_message(self, msg):
		if self.handler == None:
			warning(f"HA Number: no handler for message: {msg!r}")
			return
		try:
			val = float(msg)
		except ValueError:
			error(f"HA Number ERROR: Don't understand number: {msg!r}")
			return
		if self.minval <= val <= self.maxval:
			self.handler(val)
			self.mqtt_value(val)
		else:
			warning(f"HA Number out of range: {val} ({self.minval}...{self.maxval}). Ignoring.")

	def add_handler(self, h):
		self.handler = h

	def mqtt_value(self, value):
		self.ha.mqtt_pub(f"{self.topicbase}/STATE", value, content_type="json")

class HATemperatureSetpoint(HANumber):
	def __init__(self, ha, uid, objid, name, val):
		super().__init__(ha, uid, objid, name, "temperature", "°C", val, 16.0, 22.0)

class HomeAssistant:
	def __init__(self, mqttserver, mqttuser, mqttpasswd, base="kachel"):
		self.client = gmqtt.Client("kachel")
		self.mqttserver = mqttserver
		self.ev_disconnect = asyncio.Event()
		self.ev_disconnect.set()
		self.subscriptions = {}
		self.warned_once = {}
		self.switches = {}
		self.sensors = {}
		self.numbers = {}
		self.hostname = f"{uuid.getnode():012x}"
		self.baseid = f"{self.hostname}_{base}"
		self.topicbase_stem = f"{base}/{self.hostname}/"
		self.client.set_auth_credentials(mqttuser, mqttpasswd)
		self.client.on_connect = self._mqtt_connect
		self.client.on_message = self._mqtt_message
		self.client.on_disconnect = self._mqtt_disconnect
		self.client.on_subscribe = self._mqtt_subscribe
		if os.path.exists("home_assistant.token"):
			with open("home_assistant.token", "r") as f:
				self.ha_token = f.read().strip(" \r\n")
		else:
			self.ha_token = ""
			warning("HA: WARN: file 'home_assistant.token' not found. Please create a long lives access token.")

	def _mqtt_connect(self, c, flags, rc, properties):
		info("HA MQTT: Connected")
		self.ev_disconnect.clear()
		for topic in self.subscriptions:
			self.client.subscribe(topic, qos=1)
		debug("HA MQTT: Subscribed")
		for s in self.switches.values():
			s.mqtt_connect()
		for s in self.sensors.values():
			s.mqtt_connect()
		for s in self.numbers.values():
			s.mqtt_connect()
		debug("HA MQTT: Config sent")

	def subscribe(self, topic, handler, fmt="json"):
		self.subscriptions[topic] = (handler, fmt)
		if not self.ev_disconnect.is_set():
			self.client.subscribe(topic, qos=1)

	def _mqtt_disconnect(self, c=None, packet=None, exc=None):
		self.ev_disconnect.set()
		info("HA MQTT: Disconnected")
		for s in self.switches.values():
			s.mqtt_disconnect()
		for s in self.sensors.values():
			s.mqtt_disconnect()
		for s in self.numbers.values():
			s.mqtt_disconnect()

	def _call_topic_handler(self, h, fmt, payload):
		if fmt == "json":
			try:
				payload = json.loads(payload)
			except json.JSONDecodeError:
				error(f"HA MQTT ERROR: payload not json format: {payload!r}")
				return
		elif fmt == "utf-8" or fmt == "ascii" or fmt == "iso8859-1":
			try:
				payload = payload.decode(fmt)
			except DecodeError:
				error(f"HA MQTT ERROR: payload not in {fmt!r} format: {payload!r}")
				return
		h(payload)

	def _mqtt_message(self, c, topic, payload, qos, properties):
		# First check complete topic match:
		if topic in self.subscriptions:
			h, fmt = self.subscriptions[topic]
			self._call_topic_handler(h, fmt, payload)
			return
		# If none matched, search for topic stems and wildcard subscriptions:
		handled = False
		for t in self.subscriptions:
			t = t.strip("#")
			if t in topic:
				h = self.subscriptions[t]
				self._call_topic_handler(h, fmt, payload)
				handled = True
		if not handled:
			warning(f"HA MQTT: Unhandled message topic {topic!r}: {payload!r}")

	def _mqtt_subscribe(self, c, mid, qos, properties):
		pass

	async def state_updater(self):
		while True:
			await asyncio.sleep(10)
			if self.ev_disconnect.is_set():
				continue
			for s in self.switches.values():
				s.mqtt_state()

	async def run(self):
		asyncio.create_task(self.state_updater())
		while True:
			try:
				await self.client.connect(self.mqttserver)
			except (OSError, asyncio.CancelledError):
				print("HA MQTT client connect error. Waiting 20 seconds...")
				self._mqtt_disconnect()
				await asyncio.sleep(20)
			await self.ev_disconnect.wait()

	def mqtt_pub(self, topic, msg, retain=False, qos=0, content_type='text'):
		if self.ev_disconnect.is_set():
			return
		if content_type == "json":
			msg = json.dumps(msg).encode('utf-8')
		self.client.publish(topic, msg, qos=qos, retain=retain)

	def create_switch(self, objid, name, state):
		if objid in self.switches:
			error(f"HA MQTT ERROR: Switch id {objid} already exists!")
			raise ValueError
		num = len(self.switches)
		uid = self.baseid + f"SW{num}"
		s = HASwitch(self, uid, objid, name, state)
		self.switches[objid] = s
		return s

	def _create_sensor(self, objid, name, cls, letter):
		if objid in self.sensors:
			error(f"HA MQTT ERROR: Sensor id {objid} already exists!")
			raise ValueError
		num = len(self.sensors)
		uid = self.baseid + f"{letter}{num}"
		s = cls(self, uid, objid, name)
		self.sensors[objid] = s
		return s

	def _create_number(self, objid, name, cls, letter, initval):
		if objid in self.numbers:
			error(f"HA MQTT ERROR: Number id {objid} already exists!")
			raise ValueError
		num = len(self.numbers)
		uid = self.baseid + f"{letter}{num}"
		s = cls(self, uid, objid, name, initval)
		self.numbers[objid] = s
		return s

	def create_temperature_sensor(self, objid, name):
		return self._create_sensor(objid, name, HATemperatureSensor, "T")

	def create_volume_sensor(self, objid, name):
		return self._create_sensor(objid, name, HAVolumeSensor, "V")

	def create_co2_sensor(self, objid, name):
		return self._create_sensor(objid, name, HACO2Sensor, "CO2")

	def create_humidity_sensor(self, objid, name):
		return self._create_sensor(objid, name, HAHumiditySensor, "H")

	def create_illuminance_sensor(self, objid, name):
		return self._create_sensor(objid, name, HAIlluminanceSensor, "L")

	def create_pressure_sensor(self, objid, name):
		return self._create_sensor(objid, name, HAPressureSensor, "P")

	def create_temperature_setpoint(self, objid, name, initval):
		return self._create_number(objid, name, HATemperatureSetpoint, "Tsp", initval)

	async def restapi_get(self, url):
		baseurl = f"http://{self.mqttserver}:8123"
		url = f"/api/{url}"
		headers = {
			"Authorization": f"Bearer {self.ha_token}",
			"content-type": "application/json",
		}
		ret = None
		try:
			async with aiohttp.ClientSession(baseurl, headers=headers) as session:
				async with session.get(url) as resp:
					ret = await resp.json()
		except aiohttp.ServerDisconnectedError:
			ret = None
		return ret

	async def get_sensor_state_and_timestamp(self, objid):
		try:
			obj = await self.restapi_get(f"states/sensor.{objid}")
		except aiohttp.client_exceptions.ClientConnectorError:
			error(f"Connection error trying to get sensor.{objid}")
			return None, None
		try:
			state = obj["state"]
			lupd = obj["last_updated"]
		except KeyError:
			error(f"HA Error: Sensor {objid} does not exist?")
			return None, None
		try:
			state = float(state)
		except ValueError:
			if not self.warned_once.get(objid, False):
				error(f"HA Error: Sensor {objid} state is non numeric.")
				self.warned_once[objid] = True
			return None, None
		ts = datetime.fromisoformat(lupd)
		return state, ts.timestamp()
