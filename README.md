# On Air Link

Use mixer MIDI port to report on air CDJs using Pioneer Pro DJ Link

## Armbian network config

```bash
nmcli connection add type ethernet ifname eth0 con-name eth0-auto
nmcli connection modify eth0-auto ipv6.method disabled
nmcli connection modify eth0-auto connection.autoconnect-priority 100
nmcli connection modify eth0-auto connection.autoconnect-retries 2
nmcli connection modify eth0-auto ipv4.dhcp-timeout 10

nmcli connection add type ethernet ifname eth0 con-name eth0-ll
nmcli connection modify eth0-ll ipv4.method link-local ipv6.method disabled
nmcli connection modify eth0-ll connection.autoconnect-priority 50

nmcli connection delete 'Wired connection 1'
```

## Dependencies

```bash
sudo apt update
sudo apt install python3-pip python3-cffi libasound2
pip install alsa-midi
```

## Run script at startup

```bash
sudo cp ./onair-link.py /bin
(sudo crontab -l && echo "@reboot python /bin/onair-link.py &") | sudo crontab -
```
