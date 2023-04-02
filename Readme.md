
# Whatsminer water heating system

This project contains information, knowledge learned and tools developed while
building a Bitcoin mining based water heating system to be integrated into the
central heating system of my house.

## Objectives

### 1. Whatsminer remote-control

For a heating control system, the most important aspect is to be able to
control the miner. The most basic form of control would be to use a wifi-
power switch for example, to turn AC power on or off depending on the need
for heat. The problem with that is, that the power supplies of these miners
are not meant to be turned on and off too often. Fortunately the thermal
capacity of a house is such that one could do this with at most 1 or 2 on/off
cycles per day, if a temperature ripple of 1 or 2 degrees Celsius is
acceptable.

The proprietary [whatsminer API](https://aws-microbt-com-bucket.s3.us-west-2.amazonaws.com/WhatsminerAPI%20V2.0.4.pdf)
offers some interesting options: The commands "power_on" and "power_off" enable
or disable mining. The actual power supply stays on, but the hash boards are
disabled with the "power_off" command. Unfortunately though, the "power_on"
command starts with the *upfreq* process, which can take quite some time.
Luckily in the latest firmware version, as well as the special immersion mode
firmware from MicroBT one can adjust the upfreq speed with their proprietary
windows tool, to make it as short as 2 minutes.

### 2. Clock control

A much better way to control heating power is through the miner clock-rate
control. Contrary to what many may believe, it is in fact possible to control
the chip-clock setpoint of whatsminers in a very wide range even without custom
third-party firmware!
Unfortunately it seems to be only possible to do this via their windows tool.
The Whatsminer API command "set_target_freq" does not seem to work.
The big question is: How low can it go? Some testing revealed interesting
results. Apparently this setting works fine up until -50%. If I try to go any
lower, the clock control algorithm of my Whatsminer M31S would get unstable
and very quickly climb so high that the PSU would go into over-current shutdown
at 3800W power! But hey, -50% clock rate is pretty nice. My M31S seems to work
just fine at -50% clock, consuming a little less than 1460W and producing around
38TH/s on average. This is amounts to less than 39J/Th efficiency. Not bad for
a regular M31S!

### 3. API Access

It turns out that there is a [Python implementation](https://github.com/satoshi-anonymoto/whatsminer-api).
This repository contains a small python tool that uses the python whatsminer API
to do some simple power control. It also has an option to specify an MQTT server
IP address as well as simple MQTT access credentials. If an MQTT host is
specified on the command line, the tool will start in daemon mode, publish
Home Assistant auto configuration messages and start monitoring and polling the
miner for data every 10 seconds. A switch in Home Assistant can be used to
power on or off the miner.

See the help for more information on what this tool can do:

 $ ./wmpower/wmpower.py --help

### 3. Immersion cooling setup

[TODO]
