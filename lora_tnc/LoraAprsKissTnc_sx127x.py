#!/usr/bin/python
# -*- coding: utf-8 -*-

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import datetime
import time
import config
import sys
import math
import random
import threading
from queue import Empty
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.absolute()))
from pySX127x.SX127x.LoRa import LoRa
from pySX127x.SX127x.constants import *
from pySX127x.SX127x.board_config import BOARD
# Display libraries are optional; import them only when the display is enabled.
if config.disp_en:
   from display import display
   from PIL import Image
from pathlib import Path

#import RPi.GPIO as GPIO
#import spidev

if config.disp_en:
   display = display()

def logf(message):
    timestamp = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S - ')
    if config.log_enable:
       fileLog = open(config.logpath,"a")
       fileLog.write(timestamp + message+"\r")
       fileLog.close()
    print(timestamp + message+"\r")

def lcd(message):
       timestamp = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S - ')
       display.showtext(timestamp + message)

class LoraAprsKissTnc(LoRa):
    LORA_APRS_HEADER = b"<\xff\x01"

    # APRS data types
    DATA_TYPES_POSITION = b"!'/@`"
    DATA_TYPE_MESSAGE = b":"
    DATA_TYPE_THIRD_PARTY = b"}"

    queue = None
    server = None

    TX_TIMEOUT_MARGIN = 2.0       # s added to the computed airtime
    CHANNEL_BUSY_MAX_WAIT = 15.0  # s of carrier sense before sending anyway

    # init has LoRa APRS default config settings - might be initialized different when creating object with parameters
    def __init__(self, queue, server, frequency=433775000, preamble=8, spreadingFactor=12, bandwidth=BW.BW125,
                 codingrate=5, appendSignalReport = True, paSelect = 1, outputPower = 15, sync_word = 0x12, verbose=False):
    # Init SX127x
        if config.disp_en:
           image = Image.open(str(Path(__file__).parent.absolute())+"/LoRa-KISS-TNC_logo_64x128_raw.ppm").convert("1")
           display.showimage(image)
           time.sleep(4)

        BOARD.setup()

        # SPI is used from the main loop and from the GPIO interrupt thread.
        self.radio_lock = threading.RLock()
        self.tx_idle = threading.Event()
        self.tx_idle.set()
        self.tx_timeout = self.TX_TIMEOUT_MARGIN

        logf("LoRa radio initialized. Waiting for LoRa Spots...")
        if config.disp_en:
           lcd("LoRa radio initialized. Waiting for LoRa Spots...")

        super(LoraAprsKissTnc, self).__init__(verbose)
        self.queue = queue
        self.appendSignalReport = appendSignalReport

        self.set_mode(MODE.SLEEP)
        frequency=frequency/1e6
        self.set_freq(frequency)
        self.set_preamble(preamble)
        self.set_spreading_factor(spreadingFactor)

        bw = {
        7800  : BW.BW7_8,
        10400 : BW.BW10_4,
        15600 : BW.BW15_6,
        20800 : BW.BW20_8,
        31250 : BW.BW31_25,
        41700 : BW.BW41_7,
        62500 : BW.BW62_5,
        125000: BW.BW125,
        250000: BW.BW250,
        500000: BW.BW500
        }

        self.set_bw(bw[bandwidth])
        # Kept always on as in the original firmware, so stations running it
        # stay compatible (both sides must use the same setting).
        self.sf = spreadingFactor
        self.bandwidth_hz = bandwidth
        self.codingrate = codingrate
        self.preamble_symbols = preamble
        self.low_data_rate_optim = True
        self.set_low_data_rate_optim(self.low_data_rate_optim)

        CR = {
        5 : CODING_RATE.CR4_5,
        6 : CODING_RATE.CR4_6,
        7 : CODING_RATE.CR4_7,
        8 : CODING_RATE.CR4_8
        }
        self.set_coding_rate(CR[codingrate])
        self.set_ocp_trim(100)

        self.set_sync_word(sync_word)

        if outputPower>15 : outputPower=15
        self.set_pa_config(paSelect, 7, outputPower)
        self.set_max_payload_length(255)
        self.set_dio_mapping([0] * 6)
        self.server = server

        self.reset_ptr_rx()
        self.set_mode(MODE.RXCONT)
        #self.set_mode(MODE.SLEEP)

    def airtime(self, length):
        """LoRa time on air in seconds (Semtech AN1200.13, explicit header, CRC on)."""
        symbol_time = (2 ** self.sf) / self.bandwidth_hz
        divisor = 4 * (self.sf - (2 if self.low_data_rate_optim else 0))
        payload_symbols = 8 + max(math.ceil((8 * length - 4 * self.sf + 28 + 16) / divisor) * self.codingrate, 0)
        return (self.preamble_symbols + 4.25 + payload_symbols) * symbol_time

    def startListening(self):
        # Frames are sent as soon as they arrive from KISS (the original
        # polled the queue every 3 s, which delayed every frame and allowed
        # only one frame per 3 s).
        try:
            while True:
                try:
                    data = self.queue.get(timeout=0.5)
                except Empty:
                    continue
                if not data:
                    continue
                try:
                    if config.TX_OE_Style:
                        data = self.LORA_APRS_HEADER + data
                        logf("LoRa TX OE Syle packet: " + repr(data))
                        if config.disp_en:
                           lcd("LoRa TX OE Syle packet: " + repr(data))
                    else:
                        logf("LoRa TX Standard AX25 packet: " + repr(data))
                        if config.disp_en:
                           lcd("LoRa TX Standard AX25 packet: " + repr(data))
                    self.wait_for_tx_idle()
                    self.wait_for_clear_channel()
                    self.transmit(data)
                except Exception:
                    # one bad frame must not stop the TNC
                    logf("TX failed: " + traceback.format_exc())
        except KeyboardInterrupt:
            logf("Keyboard Interrupt received. Exiting...")
            if config.disp_en:
               lcd("Keyboard Interrupt received. Exiting...")
            BOARD.teardown()

    def wait_for_tx_idle(self):
        """Wait until the previous frame left the radio; writing the FIFO
        during TX would corrupt it."""
        if self.tx_idle.wait(self.tx_timeout):
            return
        logf("TX DONE not received in %.1f s, returning radio to RX" % self.tx_timeout)
        with self.radio_lock:
            self.clear_irq_flags(TxDone=1)
            self.set_dio_mapping([0] * 6)
            self.reset_ptr_rx()
            self.set_mode(MODE.RXCONT)
        self.tx_idle.set()

    def wait_for_clear_channel(self):
        """Carrier sense: back off for a short random time while a LoRa
        signal is being received."""
        deadline = time.monotonic() + self.CHANNEL_BUSY_MAX_WAIT
        while time.monotonic() < deadline:
            with self.radio_lock:
                busy = self.get_modem_status()["signal_detected"]
            if not busy:
                return
            time.sleep(random.uniform(0.05, 0.3))
        logf("Channel busy for %.0f s, transmitting anyway" % self.CHANNEL_BUSY_MAX_WAIT)

    def on_rx_done(self):
        data = None
        with self.radio_lock:
            try:
                flags = self.get_irq_flags()
                payload = self.read_payload(nocheck=True)
                if any([flags[s] for s in ['crc_error', 'rx_timeout']]):
                    logf("Receive Error, discarding frame.")
                elif not payload:
                    logf("No Payload!")
                else:
                    data = bytes(payload)
                    rssi = self.get_pkt_rssi_value()
                    snr = self.get_pkt_snr_value()
            finally:
                # Always clear the flags and re-arm RX; otherwise DIO0 stays
                # high and no further RX interrupt (rising edge) would come.
                self.clear_irq_flags(RxDone=1, PayloadCrcError=1, RxTimeout=1, ValidHeader=1)
                self.reset_ptr_rx()
                self.set_mode(MODE.RXCONT)
        if data is None:
            return
        signalreport = "Level:"+str(rssi)+" dBm, SNR:"+str(snr)+"dB"
        logf("LoRa RX[%idBm/%.2fdB, %ibytes]: %s" %(rssi, snr, len(data), repr(data)))
        if config.disp_en:
           lcd("LoRa RX[RSSI=%idBm, SNR=%idB, %iBytes]: %s" %(rssi, snr, len(data), repr(data)))
        if self.server:
            self.server.send(data, signalreport)

    # self.set_mode(MODE.CAD)

    def on_tx_done(self):
        with self.radio_lock:
            self.clear_irq_flags(TxDone=1)  # clear txdone IRQ flag
            self.set_dio_mapping([0] * 6)
            self.reset_ptr_rx()
            self.set_mode(MODE.RXCONT)
        self.tx_idle.set()
        logf("TX DONE")

    def transmit(self, data):
        with self.radio_lock:
            self.tx_timeout = self.airtime(len(data)) + self.TX_TIMEOUT_MARGIN
            self.tx_idle.clear()
            self.write_payload([c for c in data])
            self.set_dio_mapping([1, 0, 0, 0, 0, 0])
            self.set_mode(MODE.TX)

    def aprs_data_type(self, lora_aprs_frame):
        delimiter_position = lora_aprs_frame.find(b":")
        try:
            return lora_aprs_frame[delimiter_position + 1]
        except IndexError:
            return ""
