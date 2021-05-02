# RPi Reporter MQTT2HA Daemon

![Project Maintenance][maintenance-shield]

[![GitHub Activity][commits-shield]][commits]

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

[![GitHub Release][releases-shield]][releases]

A simple Linux python script to query the Raspberry Pi on which it is running for various configuration and status values which it then reports via via [MQTT](https://projects.eclipse.org/projects/iot.mosquitto) to your [Home Assistant](https://www.home-assistant.io/) installation.  This allows you to install and run this on each of your RPi's so you can track them all via your own Home Assistant Dashboard.

![Discovery image](./Docs/images/DiscoveryV2.png)

This script should be configured to be run in **daemon mode** continously in the background as a systemd service (or optionally as a SysV init script).  Instructions are provided below.

*(Jump to below [Lovelace Custom Card](#lovelace-custom-card).)*

## Script Updates

We've been repairing this script as users report issues with it. For a list of fixes for each release see our [ChangeLog](./ChangeLog)

----

If you like my work and/or this has helped you in some way then feel free to help me out for a couple of :coffee:'s or :pizza: slices!

[![coffee](https://www.buymeacoffee.com/assets/img/custom_images/black_img.png)](https://www.buymeacoffee.com/ironsheep)

----

## Features

* Tested on Raspberry Pi's 2/3/4 with Jessie, Stretch and Buster
* Tested with Home Assistant v0.111.0
* Tested with Mosquitto broker v5.1
* Data is published via MQTT
* MQTT discovery messages are sent so RPi's are automatically registered with Home Assistant (if MQTT discovery is enabled in your HA installation)
* MQTT authentication support
* No special/root privileges are required by this mechanism
* Linux daemon / systemd service, sd\_notify messages generated

### RPi Device

Each RPi device is reported as:

| Name            | Description |
|-----------------|-------------|
| `Manufacturer`   | Raspberry Pi (Trading) Ltd. |
| `Model`         | RPi 4 Model B v1.1  |
| `Name`      | (fqdn) pimon1.home |
| `sofware ver`  | OS Name, Version (e.g., Buster v4.19.75v7l+) |

### RPi MQTT Topics

Each RPi device is reported as three topics:

| Name            | Device Class | Units | Description
|-----------------|-------------|-------------|-------------|
| `~/monitor`   | 'timestamp' | n/a | Is a timestamp which shows when the RPi last sent information, carries a template payload conveying all monitored values (attach the lovelace custom card to this sensor!)
| `~/temperature`   | 'temperature' | degrees C | Shows the latest system temperature
| `~/disk_used`   | none | percent (%) | Shows the amount of root file system used

### RPi Monitor Topic

The monitored topic reports the following information:

| Name            | Description
|-----------------|-------------
| `rpi_model` | tinyfied hardware version string |
| `ifaces`        | comma sep list of interfaces on board [w,e,b]
| `temperature_c`   | System temperature, in [°C] (0.1°C resolution) Note: this is GPU temp. if available, else CPU temp. |
| `temp_gpu_c`   | GPU temperature, in [°C] (0.1°C resolution) |
| `temp_cpu_c`   | CPU temperature, in [°C] (0.1°C resolution) |
| `up_time`      | duration since last booted, as [days] |
| `last_update`  | updates last applied, as [date] |
| `fs_total_gb`       | / total space in [GBytes] |
| `fs_free_prcnt`       | / free space [%] |
| `host_name`       | hostname |
| `fqdn`       | hostname.domain |
| `ux_release`       | os release name (e.g., buster) |
| `ux_version`       | os version (e.g., 4.19.66-v7+) |
| `reporter`  | script name, version running on RPi |
| `networking`       | lists for each interface: interface name, mac address (and IP if the interface is connected) |
| `drives`       | lists for each drive mounted: size in GB, % used, device and mount point |
| `cpu`       | lists the model of cpu, number of cores, etc. |
| `memory`       | shows the total amount of RAM in MB and the available ram in MB |
| `throttle`    | reports the throttle status value plus interpretation thereof |
| `docker_container_running_count` | number of running containers |
| `docker_container_total_count` | total number of containers (even stopped and exited) |
| `docker_network_total_count` | total number of docker networks |

### Docker integration

If docker is installed it will be automatically detected. To get data from the docker cli we must provide the RPI Monitor daemon the rights to use the docker cli.  
`sudo usermod -aG docker daemon`


## Prerequisites

An MQTT broker is needed as the counterpart for this daemon.

MQTT is huge help in connecting different parts of your smart home and setting up of a broker is quick and easy. In many cases you've already set one up when you installed Home Assistant.

## Installation

On a modern Linux system just a few steps are needed to get the daemon working.
The following example shows the installation under Debian/Raspbian below the `/opt` directory:

```shell
sudo apt-get install git python3 python3-pip python3-tzlocal python3-sdnotify python3-colorama python3-unidecode python3-paho-mqtt

sudo git clone https://github.com/ssiergl/RPi-Reporter-MQTT2HA-Daemon.git /opt/RPi-Reporter-MQTT2HA-Daemon

cd /opt/RPi-Reporter-MQTT2HA-Daemon
sudo pip3 install -r requirements.txt
```

## Configuration

To match personal needs, all operational details can be configured by modifying entries within the file [`config.ini`](config.ini.dist).
The file needs to be created first: (*in the following: if you don't have vim installed you might try nano*)

```shell
sudo cp /opt/RPi-Reporter-MQTT2HA-Daemon/config.{ini.dist,ini}
sudo vim /opt/RPi-Reporter-MQTT2HA-Daemon/config.ini
```

You will likely want to locate and configure the following (at a minimum) in your config.ini:

```shell
fallback_domain = {if you have older RPis that dont report their fqdn correctly}
# ...
hostname = {your-mqtt-broker}
# ...
discovery_prefix = {if you use something other than 'homeassistant'}
# ...
base_topic = {your home-assistant base topic}

# ...
username = {your mqtt username if your setup requires one}
password = {your mqtt password if your setup requires one}

```

Now that your config.ini is setup let's test!

## Execution

### Initial Test

A first test run is as easy as:

```shell
python3 /opt/RPi-Reporter-MQTT2HA-Daemon/ISP-RPi-mqtt-daemon.py
```

**NOTE:** *it is a good idea to execute this script by hand this way each time you modify the config.ini.  By running after each modification the script can tell you through error messages if it had any problems with any values in the config.ini file, or any missing values. etc.*``

Using the command line argument `--config`, a directory where to read the config.ini file from can be specified, e.g.

```shell
python3 /opt/RPi-Reporter-MQTT2HA-Daemon/ISP-RPi-mqtt-daemon.py --config /opt/RPi-Reporter-MQTT2HA-Daemon
```

### Preparing to run full time

In order to have your HA system know if your RPi is online/offline and when it last reported-in then you must set up this script to run as a system service.

**NOTE:** Daemon mode must be enabled in the configuration file (default).

But first, we need to grant access to some hardware for the user account under which the sevice will run.

### Set up daemon account to allow access to temperature values

By default this script is run as user:group  **daemon:daemon**.  As this script requires access to the GPU you'll want to add access to it for the daemon user as follows:

   ```shell
   # list current groups
   groups daemon
   $ daemon : daemon

   # add video if not present
   sudo usermod daemon -a -G video

   # list current groups
   groups daemon
   $ daemon : daemon video
   #                 ^^^^^ now it is present
   ```

### Choose Run Style

You can choose to run this script as a `systemd service` or as a `Sys V init script`. If you are on a newer OS than `Jessie` or if as a system admin you are just more comfortable with Sys V init scripts then you can use the latter style.

Let's look at how to set up each of these forms:

#### Run as Systemd Daemon / Service (*for Raspian/Raspberry pi OS newer than 'jessie'*)

(**Heads Up** *We've learned the hard way that RPi's running `jessie` won't restart the script on reboot if setup this way, Please set up these RPi's using the init script form shown in the next section.*)

Set up the script to be run as a system service as follows:

   ```shell
   sudo ln -s /opt/RPi-Reporter-MQTT2HA-Daemon/isp-rpi-reporter.service /etc/systemd/system/isp-rpi-reporter.service

   sudo systemctl daemon-reload

   # tell system that it can start our script at system startup during boot
   sudo systemctl enable isp-rpi-reporter.service
   
   # start the script running
   sudo systemctl start isp-rpi-reporter.service
   
   # check to make sure all is ok with the start up
   sudo systemctl status isp-rpi-reporter.service
   ```
   
**NOTE:** *Please remember to run the 'systemctl enable ...' once at first install, if you want your script to start up every time your RPi reboots!*

#### Run as Sys V init script (*your RPi is running 'jessie' or you just like this form*)

In this form our wrapper script located in the /etc/init.d directory and is run according to symbolic links in the `/etc/rc.x` directories.

Set up the script to be run as a Sys V init script as follows:

   ```shell
   sudo ln -s /opt/RPi-Reporter-MQTT2HA-Daemon/rpi-reporter /etc/init.d/rpi-reporter

	# configure system to start this script at boot time
   sudo update-rc.d rpi-reporter defaults

   # let's start the script now, too so we don't have to reboot
   sudo /etc/init.d/rpi-reporter start
  
   # check to make sure all is ok with the start up
   sudo /etc/init.d/rpi-reporter status
   ```
   
### Update to latest

Like most active developers, we periodically upgrade our script. Use one of the following list of update steps based upon how you are set up.

#### Systemd commands to perform update

If you are setup in the systemd form, you can update to the latest we've published by following these steps:

   ```shell
   # go to local repo
   cd /opt/RPi-Reporter-MQTT2HA-Daemon

   # stop the service
   sudo systemctl stop isp-rpi-reporter.service

   # get the latest version
   sudo git pull

   # reload the systemd configuration (in case it changed)
   sudo systemctl daemon-reload

   # restart the service with your new version
   sudo systemctl start isp-rpi-reporter.service

   # if you want, check status of the running script
   systemctl status isp-rpi-reporter.service

   ```
   
#### SysV init script commands to perform update

If you are setup in the Sys V init script form, you can update to the latest we've published by following these steps:

   ```shell
   # go to local repo
   cd /opt/RPi-Reporter-MQTT2HA-Daemon

   # stop the service
   sudo /etc/init.d/rpi-reporter stop

   # get the latest version
   sudo git pull

   # restart the service with your new version
   sudo /etc/init.d/rpi-reporter start

   # if you want, check status of the running script
   sudo /etc/init.d/rpi-reporter status

   ```

## Integration

When this script is running data will be published to the (configured) MQTT broker topic "`raspberrypi/{hostname}/...`" (e.g. `raspberrypi/picam01/...`).

An example:

```json
{
  "info": {
    "timestamp": "2020-08-29T17:43:38-06:00",
    "rpi_model": "RPi 3 Model B r1.2",
    "ifaces": "e,w,b",
    "host_name": "pi3plus",
    "fqdn": "pi3plus.home",
    "ux_release": "stretch",
    "ux_version": "4.19.66-v7+",
    "up_time": "19 days,  23:27",
    "last_update": "2020-08-23T17:03:47-06:00",
    "fs_total_gb": 64,
    "fs_free_prcnt": 10,
    "networking": {
      "eth0": {
        "mac": "b8:27:eb:1a:f3:bc"
      },
      "wlan0": {
        "IP": "192.168.100.189",
        "mac": "b8:27:eb:4f:a6:e9"
      }
    },
    "drives": {
      "root": {
        "size_gb": 64,
        "used_prcnt": 10,
        "device": "/dev/root",
        "mount_pt": "/"
      },
      "media-pi-CRUZER": {
        "size_gb": 2,
        "used_prcnt": 1,
        "device": "/dev/sda1",
        "mount_pt": "/media/pi/CRUZER"
      }
    },
    "memory": {
      "size_mb": "926.078",
      "free_mb": "575.414"
    },
    "cpu": {
      "hardware": "BCM2835",
      "model": "ARMv7 Processor rev 4 (v7l)",
      "number_cores": 4,
      "bogo_mips": "307.20",
      "serial": "00000000661af3bc"
    },
    "throttle": [
      "throttled = 0x0",
      "Not throttled"
    ],
    "temperature_c": 53.7,
    "temp_gpu_c": 53.7,
    "temp_cpu_c": 52.6,
    "reporter": "ISP-RPi-mqtt-daemon v1.5.4",
    "report_interval": 5
  }
}
```

**NOTE:** Where there's an IP address that interface is connected.

This data can be subscribed to and processed by your home assistant installation. How you build your RPi dashboard from here is up to you!  

## Lovelace Custom Card

We have a Lovelace Custom Card that makes displaying this RPi Monitor data very easy.  

See my project: [Lovelace RPi Monitor Card](https://github.com/ironsheep/lovelace-rpi-monitor-card)

## Credits

Thank you to Thomas Dietrich for providing a wonderful pattern for this project. His project, which I use and heartily recommend, is [miflora-mqtt-deamon](https://github.com/ThomDietrich/miflora-mqtt-daemon)

Thanks to [synoniem](https://github.com/synoniem) for working through the issues with startup as a SystemV init script and for providing 'rpi-reporter' script itself and for identifying the need for support of other boot device forms.

----

## Disclaimer and Legal

> *Raspberry Pi* is registered trademark of *Raspberry Pi (Trading) Ltd.*
>
> This project is a community project not for commercial use.
> The authors will not be held responsible in the event of device failure or simply errant reporting of your RPi status.
>
> This project is in no way affiliated with, authorized, maintained, sponsored or endorsed by *Raspberry Pi (Trading) Ltd.* or any of its affiliates or subsidiaries.

----

### [Copyright](copyright) | [License](LICENSE)

[commits-shield]: https://img.shields.io/github/commit-activity/y/ironsheep/RPi-Reporter-MQTT2HA-Daemon.svg?style=for-the-badge
[commits]: https://github.com/ironsheep/RPi-Reporter-MQTT2HA-Daemon/commits/master

[license-shield]: https://img.shields.io/github/license/ironsheep/RPi-Reporter-MQTT2HA-Daemon.svg?style=for-the-badge

[maintenance-shield]: https://img.shields.io/badge/maintainer-S%20M%20Moraco%20%40ironsheepbiz-blue.svg?style=for-the-badge

[releases-shield]: https://img.shields.io/github/release/ironsheep/RPi-Reporter-MQTT2HA-Daemon.svg?style=for-the-badge
[releases]: https://github.com/ironsheep/RPi-Reporter-MQTT2HA-Daemon/releases
