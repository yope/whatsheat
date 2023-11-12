
import asyncio
import gmqtt
import json
import uuid
from collections import deque

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
			print(f"HA Switch: no handler for message: {msg!r}")
			return
		if msg.lower() == "on":
			self.handler(1)
			self.mqtt_state(1)
		elif msg.lower() == "off":
			self.handler(0)
			self.mqtt_state(0)
		else:
			print(f"HA Switch ERROR: Don't understand message: {msg!r}")

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

class HomeAssistant:
	def __init__(self, mqttserver, mqttuser, mqttpasswd, base="kachel"):
		self.client = gmqtt.Client("kachel")
		self.mqttserver = mqttserver
		self.ev_disconnect = asyncio.Event()
		self.ev_disconnect.set()
		self.subscriptions = {}
		self.switches = {}
		self.sensors = {}
		self.hostname = f"{uuid.getnode():012x}"
		self.baseid = f"{self.hostname}_{base}"
		self.topicbase_stem = f"{base}/{self.hostname}/"
		self.client.set_auth_credentials(mqttuser, mqttpasswd)
		self.client.on_connect = self._mqtt_connect
		self.client.on_message = self._mqtt_message
		self.client.on_disconnect = self._mqtt_disconnect
		self.client.on_subscribe = self._mqtt_subscribe

	def _mqtt_connect(self, c, flags, rc, properties):
		print("HA MQTT: Connected")
		self.ev_disconnect.clear()
		for topic in self.subscriptions:
			self.client.subscribe(topic, qos=1)
		print("HA MQTT: Subscribed")
		for s in self.switches.values():
			s.mqtt_connect()
		for s in self.sensors.values():
			s.mqtt_connect()
		print("HA MQTT: Config sent")

	def subscribe(self, topic, handler, fmt="json"):
		self.subscriptions[topic] = (handler, fmt)
		if not self.ev_disconnect.is_set():
			self.client.subscribe(topic, qos=1)

	def _mqtt_disconnect(self, c, packet, exc=None):
		self.ev_disconnect.set()
		print("HA MQTT: Disconnected")
		for s in self.switches.values():
			s.mqtt_disconnect()
		for s in self.sensors.values():
			s.mqtt_disconnect()

	def _call_topic_handler(self, h, fmt, payload):
		if fmt == "json":
			try:
				payload = json.loads(payload)
			except json.JSONDecodeError:
				print(f"HA MQTT ERROR: payload not json format: {payload!r}")
				return
		elif fmt == "utf-8" or fmt == "ascii" or fmt == "iso8859-1":
			try:
				payload = payload.decode(fmt)
			except DecodeError:
				print(f"HA MQTT ERROR: payload not in {fmt!r} format: {payload!r}")
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
			print(f"HA MQTT: Unhandled message topic {topic!r}: {payload!r}")

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
			await self.client.connect(self.mqttserver)
			await self.ev_disconnect.wait()

	def mqtt_pub(self, topic, msg, retain=False, qos=0, content_type='text'):
		if self.ev_disconnect.is_set():
			return
		if content_type == "json":
			msg = json.dumps(msg).encode('utf-8')
		self.client.publish(topic, msg, qos=qos, retain=retain)

	def create_switch(self, objid, name, state):
		if objid in self.switches:
			print(f"HA MQTT ERROR: Switch id {objid} already exists!")
			raise ValueError
		num = len(self.switches)
		uid = self.baseid + f"SW{num}"
		s = HASwitch(self, uid, objid, name, state)
		self.switches[objid] = s
		return s

	def _create_sensor(self, objid, name, cls, letter):
		if objid in self.sensors:
			print(f"HA MQTT ERROR: Sensor id {objid} already exists!")
			raise ValueError
		num = len(self.sensors)
		uid = self.baseid + f"{letter}{num}"
		s = cls(self, uid, objid, name)
		self.sensors[objid] = s
		return s

	def create_temperature_sensor(self, objid, name):
		return self._create_sensor(objid, name, HATemperatureSensor, "T")

	def create_volume_sensor(self, objid, name):
		return self._create_sensor(objid, name, HAVolumeSensor, "V")

