#!/usr/bin/env python3

import sys
import fcntl
import struct
import socket
import logging
import argparse
import ipaddress
from enum import IntEnum
from typing import Any, Iterable, List
from alsa_midi import SequencerClient, PortType, EventType, WRITE_PORT


##
# CONSTANTS
##

NET_IFACE = 'eth0'
DEVICE_NAME = 'On Air Link'


##
# CLASSES
##

class DJMEnumMeta(type(IntEnum)):
    def __getattribute__(self, __name: str) -> Any:
        attr = super().__getattribute__(__name)
        if isinstance(attr, property):
            raise NotImplementedError(f'no value assigned to {__name}')
        return attr

    def __getattr__(self, name):
        try:
            return super().__getattr__(name)
        except AttributeError:
            return None

    def __getitem__(self, name):
        return getattr(self, name)

class DJMEnumBase(IntEnum, metaclass=DJMEnumMeta):
    @property
    @staticmethod
    def CH_MAX():
        pass
    @property
    @staticmethod
    def CROSS_FADER():
        pass
    @property
    @staticmethod
    def CH1_FADER():
        pass
    @property
    @staticmethod
    def FIRST_CROSS_FADER_ASSIGN(): # for 2 channels mixers use cross fader reverse
        pass

class DJM250MK2(DJMEnumBase):
    CH_MAX = 2
    CROSS_FADER = 0xB
    CH1_FADER = 0x11
    FIRST_CROSS_FADER_ASSIGN = 0x60

class DJM450(DJMEnumBase):
    CH_MAX = 2
    CROSS_FADER = 0xB
    CH1_FADER = 0x11
    FIRST_CROSS_FADER_ASSIGN = 0x60

class DJM750(IntEnum):
    CH_MAX = 4
    CROSS_FADER = 11
    CH1_FADER = 17
    FIRST_CROSS_FADER_ASSIGN = 65
    CH_FADER = 94

class DJM750MK2(IntEnum):
    CH_MAX = 4
    CROSS_FADER = 0xB
    CH1_FADER = 0x11
    FIRST_CROSS_FADER_ASSIGN = 0x41
    CH_FADER = 0x5E

class DJM850(IntEnum):
    CH_MAX = 4
    CROSS_FADER = 11
    CH1_FADER = 17
    FIRST_CROSS_FADER_ASSIGN = 65
    CH_FADER = 94
    FADER_START1_NOTE = 102

class ProDjLink:
    _HEADER = b'\x51\x73\x70\x74\x31\x57\x6d\x4a\x4f\x4c'
    _MAX_DEVICE_NAME_LEN = 20

    @staticmethod
    def _format_device_name(device_name:str):
        device_name = device_name.ljust(ProDjLink._MAX_DEVICE_NAME_LEN, '\0')
        ret_dev_name = device_name[0:ProDjLink._MAX_DEVICE_NAME_LEN]
        if len(device_name) > ProDjLink._MAX_DEVICE_NAME_LEN:
            logging.warning(f'{device_name} truncated to {ret_dev_name}')
        return ret_dev_name.encode()

    @staticmethod
    def onair_pkt(device_name:str, onair_ch:List):
        device_name = ProDjLink._format_device_name(device_name)
        return ProDjLink._HEADER + b'\x03' + device_name + b'\x01\0\0\0\x09' + bytes(onair_ch) + b'\0\0\0\0\0'

    @staticmethod
    def fader_start_pkt(device_name:str, channel:int, stop:bool):
        device_name = ProDjLink._format_device_name(device_name)
        ch_state = [2] * 4 # by default keep current value of all fader starts
        ch_state[channel] = int(stop) # PLAY = 0, STOP = 1
        return ProDjLink._HEADER + b'\x02' + device_name + b'\x01\0\0\0\x04' + bytes(ch_state)

class MidiMain:
    FADER_TRESHOLD = 1
    CH_FADER_LOW_SLOPE_THRESHOLD = 2

    def __init__(self, djm_enum:IntEnum, client:SequencerClient, source_port=None) -> None:
        self.djm_enum = djm_enum

        self.client = client

        self.source_port = source_port
        self.input_port = self.client.create_port("input", caps=WRITE_PORT, type=PortType.MIDI_GENERIC | PortType.HARDWARE)

        self.xfader_value = 64
        self.xfader_channel = 'AB'
        self.cross_fader_assign = [64] * self.djm_enum.CH_MAX
        self.fader_value = [0] * self.djm_enum.CH_MAX
        self.onair_fader = [False] * self.djm_enum.CH_MAX
        self.fader_th = self.FADER_TRESHOLD

        self.last_fader_start_pkt = None
        self.last_onair_pkt = None

        self.prev_event = None

        if self.source_port:
            self.input_port.connect_from(self.source_port)

    def __update_onair_fader(self, direction_down:bool, fader_th:int, upd_idx:Iterable):
        for i in upd_idx:
            self.onair_fader[i] = self.fader_value[i] >= fader_th + int(direction_down)

    def _get_onair_xfader(self):
        if self.xfader_channel == 'A':
            onair_xfader = [v <= 64 for v in self.cross_fader_assign]

        elif self.xfader_channel == 'B':
            onair_xfader = [v >= 64 for v in self.cross_fader_assign]

        else:
            onair_xfader = [True] * 4

        return onair_xfader

    def connect_port(self, port_str:str):
        ports = self.client.list_ports(input=True, type=PortType.MIDI_GENERIC | PortType.HARDWARE)

        for port_info in ports:
            if port_str in port_info.client_name:
                self.source_port = port_info
                break
        else:
            return False

        self.input_port.connect_from(self.source_port)

        return True

    def wait_handle_input_event(self):
        if self.source_port == None:
            return None

        try:
            event = self.client.event_input()
            if event.type == EventType.PORT_UNSUBSCRIBED:
                raise RuntimeError(repr(event))
        except Exception:
            self.source_port = None
            return None
        except:
            sys.exit(1)

        out_pkt = b''

        if self.prev_event == None:
            self.prev_event = event

        send_onair_pkt = True

        if event.type == EventType.CONTROLLER:
            if event.param == self.djm_enum.CROSS_FADER:
                if event.value <= self.FADER_TRESHOLD - int(event.value > self.xfader_value):
                    self.xfader_channel = 'A'
                elif event.value >= 127 - (self.FADER_TRESHOLD - int(event.value < self.xfader_value)):
                    self.xfader_channel = 'B'
                else:
                    self.xfader_channel = 'AB'
                self.xfader_value = event.value

            elif event.param in range(self.djm_enum.CH1_FADER, self.djm_enum.CH1_FADER + self.djm_enum.CH_MAX):
                direction_down = event.value < self.fader_value[event.param - self.djm_enum.CH1_FADER]
                self.fader_value[event.param - self.djm_enum.CH1_FADER] = event.value
                self.__update_onair_fader(direction_down, self.fader_th, [event.param - self.djm_enum.CH1_FADER])

            elif event.param in range(self.djm_enum.FIRST_CROSS_FADER_ASSIGN, self.djm_enum.FIRST_CROSS_FADER_ASSIGN + self.djm_enum.CH_MAX):
                if self.djm_enum.CH_MAX == 2:
                    self.cross_fader_assign[0] = event.value
                    self.cross_fader_assign[1] = 127 - event.value
                else:
                    self.cross_fader_assign[event.param - self.djm_enum.FIRST_CROSS_FADER_ASSIGN] = event.value

            elif event.param == self.djm_enum.CH_FADER:
                new_fader_th = self.FADER_TRESHOLD if event.value >= 64 else self.CH_FADER_LOW_SLOPE_THRESHOLD

                if self.fader_th != new_fader_th:
                    self.__update_onair_fader(new_fader_th > self.fader_th, new_fader_th, range(4))

                self.fader_th = new_fader_th
            else:
                send_onair_pkt = False

        elif event.type == EventType.NOTEON and self.djm_enum.FADER_START1_NOTE:
            if event.note in range(self.djm_enum.FADER_START1_NOTE, self.djm_enum.FADER_START1_NOTE + self.djm_enum.CH_MAX):

                pkt = ProDjLink.fader_start_pkt(DEVICE_NAME, event.note - self.djm_enum.FADER_START1_NOTE, event.velocity == 0)
                if pkt != self.last_fader_start_pkt:
                    logging.debug(f'Fader Start CH{event.note - self.djm_enum.FADER_START1_NOTE + 1}: {"STOP" if event.velocity == 0 else "PLAY"}')
                    out_pkt = pkt
                self.last_fader_start_pkt = pkt

                if self.prev_event.type == EventType.CONTROLLER:
                    if self.prev_event.param == self.djm_enum.CROSS_FADER:
                        self.cross_fader_assign[event.note - self.djm_enum.FADER_START1_NOTE] = 0 if self.prev_event.value >= 64 else 127
                    elif self.prev_event.param in range(self.djm_enum.CH1_FADER, self.djm_enum.CH1_FADER + self.djm_enum.CH_MAX):
                        self.cross_fader_assign[event.note - self.djm_enum.FADER_START1_NOTE] = 64
            else:
                send_onair_pkt = False

        else:
            send_onair_pkt = False

        if send_onair_pkt:
            pkt = ProDjLink.onair_pkt(DEVICE_NAME, [vf & vx for vf, vx in zip(self.onair_fader, self._get_onair_xfader())])
            if pkt != self.last_onair_pkt:
                logging.debug(f'On Air Channels Status: {[vf & vx for vf, vx in zip(self.onair_fader, self._get_onair_xfader())]}')
                out_pkt = pkt
            self.last_onair_pkt = pkt

        self.prev_event = event

        return out_pkt


##
# FUNCTIONS
##

def get_ip_address(ifname:str):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x8915,  # SIOCGIFADDR
        struct.pack('256s', ifname[:15].encode())
    )[20:24])

def get_netmask(ifname:str):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x891b,  # SIOCGIFNETMASK
        struct.pack('256s', ifname[:15].encode())
    )[20:24])

def find_port(client:SequencerClient, port_str:str):
    for port_info in client.list_ports(input=True, type=PortType.MIDI_GENERIC | PortType.HARDWARE):
        if port_str in port_info.client_name:
            return port_info.client_name
    return ''

def get_djm_enum(client_name:str):
    djm_enum = None

    if client_name == 'DJM-250MK2':
        djm_enum = DJM250MK2
    elif client_name == 'DJM-450':
        djm_enum = DJM450
    if client_name == 'DJM-750':
        djm_enum = DJM750
    if client_name == 'DJM-750MK2':
        djm_enum = DJM750MK2
    if client_name == 'DJM-850':
        djm_enum = DJM850

    if djm_enum != None:
        logging.debug(f'Found DJM Enum: {djm_enum.__name__}')
    return djm_enum


##
# MAIN STUFFS
##

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-v', '--verbose',
        help="Print INFO messages",
        action="store_const", dest="loglevel", const=logging.INFO,
        default=logging.WARNING,
    )
    parser.add_argument(
        '-d', '--debug',
        help="Print DEBUG messages",
        action="store_const", dest="loglevel", const=logging.DEBUG,
    )
    parser.add_argument(
        '-l', '--no-global-broadcast',
        help="Use local net broadcast addr to send packets instead of '255.255.255.255' (option always enabled if link-local IP)",
        action="store_true", dest="local_broadcast",
    )
    return parser.parse_args()

def main(local_broadcast:bool):
    logging.debug('Program Start')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    midi_client = SequencerClient(DEVICE_NAME)

    midi_main = None
    last_src_port_name = None

    logging.info('Waiting For USB MIDI Port...')
    while midi_main == None:
        djm_enum = get_djm_enum(find_port(midi_client, 'DJM'))
        if djm_enum:
            midi_main = MidiMain(djm_enum, midi_client)

    logging.debug('Entering Main Loop...')
    while True:
        if midi_main.source_port == None:
            if not midi_main.connect_port('DJM'):
                continue

            # If USB MIDI device has changed, force midi_main fields reset
            if last_src_port_name != None and last_src_port_name != midi_main.source_port.client_name:
                midi_main = MidiMain(get_djm_enum(midi_main.source_port.client_name), midi_client, midi_main.source_port)
            last_src_port_name = midi_main.source_port.client_name
            logging.info(f'MIDI USB Connected to: {last_src_port_name}')

        pkt = midi_main.wait_handle_input_event()

        if pkt == None:
            logging.info(f'MIDI USB Disconnected from: {last_src_port_name}')
        elif pkt:
            try:
                net = ipaddress.IPv4Network(get_ip_address(NET_IFACE) + '/' + get_netmask(NET_IFACE), False)
                dst_addr = str(net.broadcast_address) if local_broadcast or net.is_link_local else '255.255.255.255'
                logging.debug(f'DEVice NET: {net}, DESTination ADDRess: {dst_addr}')
                sock.sendto(pkt, (dst_addr, 50001))
            except Exception:
                continue
            except:
                sys.exit(1)

if __name__ == '__main__':
    try:
        args = parse_args()
        logging.basicConfig(level=args.loglevel, format='%(levelname)s:%(message)s')
        main(args.local_broadcast)
    except:
        sys.exit(1)
