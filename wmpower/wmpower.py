#!/usr/bin/env python3

import os
import sys
import functools
import json
import inspect
from whatsminer import WhatsminerAccessToken, WhatsminerAPI

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
		except KeyError:
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


def main(args):
	"""
	Usage:
		wmpower.py -h <host> [-p <password>] command [args]

	Options:
		-h <host>       : Specify hostname/ip-address of miner
		-p <password>   : Admin password, needed only for non read-only commands
		--help          : Display this help text

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
	passwd = None
	command = None
	resp = None
	cmdargs = []
	while args:
		a = args.pop(0)
		if a == "-h":
			host = args.pop(0)
		elif a == "-p":
			passwd = args.pop(0)
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
	if command is None:
		w.stats()
	elif command == "psustats":
		w.psustats()
	else:
		resp = w.run_command(command, cmdargs)
	if resp is not None:
		print(json.dumps(resp, indent=4))

if __name__ == "__main__":
	main(sys.argv[1:])
