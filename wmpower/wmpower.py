#!/usr/bin/env python3

import os
import sys
import functools
import json
import inspect
from whatsminer import WhatsminerAccessToken, WhatsminerAPI

class Whatsminer:
	def __init__(self, token, passwd):
		self.token = token
		if passwd is not None:
			self.token.enable_write_access(admin_password=passwd)

	def run_command(self, cmd, args=None):
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

	def stats(self):
		s = self.run_command("summary")["SUMMARY"][0]
		e = self.run_command("edevs")["DEVS"]
		upfreq = ",".join([str(x["Upfreq Complete"]) for x in e])
		print(f'Power: {s["Power_RT"]}W Voltage: {int(s["Voltage"])/1000:5.3f}V Fan speeds: {s["Fan Speed In"]}/{s["Fan Speed Out"]}rpm HR: {int(s["MHS av"])/1000000:4.1f}TH/s Temp: {s["Temperature"]}\u00b0C upfreq: {upfreq}')

	def psustats(self):
		s = self.run_command("get_psu")["Msg"]
		vin = int(s["vin"]) / 100
		iin = int(s["iin"]) / 1000
		pin = vin * iin
		print(f'Vin: {vin:4.1f} Vac Iin: {iin/1000:4.3f} Aac Pin: {pin:5.1f} VA Fan speed: {s["fan_speed"]} rpm')


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
	token = WhatsminerAccessToken(ip_address=host)
	w = Whatsminer(token, passwd)
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
