# Weatherboy phone base on an ESP8266: give UART0 to the host, not to the REPL.
# Runs before main.py at every boot. Without this the REPL echoes and eats the hook
# messages, and the host sees garbage mixed in with them.
#
# Recovery: hold FLASH (GPIO0) while resetting to get the serial bootloader back, then
# re-flash main.py - the REPL is not coming back on its own.
import os

import machine

os.dupterm(None, 0)  # UART0 belongs to the host now
uart = machine.UART(0, baudrate=115200)