#!/usr/bin/python
#
# Copyright 2011 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import hooking
from vdsm.config import config

if config.getboolean('vars', 'fake_kvm_support'):
    domxml = hooking.read_domxml()
    domxml.documentElement.setAttribute("type", "qemu")

    graphics = domxml.getElementsByTagName("graphics")[0]
    graphics.removeAttribute("passwdValidTo")

    memory = config.get('vars', 'fake_kvm_memory')

    if memory != '0':
        for memtag in ("memory", "currentMemory"):
            memvalue = domxml.getElementsByTagName(memtag)[0]
            while memvalue.firstChild:
                memvalue.removeChild(memvalue.firstChild)

            memvalue.appendChild(domxml.createTextNode(memory))

    for cputag in domxml.getElementsByTagName("cpu"):
        cputag.parentNode.removeChild(cputag)

    hooking.write_domxml(domxml)