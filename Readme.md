
# Whatsminer water heating system

This project contains information, knowledge learned and tools developed while
building a Bitcoin mining based water heating system to be integrated into the
centtral heating system of my house.

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

Another problem to keep in mind is that specially Whatsminer ASICs tend to take
a long time to settle to the proper clock frequency and chip voltage values.
Whatsminer calls this the "Upfreq" process, and it can take up to 30 minutes
even in fast boot mode (more about that later). During this period, the miner
consumes a lot of power, but has a very low hash-rate, so you don't want to do
that very often.

### 2. Clock control

A much better way to control heating power is through the miner clock-rate
control. Contrary to what many may believe, it is in fact possible to control
the chip-clock setpoint of whatsminers in a very wide range even without custom
third-party firmware!
The answer is the priprietary [whatsminer API](https://aws-microbt-com-bucket.s3.us-west-2.amazonaws.com/WhatsminerAPI%20V2.0.4.pdf).
It turns out that there even is a [Python implementation](https://github.com/satoshi-anonymoto/whatsminer-api).

This API has an interesting command called "set_target_freq", which can set a
percentage change of the chip target clock frequency. The range mentioned in the
API document is a bit misleading though, since it says "-100...+100". Obviously
setting the clock to -100% makes no sense, so there must be a lower limit to
this setting somewhere between -100 and 0. The big question is: How low can it
go? Some testing revealed interesting results. Apparently this setting works
fine up until -50%. If I try to go any lower, the clock control algorithm of my
Whatsminer M31S would get unstable and very quickly climb so high that the PSU
would go into over-current shutdown at 3800W power! But hey, -50% clock rate is
pretty nice. My M31S seems to work just fine at -50% clock, consuming a little
less than 1600W and producing around 38TH/s on average. This is amounts to
around 42J/Th efficiency. Not bad for a regular M31S!

This repository contains a small python tool that uses the python whatsminer API
to do some simple power control. To use it, you need to install the latest
version of the python whatsminer API library first:

 $ pip install whatsminer

See the help for more information on what this tool can do:

 $ ./wmpower/wmpower.py --help

### 3. Immersion cooling setup

[TODO]
