#!/usr/bin/env python3

import os
import sys
import functools
import json
import inspect
from whatsminer import WhatsminerAccessToken, WhatsminerAPI
import gmqtt
import socket
import asyncio

class Whatsminer:
	def __init__(self, host, passwd):
		self.passwd = passwd
		self.host = host
		self.online = False

	def connect(self):
		self.token = WhatsminerAccessToken(ip_address=self.host)
		if self.passwd is not None:
			self.token.enable_write_access(admin_password=self.passwd)
		self.online = True

	def set_offline(self):
		self.online = False

	def run_command(self, cmd, args=None):
		if not self.online:
			self.connect()
		wacmd = functools.partial(WhatsminerAPI.exec_command, access_token=self.token)
		waro = functools.partial(WhatsminerAPI.get_read_only_info, access_token=self.token)
		if cmd == "power":
			onoff = args[0]
			if onoff == "on":
				resp = wacmd(cmd="power_on")
			elif onoff == "off":
				resp = wacmd(cmd="power_off", additional_params={"respbefore": "true"})
			else:
				print("Error: unknown parameter to power command")
				return None
		elif cmd == "summary":
			resp = waro(cmd="summary")
		elif cmd == "status":
			resp = waro(cmd="status")
		elif cmd == "led":
			mode = args[0]
			resp = wacmd(cmd="set_led", additional_params={"param": mode})
		elif cmd == "set_target_freq":
			freq = args[0]
			resp = wacmd(cmd="set_target_freq", additional_params={"percent": freq})
		elif cmd == "edevs":
			resp = waro(cmd="edevs")
		elif cmd == "get_psu":
			resp = waro(cmd="get_psu")
		else:
			print(f"Error: Unknown command {cmd}")
			return None
		return resp

	def get_psu_stats(self):
		psu = self.run_command("get_psu")["Msg"]
		vin = int(psu["vin"]) / 100
		iin = int(psu["iin"]) / 1000
		fs = int(psu["fan_speed"])
		pw = int(vin * iin)
		return vin, iin, pw, fs

	def get_summary_stats(self):
		try:
			s = self.run_command("summary")["SUMMARY"][0]
		except (KeyError, json.JSONDecodeError):
			return 0, 0, 0, 0, 0, 0
		if not "Voltage" in s:
			s["Voltage"]=0.0
		hr = int(s["MHS av"]) / 1000000
		voltage = int(s["Voltage"])/1000
		fanin = int(s["Fan Speed In"])
		fanout = int(s["Fan Speed Out"])
		freq = int(s["freq_avg"])
		temp = float(s["Temperature"])
		return voltage, fanin, fanout, freq, hr, temp

	def stats(self):
		s = self.run_command("summary")["SUMMARY"][0]
		try:
			e = self.run_command("edevs")["DEVS"]
		except KeyError:
			print("Device not up yet")
			upfreq = "0,0,0"
		else:
			upfreq = ",".join([str(x["Upfreq Complete"]) for x in e])
		vin, iin, pw, _fs = self.get_psu_stats()
		# pw = int(s["Power"])
		hr = int(s["MHS av"]) / 1000000
		if not "Voltage" in s:
			s["Voltage"]=0.0
		try:
			eff = pw / hr
		except ZeroDivisionError:
			eff = 0
		print(f'Power: {pw}W Voltage: {int(s["Voltage"])/1000:5.3f}V Fan speeds: {s["Fan Speed In"]}/{s["Fan Speed Out"]}rpm Freq: {s["freq_avg"]}MHz HR: {hr:4.1f}TH/s Eff: {eff:5.1f}J/Th Temp: {s["Temperature"]}\u00b0C upfreq: {upfreq}')

	def psustats(self):
		vin, iin, pin, fs = self.get_psu_stats()
		print(f'Vin: {vin:4.1f} Vac Iin: {iin:4.3f} Aac Pin: {pin:5.1f} VA Fan speed: {fs} rpm')

class HAMiner:
	def __init__(self, whatsminer, mqtthost, mqttuser, mqttpass, hostname=None):
		self._disconnected = asyncio.Event()
		self.reconnect = True
		self.retries = 3
		if hostname is None:
			hostname = socket.gethostname()
		self.hostname = hostname
		self.baseid = f"{self.hostname}_wmpower"
		self.wm = whatsminer
		self.topicbase = f"wmpower/{self.hostname}/whatsminer"
		self.client = gmqtt.Client("clientid")
		self.client.on_disconnect = self.mqtt_disconnect
		self.client.on_message = self.mqtt_message
		self.client.set_auth_credentials(mqttuser, mqttpass)
		self.mqtthost = mqtthost

	def mqtt_disconnect(self, client, packet, exc=None):
		if self.retries <= 0:
			self.reconnect = False
		self._disconnected.set()

	def mqtt_message(self, cient, topic, payload, qos, props):
		print(f"MQTT RX topic:{topic}, payload:{payload!r}")
		tparts = topic.split("/")
		if tparts[-1] == "mining":
			onoff = payload.decode("utf-8").lower()
			if onoff not in ["on", "off"]:
				print(f"Error, mining payload {onoff!r} not recognized!")
				return
			print("Set minig to:", payload.decode("utf-8"))
			self.wm.run_command("power", [onoff])

	async def run_async_timed(self, func, *args, timeout=5.0):
		loop = asyncio.get_running_loop()
		return await asyncio.wait_for(loop.run_in_executor(None, func, *args), timeout)

	async def get_psu_stats(self):
		try:
			return await self.run_async_timed(self.wm.get_psu_stats)
		except (TimeoutError, OSError, asyncio.exceptions.TimeoutError):
			print("WM timeout in get_psu_stats")
			self.wm.set_offline()
			return 0, 0, 0, 0

	async def get_summary_stats(self):
		try:
			return await self.run_async_timed(self.wm.get_summary_stats)
		except (TimeoutError, OSError, asyncio.exceptions.TimeoutError):
			print("WM timeout in get_summary_stats")
			self.wm.set_offline()
			return 0, 0, 0, 0, 0, 0

	async def shutdown(self):
		self.reconnect = False
		await self.client.disconnect()

	async def run(self):
		while True:
			self._disconnected.clear()
			if not self.reconnect:
				break
			print("MQTT: Connecting...")
			try:
				await self.client.connect(self.mqtthost)
			except ConnectionRefusedError:
				print("MQTT: Connection refused... retrying in 20 seconds.")
				await asyncio.sleep(20)
				continue
			self.client.subscribe([
				gmqtt.Subscription(f"{self.topicbase}/#", qos=1)
			])
			self.ha_config()
			await self.coro_connection()
			self.retries -= 1
			if self.retries <= 0:
				self.reconnect = False

	async def coro_connection(self):
		print("MQTT: Connected, processing messages")
		while not self._disconnected.is_set():
			vin, iin, pin, fs = await self.get_psu_stats()
			voltage, fanin, fanout, freq, hr, temp = await self.get_summary_stats()
			if temp > 0:
				self.client.publish(f"{self.topicbase}/SENSOR", {
					"VoltageIn": vin,
					"CurrentIn": iin,
					"Power": pin,
					"PSUFanSpeed": fs,
					"InputFanSpeed": fanin,
					"OuputFanSpeed": fanout,
					"VoltageChip": voltage,
					"Frequency": freq,
					"HashRate": hr,
					"Temperature": temp
				}, qos=1, content_type='json')
			minerstate = "ON" if pin > 300 else "OFF"
			self.client.publish(f"{self.topicbase}/state", minerstate, qos=1, content_type='utf-8')
			await asyncio.sleep(10)

	def ha_config(self):
		self.client.publish(f"homeassistant/switch/{self.baseid}S/config", {
			"name": "Whatsminer mining",
			"object_id": "whatsminer_mining_switch",
			"unique_id": f"{self.baseid}S",
			"~": self.topicbase,
			"cmd_t": "~/mining",
			"stat_t": "~/state",
		}, qos=1, content_type='json')
		self.client.publish(f"homeassistant/sensor/{self.baseid}T/config", {
			"name": "Whatsminer temperature",
			"object_id": "whatsminer_temperature",
			"unique_id": f"{self.baseid}T",
			"~": self.topicbase,
			"stat_t": "~/SENSOR",
			"unit_of_measurement": "°C",
			"device_class": "temperature",
			"value_template": "{{ value_json.Temperature}}"
		}, qos=1, content_type='json')
		self.client.publish(f"homeassistant/sensor/{self.baseid}P/config", {
			"name": "Whatsminer power",
			"object_id": "whatsminer_power",
			"unique_id": f"{self.baseid}P",
			"~": self.topicbase,
			"stat_t": "~/SENSOR",
			"unit_of_measurement": "W",
			"device_class": "power",
			"value_template": "{{ value_json.Power}}"
		}, qos=1, content_type='json')
		self.client.publish(f"homeassistant/sensor/{self.baseid}HR/config", {
			"name": "Whatsminer hash rate",
			"object_id": "whatsminer_hashrate",
			"unique_id": f"{self.baseid}HR",
			"~": self.topicbase,
			"stat_t": "~/SENSOR",
			"unit_of_measurement": "TH/s",
			"value_template": "{{ value_json.HashRate}}"
		}, qos=1, content_type='json')

def main(args):
	"""
	Usage:
		wmpower.py -h <host> [-p <password>] command [args]

	Options:
		-h <host>       : Specify hostname/ip-address of miner
		-p <password>   : Admin password, needed only for non read-only commands
		-m <mqtthost>   : Start MQTT client connected to <mqtthost>
		-u <mqttuser>   : Specify the user or token to authenticate to MQTT broker
		-w <mqttpasswd> : Specify optional MQTT broker password
		-H <hostname>   : Use <hostname> for MQTT topic instead of own hostname
		--help          : Display this help text

	Environment Variables to avoid leaking credentials to the command line:
		WMPOWER_PASSWD  : Admin password.
		WMPOWER_MQTTUSER, WMPOWER_MQTTPASSWD : Likewise for MQTT broker access.

	Commands:
		stats           : Display one-line status of miner (default if command omitted)
		psustats        : Display current input voltage, -current and -power of PSU
		power [on|off]  : Turn hashing power on or off
		summary         : Print comlete summary in json format
		status          : Print miner status in json format
		led auto        : Set LED mode back to "auto" (other modes unknown)
		edevs           : Print hash board status in json format
		get_psu         : Print PSU status in json format
	"""
	host = None
	passwd = os.environ.get("WMPOWER_PASSWD", None)
	command = None
	resp = None
	mqtthost = None
	hostname = None
	mqttuser = os.environ.get("WMPOWER_MQTTUSER", None)
	mqttpasswd = os.environ.get("WMPOWER_MQTTPASSWD", None)
	cmdargs = []
	while args:
		a = args.pop(0)
		if a == "-h":
			host = args.pop(0)
		elif a == "-p":
			passwd = args.pop(0)
		elif a == "-m":
			mqtthost = args.pop(0)
		elif a == "-u":
			mqttuser = args.pop(0)
		elif a == "-w":
			mqttpasswd = args.pop(0)
		elif a == "-H":
			hostname = args.pop(0)
		elif a == "--help":
			print(inspect.cleandoc(main.__doc__))
			return 0
		elif command is None:
			command = a
			cmdargs = args
			break
	if host is None:
		print("Error must provide hostname/ip-address")
		print(inspect.cleandoc(main.__doc__))
		return 1
	w = Whatsminer(host, passwd)
	if mqtthost is not None:
		haminer = HAMiner(w, mqtthost, mqttuser, mqttpasswd)
		asyncio.run(haminer.run())
	elif command is None:
		w.stats()
	elif command == "psustats":
		w.psustats()
	else:
		resp = w.run_command(command, cmdargs)
	if resp is not None:
		print(json.dumps(resp, indent=4))

if __name__ == "__main__":
	main(sys.argv[1:])
