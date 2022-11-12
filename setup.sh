# Clean Armbian Bullseye Minimal setup
apt update && apt upgrade -y

# Install dependencies
apt install python3-pip python3-cffi libasound2 -y
pip install alsa-midi

# Configure run at startup
cp ./onair-link.py /bin
(crontab -l 2>/dev/null; echo "@reboot onair-link.py &") | crontab -

# Network configuration
nmcli connection add type ethernet ifname eth0 con-name eth0-auto
nmcli connection modify eth0-auto ipv6.method disabled
nmcli connection modify eth0-auto connection.autoconnect-priority 100
nmcli connection modify eth0-auto connection.autoconnect-retries 2
nmcli connection modify eth0-auto ipv4.dhcp-timeout 10

nmcli connection add type ethernet ifname eth0 con-name eth0-ll
nmcli connection modify eth0-ll ipv4.method link-local ipv6.method disabled
nmcli connection modify eth0-ll connection.autoconnect-priority 50

nmcli connection delete 'Wired connection 1'
