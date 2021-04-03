#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import _thread
from datetime import datetime, timedelta
from tzlocal import get_localzone
import threading
import socket
import os
import subprocess
import uuid
import ssl
import sys
import re
import json
import os.path
import argparse
from time import time, sleep, localtime, strftime
from collections import OrderedDict
from colorama import init as colorama_init
from colorama import Fore, Back, Style
from configparser import ConfigParser
from unidecode import unidecode
import paho.mqtt.client as mqtt
import sdnotify
from signal import signal, SIGPIPE, SIG_DFL
signal(SIGPIPE,SIG_DFL)

script_version = "1.5.4"
script_name = 'ISP-RPi-mqtt-daemon.py'
script_info = '{} v{}'.format(script_name, script_version)
project_name = 'RPi Reporter MQTT2HA Daemon'
project_url = 'https://github.com/ironsheep/RPi-Reporter-MQTT2HA-Daemon'

# we'll use this throughout
local_tz = get_localzone()

# TODO:
#  - add announcement of free-space and temperatore endpoints

if False:
    # will be caught by python 2.7 to be illegal syntax
    print_line('Sorry, this script requires a python3 runtime environment.', file=sys.stderr)
    os._exit(1)

# Argparse
opt_debug = False
opt_verbose = False

# Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()

# Logging function
def print_line(text, error=False, warning=False, info=False, verbose=False, debug=False, console=True, sd_notify=False):
    timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
    if console:
        if error:
            print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
        elif warning:
            print(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
        elif info or verbose:
            if opt_verbose:
                print(Fore.GREEN + '[{}] '.format(timestamp) + Fore.YELLOW  + '- ' + '{}'.format(text) + Style.RESET_ALL)
        elif debug:
            if opt_debug:
                print(Fore.CYAN + '[{}] '.format(timestamp) + '- (DBG): ' + '{}'.format(text) + Style.RESET_ALL)
        else:
            print(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)

    timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
    if sd_notify:
        sd_notifier.notify('STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))

# Identifier cleanup
def clean_identifier(name):
    clean = name.strip()
    for this, that in [[' ', '-'], ['ä', 'ae'], ['Ä', 'Ae'], ['ö', 'oe'], ['Ö', 'Oe'], ['ü', 'ue'], ['Ü', 'Ue'], ['ß', 'ss']]:
        clean = clean.replace(this, that)
    clean = unidecode(clean)
    return clean

# Argparse
parser = argparse.ArgumentParser(description=project_name, epilog='For further details see: ' + project_url)
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
parser.add_argument("-d", "--debug", help="show debug output", action="store_true")
parser.add_argument("-s", "--stall", help="TEST: report only the first time", action="store_true")
parser.add_argument("-c", '--config_dir', help='set directory where config.ini is located', default=sys.path[0])
parse_args = parser.parse_args()

config_dir = parse_args.config_dir
opt_debug = parse_args.debug
opt_verbose = parse_args.verbose
opt_stall = parse_args.stall

print_line(script_info, info=True)
if opt_verbose:
    print_line('Verbose enabled', info=True)
if opt_debug:
    print_line('Debug enabled', debug=True)
if opt_stall:
    print_line('TEST: Stall (no-re-reporting) enabled', debug=True)

# -----------------------------------------------------------------------------
#  MQTT handlers
# -----------------------------------------------------------------------------

# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks

mqtt_client_connected = False
print_line('* init mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
mqtt_client_should_attempt_reconnect = True

def on_connect(client, userdata, flags, rc):
    global mqtt_client_connected
    if rc == 0:
        print_line('* MQTT connection established', console=True, sd_notify=True)
        print_line('')  # blank line?!
        #_thread.start_new_thread(afterMQTTConnect, ())
        mqtt_client_connected = True
        print_line('on_connect() mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
    else:
        print_line('! Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True)
        print_line('MQTT Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True, sd_notify=True)
        mqtt_client_connected = False   # technically NOT useful but readying possible new shape...
        print_line('on_connect() mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True, error=True)
        #kill main thread
        os._exit(1)

def on_publish(client, userdata, mid):
    #print_line('* Data successfully published.')
    pass

# Load configuration file
config = ConfigParser(delimiters=('=', ), inline_comment_prefixes=('#'))
config.optionxform = str
try:
    with open(os.path.join(config_dir, 'config.ini')) as config_file:
        config.read_file(config_file)
except IOError:
    print_line('No configuration file "config.ini"', error=True, sd_notify=True)
    sys.exit(1)

daemon_enabled = config['Daemon'].getboolean('enabled', True)

# This script uses a flag file containing a date/timestamp of when the system was last updated
default_update_flag_filespec = '/home/pi/bin/lastupd.date'
update_flag_filespec = config['Daemon'].get('update_flag_filespec', default_update_flag_filespec)

default_base_topic = 'home/nodes'
base_topic = config['MQTT'].get('base_topic', default_base_topic).lower()

default_sensor_name = 'rpi-reporter'
sensor_name = config['MQTT'].get('sensor_name', default_sensor_name).lower()

# by default Home Assistant listens to the /homeassistant but it can be changed for a given installation
default_discovery_prefix = 'homeassistant'
discovery_prefix = config['MQTT'].get('discovery_prefix', default_discovery_prefix).lower()

# report our RPi values every 5min
min_interval_in_minutes = 2
max_interval_in_minutes = 30
default_interval_in_minutes = 5
interval_in_minutes = config['Daemon'].getint('interval_in_minutes', default_interval_in_minutes)

# default domain when hostname -f doesn't return it
default_domain = ''
fallback_domain = config['Daemon'].get('fallback_domain', default_domain).lower()


# Check configuration
#
if (interval_in_minutes < min_interval_in_minutes) or (interval_in_minutes > max_interval_in_minutes):
    print_line('ERROR: Invalid "interval_in_minutes" found in configuration file: "config.ini"! Must be [{}-{}] Fix and try again... Aborting'.format(min_interval_in_minutes, max_interval_in_minutes), error=True, sd_notify=True)
    sys.exit(1)

### Ensure required values within sections of our config are present
if not config['MQTT']:
    print_line('ERROR: No MQTT settings found in configuration file "config.ini"! Fix and try again... Aborting', error=True, sd_notify=True)
    sys.exit(1)

print_line('Configuration accepted', console=False, sd_notify=True)

# -----------------------------------------------------------------------------
#  RPi variables monitored
# -----------------------------------------------------------------------------

rpi_mac = ''
rpi_model_raw = ''
rpi_model = ''
rpi_connections = ''
rpi_hostname = ''
rpi_fqdn = ''
rpi_linux_release = ''
rpi_linux_version = ''
rpi_uptime_raw = ''
rpi_uptime = ''
rpi_load_1m = ''
rpi_load_5m = ''
rpi_load_15m = ''
rpi_last_update_date = datetime.min
#rpi_last_update_date_v2 = datetime.min
rpi_filesystem_space_raw = ''
rpi_filesystem_space = ''
rpi_filesystem_percent = ''
rpi_cpu_temp = ''
rpi_mqtt_script = script_info
rpi_interfaces = []
rpi_filesystem = []
# Tuple (Total, Free, Avail.)
rpi_memory_tuple = ''
# Tuple (Hardware, Model Name, NbrCores, BogoMIPS, Serial)
rpi_cpu_tuple = ''

# -----------------------------------------------------------------------------
#  monitor variable fetch routines
#
def getDeviceCpuInfo():
    global rpi_cpu_tuple
    #  cat /proc/cpuinfo | egrep -i "processor|model|bogo|hardware|serial"
    # MULTI-CORE
    #  processor	: 0
    #  model name	: ARMv7 Processor rev 4 (v7l)
    #  BogoMIPS	: 38.40
    #  processor	: 1
    #  model name	: ARMv7 Processor rev 4 (v7l)
    #  BogoMIPS	: 38.40
    #  processor	: 2
    #  model name	: ARMv7 Processor rev 4 (v7l)
    #  BogoMIPS	: 38.40
    #  processor	: 3
    #  model name	: ARMv7 Processor rev 4 (v7l)
    #  BogoMIPS	: 38.40
    #  Hardware	: BCM2835
    #  Serial		: 00000000a8d11642
    #
    # SINGLE CORE
    #  processor	: 0
    #  model name	: ARMv6-compatible processor rev 7 (v6l)
    #  BogoMIPS	: 697.95
    #  Hardware	: BCM2835
    #  Serial		: 00000000131030c0
    #  Model		: Raspberry Pi Zero W Rev 1.1
    out = subprocess.Popen("lscpu | egrep -i 'vendor|^CPU\(s\)|model name|architecture|bogo'",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    lines = stdout.decode('utf-8').split("\n")
    trimmedLines = []
    for currLine in lines:
        trimmedLine = currLine.lstrip().rstrip()
        trimmedLines.append(trimmedLine)
    cpu_vendor = ''
    cpu_architecture = ''
    cpu_cores = 0
    cpu_model = ''
    cpu_bogomips = 0.0
    for currLine in trimmedLines:
        lineParts = currLine.split(':')
        currValue = '{?unk?}'
        if len(lineParts) >= 2:
            currValue = lineParts[1].lstrip().rstrip()
        if 'Vendor' in currLine:
            cpu_vendor = currValue
        if 'Model name' in currLine:
            cpu_model = currValue
        if 'Architecture' in currLine:
            cpu_architecture = currValue
        if 'BogoMIPS' in currLine:
            cpu_bogomips = float(currValue)
        if 'CPU' in currLine:
            cpu_cores = int(currValue)
    # Tuple (Hardware, Model Name, NbrCores, BogoMIPS, Serial)
    rpi_cpu_tuple = ( cpu_vendor, cpu_model, cpu_architecture, cpu_bogomips, cpu_cores )
    print_line('rpi_cpu_tuple=[{}]'.format(rpi_cpu_tuple), debug=True)

def getDeviceMemory():
    global rpi_memory_tuple
    #  $ cat /proc/meminfo | egrep -i "mem[TFA]"
    #  MemTotal:         948304 kB
    #  MemFree:           40632 kB
    #  MemAvailable:     513332 kB
    out = subprocess.Popen("cat /proc/meminfo | egrep -i 'mem[tfa]'",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    lines = stdout.decode('utf-8').split("\n")
    trimmedLines = []
    for currLine in lines:
        trimmedLine = currLine.lstrip().rstrip()
        trimmedLines.append(trimmedLine)
    mem_total = ''
    mem_free = ''
    mem_avail = ''
    for currLine in trimmedLines:
        lineParts = currLine.split()
        if 'MemTotal' in currLine:
            mem_total = float(lineParts[1]) / 1024
        if 'MemFree' in currLine:
            mem_free = float(lineParts[1]) / 1024
        if 'MemAvail' in currLine:
            mem_avail = float(lineParts[1]) / 1024
    # Tuple (Total, Free, Avail.)
    rpi_memory_tuple = ( mem_total, mem_free, mem_avail )
    print_line('rpi_memory_tuple=[{}]'.format(rpi_memory_tuple), debug=True)

def getDeviceModel():
    global rpi_model
    global rpi_model_raw
    global rpi_connections
    out = subprocess.Popen("/bin/cat /proc/device-tree/model | /bin/sed -e 's/\\x0//g'",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    rpi_model_raw = stdout.decode('utf-8')
    # now reduce string length (just more compact, same info)
    rpi_model = rpi_model_raw.replace('Raspberry ', 'R').replace('i Model ', 'i 1 Model').replace('Rev ', 'r').replace(' Plus ', '+')

    # now decode interfaces
    rpi_connections = 'Ethernet, Wireless, Bluetooth' # default
    if 'Pi 3 ' in rpi_model:
        if ' A ' in rpi_model:
            rpi_connections = 'Wireless, Bluetooth'
        else:
            rpi_connections = 'Ethernet, Wireless, Bluetooth'
    elif 'Pi 2 ' in rpi_model:
        rpi_connections = 'Ethernet'
    elif 'Pi 1 ' in rpi_model:
        if ' A ' in rpi_model:
            rpi_connections = ''
        else:
            rpi_connections = 'Ethernet'

    print_line('rpi_model_raw=[{}]'.format(rpi_model_raw), debug=True)
    print_line('rpi_model=[{}]'.format(rpi_model), debug=True)
    print_line('rpi_connections=[{}]'.format(rpi_connections), debug=True)

def getLinuxRelease():
    global rpi_linux_release
    out = subprocess.Popen("/usr/bin/lsb_release -d -s",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    rpi_linux_release = stdout.decode('utf-8').rstrip()
    print_line('rpi_linux_release=[{}]'.format(rpi_linux_release), debug=True)

def getLinuxVersion():
    global rpi_linux_version
    out = subprocess.Popen("/bin/uname -r",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    rpi_linux_version = stdout.decode('utf-8').rstrip()
    print_line('rpi_linux_version=[{}]'.format(rpi_linux_version), debug=True)

def getHostnames():
    global rpi_hostname
    global rpi_fqdn
    out = subprocess.Popen("/bin/hostname -f",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    fqdn_raw = stdout.decode('utf-8').rstrip()
    print_line('fqdn_raw=[{}]'.format(fqdn_raw), debug=True)
    rpi_hostname = fqdn_raw
    if '.' in fqdn_raw:
        # have good fqdn
        nameParts = fqdn_raw.split('.')
        rpi_fqdn = fqdn_raw
        rpi_hostname = nameParts[0]
    else:
        # missing domain, if we have a fallback apply it
        if len(fallback_domain) > 0:
            rpi_fqdn = '{}.{}'.format(fqdn_raw, fallback_domain)
        else:
            rpi_fqdn = rpi_hostname

    print_line('rpi_fqdn=[{}]'.format(rpi_fqdn), debug=True)
    print_line('rpi_hostname=[{}]'.format(rpi_hostname), debug=True)

def getUptimeAndLoad():
    global rpi_uptime_raw
    global rpi_uptime
    global rpi_load_1m
    global rpi_load_5m
    global rpi_load_15m
    out = subprocess.Popen("/usr/bin/uptime",
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    rpi_uptime_raw = stdout.decode('utf-8').rstrip().lstrip()
    print_line('rpi_uptime_raw=[{}]'.format(rpi_uptime_raw), debug=True)
    basicParts = rpi_uptime_raw.split()
    timeStamp = basicParts[0]
    lineParts = rpi_uptime_raw.split(',')
    if('user' in lineParts[1]):
        rpi_uptime_raw = lineParts[0]
        rpi_load_1m = lineParts[2].lstrip().split(':')[1]
        rpi_load_5m = lineParts[3]
        rpi_load_15m = lineParts[4]
    else:
        rpi_uptime_raw = '{}, {}'.format(lineParts[0], lineParts[1])
        rpi_load_1m = lineParts[3].lstrip().split(':')[1]
        rpi_load_5m = lineParts[4]
        rpi_load_15m = lineParts[5]
    rpi_uptime = rpi_uptime_raw.replace(timeStamp, '').lstrip().replace('up ', '')
    print_line('rpi_load_1m=[{}]'.format(rpi_load_1m), debug=True)
    print_line('rpi_load_5m=[{}]'.format(rpi_load_5m), debug=True)
    print_line('rpi_load_15m=[{}]'.format(rpi_load_15m), debug=True)
    print_line('rpi_uptime=[{}]'.format(rpi_uptime), debug=True)

def getNetworkIFsUsingIP(ip_cmd):
    cmd_str = '{} link show | /bin/egrep -v "link" | /bin/egrep " eth| wlan"'.format(ip_cmd)
    out = subprocess.Popen(cmd_str,
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    lines = stdout.decode('utf-8').split("\n")
    interfaceNames = []
    line_count = len(lines)
    if line_count > 2:
        line_count = 2;
    if line_count == 0:
        print_line('ERROR no lines left by ip(8) filter!',error=True)
        sys.exit(1)

    for lineIdx in range(line_count):
        trimmedLine = lines[lineIdx].lstrip().rstrip()
        if len(trimmedLine) > 0:
            lineParts = trimmedLine.split()
            interfaceName = lineParts[1].replace(':', '')
            interfaceNames.append(interfaceName)

    print_line('interfaceNames=[{}]'.format(interfaceNames), debug=True)

    trimmedLines = []
    for interface in interfaceNames:
        lines = getSingleInterfaceDetails(interface)
        for currLine in lines:
            trimmedLines.append(currLine)

    loadNetworkIFDetailsFromLines(trimmedLines)

def getSingleInterfaceDetails(interfaceName):
    cmdString = '/sbin/ifconfig {} | /bin/egrep "Link|flags|inet |ether " | /bin/egrep -v -i "lo:|loopback|inet6|\:\:1|127\.0\.0\.1"'.format(interfaceName)
    out = subprocess.Popen(cmdString,
           shell=True,
           stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    lines = stdout.decode('utf-8').split("\n")
    trimmedLines = []
    for currLine in lines:
        trimmedLine = currLine.lstrip().rstrip()
        if len(trimmedLine) > 0:
            trimmedLines.append(trimmedLine)

    #print_line('interface:[{}] trimmedLines=[{}]'.format(interfaceName, trimmedLines), debug=True)
    return trimmedLines

def loadNetworkIFDetailsFromLines(ifConfigLines):
    global rpi_interfaces
    global rpi_mac
       #
    # OLDER SYSTEMS
    #  eth0      Link encap:Ethernet  HWaddr b8:27:eb:c8:81:f2
    #    inet addr:192.168.100.41  Bcast:192.168.100.255  Mask:255.255.255.0
    #  wlan0     Link encap:Ethernet  HWaddr 00:0f:60:03:e6:dd
    # NEWER SYSTEMS
    #  The following means eth0 (wired is NOT connected, and WiFi is connected)
    #  eth0: flags=4099<UP,BROADCAST,MULTICAST>  mtu 1500
    #    ether b8:27:eb:1a:f3:bc  txqueuelen 1000  (Ethernet)
    #  wlan0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
    #    inet 192.168.100.189  netmask 255.255.255.0  broadcast 192.168.100.255
    #    ether b8:27:eb:4f:a6:e9  txqueuelen 1000  (Ethernet)
    #
    tmpInterfaces = []
    haveIF = False
    imterfc = ''
    rpi_mac = ''
    for currLine in ifConfigLines:
        lineParts = currLine.split()
        #print_line('- currLine=[{}]'.format(currLine), debug=True)
        #print_line('- lineParts=[{}]'.format(lineParts), debug=True)
        if len(lineParts) > 0:
            # skip interfaces generated by Home Assistant on RPi
            if 'docker' in currLine or 'veth' in currLine or 'hassio' in currLine:
                haveIF = False
                continue
            # let's evaluate remaining interfaces
            if 'flags' in currLine:  # NEWER ONLY
                haveIF = True
                imterfc = lineParts[0].replace(':', '')
                #print_line('newIF=[{}]'.format(imterfc), debug=True)
            elif 'Link' in currLine:  # OLDER ONLY
                haveIF = True
                imterfc = lineParts[0].replace(':', '')
                newTuple = (imterfc, 'mac', lineParts[4])
                if rpi_mac == '':
                    rpi_mac = lineParts[4]
                    print_line('rpi_mac=[{}]'.format(rpi_mac), debug=True)
                tmpInterfaces.append(newTuple)
                print_line('newTuple=[{}]'.format(newTuple), debug=True)
            elif haveIF == True:
                print_line('IF=[{}], lineParts=[{}]'.format(imterfc, lineParts), debug=True)
                if 'inet' in currLine:  # OLDER & NEWER
                    newTuple = (imterfc, 'IP', lineParts[1].replace('addr:',''))
                    tmpInterfaces.append(newTuple)
                    print_line('newTuple=[{}]'.format(newTuple), debug=True)
                elif 'ether' in currLine: # NEWER ONLY
                    newTuple = (imterfc, 'mac', lineParts[1])
                    tmpInterfaces.append(newTuple)
                    if rpi_mac == '':
                        rpi_mac = lineParts[1]
                        print_line('rpi_mac=[{}]'.format(rpi_mac), debug=True)
                    print_line('newTuple=[{}]'.format(newTuple), debug=True)
                    haveIF = False

    rpi_interfaces = tmpInterfaces
    print_line('rpi_interfaces=[{}]'.format(rpi_interfaces), debug=True)
    print_line('rpi_mac=[{}]'.format(rpi_mac), debug=True)

def getNetworkIFs():
    ip_cmd = getIPCmd()
    if ip_cmd != '':
        getNetworkIFsUsingIP(ip_cmd)
    else:
        out = subprocess.Popen('/sbin/ifconfig | /bin/egrep "Link|flags|inet |ether " | /bin/egrep -v -i "lo:|loopback|inet6|\:\:1|127\.0\.0\.1"',
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        stdout,_ = out.communicate()
        lines = stdout.decode('utf-8').split("\n")
        trimmedLines = []
        for currLine in lines:
            trimmedLine = currLine.lstrip().rstrip()
            if len(trimmedLine) > 0:
                trimmedLines.append(trimmedLine)

        print_line('trimmedLines=[{}]'.format(trimmedLines), debug=True)

        loadNetworkIFDetailsFromLines(trimmedLines)


def getFileSystemDrives():
    global rpi_filesystem_space_raw
    global rpi_filesystem_space
    global rpi_filesystem_percent
    out = subprocess.Popen("/bin/df -m | /usr/bin/tail -n +2 | /bin/egrep -v 'tmpfs|boot'",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    lines = stdout.decode('utf-8').split("\n")
    trimmedLines = []
    for currLine in lines:
        trimmedLine = currLine.lstrip().rstrip()
        if len(trimmedLine) > 0:
            trimmedLines.append(trimmedLine)

    print_line('getFileSystemDrives() trimmedLines=[{}]'.format(trimmedLines), debug=True)

    #  EXAMPLES
    #
    #  Filesystem     1M-blocks  Used Available Use% Mounted on
    #  /dev/root          59998   9290     48208  17% /
    #  /dev/sda1         937872 177420    712743  20% /media/data
    # or
    #  /dev/root          59647  3328     53847   6% /
    #  /dev/sda1           3703    25      3472   1% /media/pi/SANDISK
    # or
    #  xxx.xxx.xxx.xxx:/srv/c2db7b94 200561 148655 41651 79% /

    # FAILING Case v1.4.0:
    # Here is the output of 'df -m'

    # Sys. de fichiers blocs de 1M Utilisé Disponible Uti% Monté sur
    # /dev/root 119774 41519 73358 37% /
    # devtmpfs 1570 0 1570 0% /dev
    # tmpfs 1699 0 1699 0% /dev/shm
    # tmpfs 1699 33 1667 2% /run
    # tmpfs 5 1 5 1% /run/lock
    # tmpfs 1699 0 1699 0% /sys/fs/cgroup
    # /dev/mmcblk0p1 253 55 198 22% /boot
    # tmpfs 340 0 340 0% /run/user/1000

    for currLine in trimmedLines:
        lineParts = currLine.split()
        print_line('lineParts({})=[{}]'.format(len(lineParts), lineParts), debug=True)
        if len(lineParts) < 6:
            print_line('BAD LINE FORMAT, Skipped=[{}]'.format(lineParts), debug=True, warning=True)
            continue
        # tuple { total blocks, used%, mountPoint, device }
        #
        # new mech:
        #  Filesystem     1M-blocks  Used Available Use% Mounted on
        #     [0]           [1]       [2]     [3]    [4]   [5]
        #     [--]         [n-3]     [n-2]   [n-1]   [n]   [--]
        #  where  percent_field_index  is 'n'
        #

        # locate our % used field...
        for percent_field_index in range(len(lineParts) - 2, 1, -1):
            if '%' in lineParts[percent_field_index]:
                break;
        print_line('percent_field_index=[{}]'.format(percent_field_index), debug=True)

        total_size_idx = percent_field_index - 3
        mount_idx = percent_field_index + 1

        # do we have a two part device name?
        device = lineParts[0]
        if total_size_idx != 1:
            device = '{} {}'.format(lineParts[0], lineParts[1])
        print_line('device=[{}]'.format(device), debug=True)

        # do we have a two part mount point?
        mount_point = lineParts[mount_idx]
        if len(lineParts) - 1 > mount_idx:
            mount_point = '{} {}'.format(lineParts[mount_idx], lineParts[mount_idx + 1])
        print_line('mount_point=[{}]'.format(mount_point), debug=True)

        total_size = '{:.0f}'.format(next_power_of_2(lineParts[total_size_idx]))
        newTuple = ( total_size, lineParts[percent_field_index].replace('%',''),  mount_point, device )
        print_line('newTuple=[{}]'.format(newTuple), debug=True)
        if newTuple[2] == '/':
            rpi_filesystem_space_raw = currLine
            rpi_filesystem_space = newTuple[0]
            rpi_filesystem_percent = newTuple[1]
            print_line('rpi_filesystem_space=[{}GB]'.format(newTuple[0]), debug=True)
            print_line('rpi_filesystem_percent=[{}]'.format(newTuple[1]), debug=True)

def next_power_of_2(size):
    size_as_nbr = int(size) - 1
    return 1 if size == 0 else (1<<size_as_nbr.bit_length()) / 1024

def getIPCmd():
    cmd_locn1 = '/bin/ip'
    cmd_locn2 = '/sbin/ip'
    desiredCommand = ''
    if os.path.exists(cmd_locn1) == True:
        desiredCommand = cmd_locn1
    elif os.path.exists(cmd_locn2) == True:
        desiredCommand = cmd_locn2
    if desiredCommand != '':
        print_line('Found IP(8)=[{}]'.format(desiredCommand), debug=True)
    return desiredCommand

def getSystemTemperature():
    global rpi_cpu_temp

    out = subprocess.Popen("/bin/cat /sys/class/thermal/thermal_zone0/temp",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    rpi_cpu_temp_raw = stdout.decode('utf-8').rstrip()
    rpi_cpu_temp = float(rpi_cpu_temp_raw) / 1000.0
    print_line('rpi_cpu_temp=[{}]'.format(rpi_cpu_temp), debug=True)

def getLastUpdateDate():
    global rpi_last_update_date
    # apt-get update writes to following dir (so date changes on update)
    apt_listdir_filespec = '/var/lib/apt/lists/partial'
    # apt-get dist-upgrade | autoremove update the following file when actions are taken
    apt_lockdir_filespec = '/var/lib/dpkg/lock'
    cmdString = '/bin/ls -ltrd {} {}'.format(apt_listdir_filespec, apt_lockdir_filespec)
    out = subprocess.Popen(cmdString,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    lines = stdout.decode('utf-8').split("\n")
    trimmedLines = []
    for currLine in lines:
        trimmedLine = currLine.lstrip().rstrip()
        if len(trimmedLine) > 0:
            trimmedLines.append(trimmedLine)
    print_line('trimmedLines=[{}]'.format(trimmedLines), debug=True)

    fileSpec_latest = ''
    if len(trimmedLines) > 0:
        lastLineIdx = len(trimmedLines) - 1
        lineParts = trimmedLines[lastLineIdx].split()
        if len(lineParts) > 0:
            lastPartIdx = len(lineParts) - 1
            fileSpec_latest = lineParts[lastPartIdx]
    print_line('fileSpec_latest=[{}]'.format(fileSpec_latest), debug=True)

    fileModDateInSeconds = os.path.getmtime(fileSpec_latest)
    fileModDate = datetime.fromtimestamp(fileModDateInSeconds)
    rpi_last_update_date = fileModDate.replace(tzinfo=local_tz)
    print_line('rpi_last_update_date=[{}]'.format(rpi_last_update_date), debug=True)

def to_datetime(time):
    return datetime.fromordinal(int(time)) + datetime.timedelta(time % 1)

def getLastInstallDate():
    global rpi_last_update_date
    #apt_log_filespec = '/var/log/dpkg.log'
    #apt_log_filespec2 = '/var/log/dpkg.log.1'
    out = subprocess.Popen("/bin/grep --binary-files=text 'status installed' /var/log/dpkg.log /var/log/dpkg.log.1 2>/dev/null | sort | tail -1",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
    stdout,_ = out.communicate()
    last_installed_pkg_raw = stdout.decode('utf-8').rstrip().replace('/var/log/dpkg.log:','').replace('/var/log/dpkg.log.1:','')
    print_line('last_installed_pkg_raw=[{}]'.format(last_installed_pkg_raw), debug=True)
    line_parts = last_installed_pkg_raw.split()
    if len(line_parts) > 1:
        pkg_date_string = '{} {}'.format(line_parts[0], line_parts[1])
        print_line('pkg_date_string=[{}]'.format(pkg_date_string), debug=True)
        # Example:
        #   2020-07-22 17:08:26 status installed python3-tzlocal:all 1.3-1

        pkg_install_date = datetime.strptime(pkg_date_string, '%Y-%m-%d %H:%M:%S').replace(tzinfo=local_tz)
        rpi_last_update_date  = pkg_install_date

    print_line('rpi_last_update_date=[{}]'.format(rpi_last_update_date), debug=True)



# get our hostnames so we can setup MQTT
getHostnames()
if(sensor_name == default_sensor_name):
    sensor_name = 'rpi-{}'.format(rpi_hostname)
# get model so we can use it too in MQTT
getDeviceModel()
getDeviceCpuInfo()
getLinuxRelease()
getLinuxVersion()
getFileSystemDrives()

# -----------------------------------------------------------------------------
#  timer and timer funcs for ALIVE MQTT Notices handling
# -----------------------------------------------------------------------------

ALIVE_TIMOUT_IN_SECONDS = 60

def publishAliveStatus():
    print_line('- SEND: yes, still alive -', debug=True)
    mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)

def aliveTimeoutHandler():
    print_line('- MQTT TIMER INTERRUPT -', debug=True)
    _thread.start_new_thread(publishAliveStatus, ())
    startAliveTimer()

def startAliveTimer():
    global aliveTimer
    global aliveTimerRunningStatus
    stopAliveTimer()
    aliveTimer = threading.Timer(ALIVE_TIMOUT_IN_SECONDS, aliveTimeoutHandler)
    aliveTimer.start()
    aliveTimerRunningStatus = True
    print_line('- started MQTT timer - every {} seconds'.format(ALIVE_TIMOUT_IN_SECONDS), debug=True)

def stopAliveTimer():
    global aliveTimer
    global aliveTimerRunningStatus
    aliveTimer.cancel()
    aliveTimerRunningStatus = False
    print_line('- stopped MQTT timer', debug=True)

def isAliveTimerRunning():
    global aliveTimerRunningStatus
    return aliveTimerRunningStatus

# our ALIVE TIMER
aliveTimer = threading.Timer(ALIVE_TIMOUT_IN_SECONDS, aliveTimeoutHandler)
# our BOOL tracking state of ALIVE TIMER
aliveTimerRunningStatus = False



# -----------------------------------------------------------------------------
#  MQTT setup and startup
# -----------------------------------------------------------------------------

# MQTT connection
lwt_topic = '{}/sensor/{}/status'.format(base_topic, sensor_name.lower())
lwt_online_val = 'online'
lwt_offline_val = 'offline'

print_line('Connecting to MQTT broker ...', verbose=True)
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish



mqtt_client.will_set(lwt_topic, payload=lwt_offline_val, retain=True)

if config['MQTT'].getboolean('tls', False):
    # According to the docs, setting PROTOCOL_SSLv23 "Selects the highest protocol version
    # that both the client and server support. Despite the name, this option can select
    # “TLS” protocols as well as “SSL”" - so this seems like a resonable default
    mqtt_client.tls_set(
        ca_certs=config['MQTT'].get('tls_ca_cert', None),
        keyfile=config['MQTT'].get('tls_keyfile', None),
        certfile=config['MQTT'].get('tls_certfile', None),
        tls_version=ssl.PROTOCOL_SSLv23
    )

mqtt_username = os.environ.get("MQTT_USERNAME", config['MQTT'].get('username'))
mqtt_password = os.environ.get("MQTT_PASSWORD", config['MQTT'].get('password', None))

if mqtt_username:
    mqtt_client.username_pw_set(mqtt_username, mqtt_password)
try:
    mqtt_client.connect(os.environ.get('MQTT_HOSTNAME', config['MQTT'].get('hostname', 'localhost')),
                        port=int(os.environ.get('MQTT_PORT', config['MQTT'].get('port', '1883'))),
                        keepalive=config['MQTT'].getint('keepalive', 60))
except:
    print_line('MQTT connection error. Please check your settings in the configuration file "config.ini"', error=True, sd_notify=True)
    sys.exit(1)
else:
    mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)
    mqtt_client.loop_start()

    while mqtt_client_connected == False: #wait in loop
        print_line('* Wait on mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
        sleep(1.0) # some slack to establish the connection

    startAliveTimer()

sd_notifier.notify('READY=1')


# -----------------------------------------------------------------------------
#  Perform our MQTT Discovery Announcement...
# -----------------------------------------------------------------------------

# what RPi device are we on?
# get our hostnames so we can setup MQTT
getNetworkIFs() # this will fill-in rpi_mac

mac_basic = rpi_mac.lower().replace(":", "")
mac_left = mac_basic[:6]
mac_right = mac_basic[6:]
print_line('mac lt=[{}], rt=[{}], mac=[{}]'.format(mac_left, mac_right, mac_basic), debug=True)
uniqID = "RPi-{}Mon{}".format(mac_left, mac_right)

# our RPi Reporter device
LD_MONITOR = "monitor" # KeyError: 'home310/sensor/rpi-pi3plus/values' let's not use this 'values' as topic
LD_SYS_TEMP= "temperature"
LD_FS_USED = "disk_used"
LDS_PAYLOAD_NAME = "info"

# Publish our MQTT auto discovery
#  table of key items to publish:
detectorValues = OrderedDict([
    (LD_MONITOR, dict(title="RPi Monitor {}".format(rpi_hostname), device_class="timestamp", no_title_prefix="yes", json_value="timestamp", json_attr="yes", icon='mdi:raspberry-pi', device_ident="RPi-{}".format(rpi_fqdn))),
    (LD_SYS_TEMP, dict(title="RPi Temp {}".format(rpi_hostname), device_class="temperature", no_title_prefix="yes", unit="C", json_value="temperature_c", icon='mdi:thermometer')),
    (LD_FS_USED, dict(title="RPi Used {}".format(rpi_hostname), no_title_prefix="yes", json_value="fs_free_prcnt", unit="%", icon='mdi:sd')),
])

print_line('Announcing RPi Monitoring device to MQTT broker for auto-discovery ...')

base_topic = '{}/sensor/{}'.format(base_topic, sensor_name.lower())
values_topic_rel = '{}/{}'.format('~', LD_MONITOR)
values_topic = '{}/{}'.format(base_topic, LD_MONITOR)
activity_topic_rel = '{}/status'.format('~')     # vs. LWT
activity_topic = '{}/status'.format(base_topic)    # vs. LWT

command_topic_rel = '~/set'

for [sensor, params] in detectorValues.items():
    discovery_topic = '{}/sensor/{}/{}/config'.format(discovery_prefix, sensor_name.lower(), sensor)
    payload = OrderedDict()
    if 'no_title_prefix' in params:
        payload['name'] = "{}".format(params['title'].title())
    else:
        payload['name'] = "{} {}".format(sensor_name.title(), params['title'].title())
    payload['uniq_id'] = "{}_{}".format(uniqID, sensor.lower())
    if 'device_class' in params:
        payload['dev_cla'] = params['device_class']
    if 'unit' in params:
        payload['unit_of_measurement'] = params['unit']
    if 'json_value' in params:
        payload['stat_t'] = values_topic_rel
        payload['val_tpl'] = "{{{{ value_json.{}.{} }}}}".format(LDS_PAYLOAD_NAME, params['json_value'])
    payload['~'] = base_topic
    payload['pl_avail'] = lwt_online_val
    payload['pl_not_avail'] = lwt_offline_val
    if 'icon' in params:
        payload['ic'] = params['icon']
    payload['avty_t'] = activity_topic_rel
    if 'json_attr' in params:
        payload['json_attr_t'] = values_topic_rel
        payload['json_attr_tpl'] = '{{{{ value_json.{} | tojson }}}}'.format(LDS_PAYLOAD_NAME)
    if 'device_ident' in params:
        payload['dev'] = {
                'identifiers' : ["{}".format(uniqID)],
                'manufacturer' : 'Raspberry Pi (Trading) Ltd.',
                'name' : params['device_ident'],
                'model' : '{}'.format(rpi_model),
                'sw_version': "{} {}".format(rpi_linux_release, rpi_linux_version)
        }
    else:
         payload['dev'] = {
                'identifiers' : ["{}".format(uniqID)],
         }
    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)

    # remove connections as test:                  'connections' : [["mac", mac.lower()], [interface, ipaddr]],

# -----------------------------------------------------------------------------
#  timer and timer funcs for period handling
# -----------------------------------------------------------------------------

TIMER_INTERRUPT = (-1)
TEST_INTERRUPT = (-2)

def periodTimeoutHandler():
    print_line('- PERIOD TIMER INTERRUPT -', debug=True)
    handle_interrupt(TIMER_INTERRUPT) # '0' means we have a timer interrupt!!!
    startPeriodTimer()

def startPeriodTimer():
    global endPeriodTimer
    global periodTimeRunningStatus
    stopPeriodTimer()
    endPeriodTimer = threading.Timer(interval_in_minutes * 60.0, periodTimeoutHandler)
    endPeriodTimer.start()
    periodTimeRunningStatus = True
    print_line('- started PERIOD timer - every {} seconds'.format(interval_in_minutes * 60.0), debug=True)

def stopPeriodTimer():
    global endPeriodTimer
    global periodTimeRunningStatus
    endPeriodTimer.cancel()
    periodTimeRunningStatus = False
    print_line('- stopped PERIOD timer', debug=True)

def isPeriodTimerRunning():
    global periodTimeRunningStatus
    return periodTimeRunningStatus

# our TIMER
endPeriodTimer = threading.Timer(interval_in_minutes * 60.0, periodTimeoutHandler)
# our BOOL tracking state of TIMER
periodTimeRunningStatus = False
reported_first_time = False

# -----------------------------------------------------------------------------
#  MQTT Transmit Helper Routines
# -----------------------------------------------------------------------------
SCRIPT_TIMESTAMP = "timestamp"
RPI_MODEL = "rpi_model"
RPI_CONNECTIONS = "ifaces"
RPI_HOSTNAME = "host_name"
RPI_FQDN = "fqdn"
RPI_LINUX_RELEASE = "os_release"
RPI_LINUX_VERSION = "os_kernel_version"
RPI_UPTIME = "up_time"
RPI_LOAD_1M = "load_1m"
RPI_LOAD_5M = "load_5m"
RPI_LOAD_15M = "load_15m"
RPI_DATE_LAST_UPDATE = "last_update"
RPI_FS_SPACE = 'root_fs_total'
RPI_FS_AVAIL = 'root_fs_used_percent'
RPI_CPU_TEMP = "cpu_temperature"
RPI_SCRIPT = "reporter"
RPI_INTERFACE = "interface"
SCRIPT_REPORT_INTERVAL = "report_interval"
# new memory dictionary
RPI_MEM_TOTAL = "memory_size"
RPI_MEM_AVAIL = "memory_available"
RPI_MEM_FREE = "memory_free"
# Tuple (Hardware, Model Name, NbrCores, Serial)
RPI_CPU_VENDOR = "cpu_vendor"
RPI_CPU_MODEL = "cpu_model"
RPI_CPU_ARCHITECTURE= "cpu_architecture"
RPI_CPU_BOGOMIPS = "cpu_bogomips"
RPI_CPU_CORES = "cpu_number_of_cores"

def send_status(timestamp, nothing):
    rpiData = OrderedDict()
    rpiData[SCRIPT_TIMESTAMP] = timestamp.astimezone().replace(microsecond=0).isoformat()
    rpiData[RPI_MODEL] = rpi_model
    rpiData[RPI_CONNECTIONS] = rpi_connections
    rpiData[RPI_HOSTNAME] = rpi_hostname
    rpiData[RPI_FQDN] = rpi_fqdn
    rpiData[RPI_LINUX_RELEASE] = rpi_linux_release
    rpiData[RPI_LINUX_VERSION] = rpi_linux_version
    rpiData[RPI_UPTIME] = rpi_uptime

    rpiData[RPI_LOAD_1M] = rpi_load_1m
    rpiData[RPI_LOAD_5M] = rpi_load_5m
    rpiData[RPI_LOAD_15M] = rpi_load_15m

    #  DON'T use V1 form of getting date (my dashbord mech)
    #actualDate = datetime.strptime(rpi_last_update_date, '%y%m%d%H%M%S')
    #actualDate.replace(tzinfo=local_tz)
    #rpiData[RPI_DATE_LAST_UPDATE] = actualDate.astimezone().replace(microsecond=0).isoformat()
    # also don't use V2 form...
    #if rpi_last_update_date_v2 != datetime.min:
    #    rpiData[RPI_DATE_LAST_UPDATE] = rpi_last_update_date_v2.astimezone().replace(microsecond=0).isoformat()
    #else:
    #    rpiData[RPI_DATE_LAST_UPDATE] = ''
    if rpi_last_update_date != datetime.min:
        rpiData[RPI_DATE_LAST_UPDATE] = rpi_last_update_date.astimezone().replace(microsecond=0).isoformat()
    else:
        rpiData[RPI_DATE_LAST_UPDATE] = ''
    rpiData[RPI_FS_SPACE] = rpi_filesystem_space.replace('GB', '')+' GB'
    rpiData[RPI_FS_AVAIL] = rpi_filesystem_percent+' %'

    rpiRam = getMemoryDictionary()
    if len(rpiRam) > 0:
        rpiData[RPI_MEM_TOTAL] = rpiRam[RPI_MEM_TOTAL]+' MB'
        rpiData[RPI_MEM_AVAIL] = rpiRam[RPI_MEM_AVAIL]+' MB'
        rpiData[RPI_MEM_FREE] = rpiRam[RPI_MEM_FREE]+' MB'

    rpiCpu = getCPUDictionary()
    if len(rpiCpu) > 0:
        rpiData[RPI_CPU_VENDOR] = rpiCpu[RPI_CPU_VENDOR]
        rpiData[RPI_CPU_MODEL] = rpiCpu[RPI_CPU_MODEL]
        rpiData[RPI_CPU_ARCHITECTURE] = rpiCpu[RPI_CPU_ARCHITECTURE]
        rpiData[RPI_CPU_BOGOMIPS] = rpiCpu[RPI_CPU_BOGOMIPS]
        rpiData[RPI_CPU_CORES] = rpiCpu[RPI_CPU_CORES]

    rpiData[RPI_CPU_TEMP] = forceSingleDigit(rpi_cpu_temp)+' °C'

    rpiData[RPI_SCRIPT] = rpi_mqtt_script.replace('.py', '')
    rpiData[SCRIPT_REPORT_INTERVAL] = interval_in_minutes

    rpiTopDict = OrderedDict()
    rpiTopDict[LDS_PAYLOAD_NAME] = rpiData

    _thread.start_new_thread(publishMonitorData, (rpiTopDict, values_topic))

def forceSingleDigit(temperature):
    tempInterp = '{:.1f}'.format(temperature)
    return tempInterp

def getMemoryDictionary():
    # TYPICAL:
    #   Tuple (Total, Free, Avail.)
    memoryData = OrderedDict()
    if rpi_memory_tuple != '':
        memoryData[RPI_MEM_TOTAL] = '{:.2f}'.format(rpi_memory_tuple[0])
        memoryData[RPI_MEM_FREE] = '{:.2f}'.format(rpi_memory_tuple[1])
        memoryData[RPI_MEM_AVAIL] = '{:.2f}'.format(rpi_memory_tuple[2])
    #print_line('memoryData:{}"'.format(memoryData), debug=True)
    return memoryData

def getCPUDictionary():
    # TYPICAL:
    #   Tuple (Hardware, Model Name, NbrCores, BogoMIPS, Serial)
    cpuDict = OrderedDict()
    #print_line('rpi_cpu_tuple:{}"'.format(rpi_cpu_tuple), debug=True)
    if rpi_cpu_tuple != '':
        cpuDict[RPI_CPU_VENDOR] = rpi_cpu_tuple[0]
        cpuDict[RPI_CPU_MODEL] = rpi_cpu_tuple[1]
        cpuDict[RPI_CPU_ARCHITECTURE] = rpi_cpu_tuple[2]
        cpuDict[RPI_CPU_BOGOMIPS] = rpi_cpu_tuple[3]
        cpuDict[RPI_CPU_CORES] = rpi_cpu_tuple[4]

    print_line('cpuDict:{}"'.format(cpuDict), debug=True)
    return cpuDict

def publishMonitorData(latestData, topic):
    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(topic, json.dumps(latestData)))
    mqtt_client.publish('{}'.format(topic), json.dumps(latestData), 1, retain=False)
    sleep(0.5) # some slack for the publish roundtrip and callback function


def update_values():
    # nothing here yet
    getUptimeAndLoad()
    getFileSystemDrives()
    getSystemTemperature()
    getLastUpdateDate()
    getDeviceMemory()

# -----------------------------------------------------------------------------

# Interrupt handler
def handle_interrupt(channel):
    global reported_first_time
    sourceID = "<< INTR(" + str(channel) + ")"
    current_timestamp = datetime.now(local_tz)
    print_line(sourceID + " >> Time to report! (%s)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'), verbose=True)
    # ----------------------------------
    # have PERIOD interrupt!
    update_values()

    if (opt_stall == False or reported_first_time == False and opt_stall == True):
        # ok, report our new detection to MQTT
        _thread.start_new_thread(send_status, (current_timestamp, ''))
        reported_first_time = True
    else:
        print_line(sourceID + " >> Time to report! (%s) but SKIPPED (TEST: stall)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'), verbose=True)

def afterMQTTConnect():
    print_line('* afterMQTTConnect()', verbose=True)
    #  NOTE: this is run after MQTT connects
    # start our interval timer
    startPeriodTimer()
    # do our first report
    handle_interrupt(0)

# TESTING AGAIN
#getNetworkIFs()
#getLastUpdateDate()

# TESTING, early abort
#stopAliveTimer()
#exit(0)

afterMQTTConnect()  # now instead of after?

# now just hang in forever loop until script is stopped externally
try:
    while True:
        #  our INTERVAL timer does the work
        sleep(10000)

finally:
    # cleanup used pins... just because we like cleaning up after us
    stopPeriodTimer()   # don't leave our timers running!
    stopAliveTimer()
