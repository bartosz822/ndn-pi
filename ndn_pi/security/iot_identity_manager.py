# -*- Mode:python; c-file-style:"gnu"; indent-tabs-mode:nil -*- */
#
# Copyright (C) 2014 Regents of the University of California.
# Author: Adeola Bannis <thecodemaiden@gmail.com>
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
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
# A copy of the GNU General Public License is in the file COPYING.

from pyndn.security.identity import IdentityManager
from iot_private_key_storage import IotPrivateKeyStorage

class IotIdentityManager(IdentityManager):
    """
     Overrides the default constructor to force the use of our 
        IotPrivateKeyStorage
    """
    def __init__(self, identityStorage=None):
        super(IotIdentityManager, self).__init__(identityStorage, IotPrivateKeyStorage())
        
    def getPrivateKey(self, keyName):
        return self._privateKeyStorage.getPrivateKey(keyName)

    def addPrivateKey(self, keyName, keyDer):
        self._privateKeyStorage.addPrivateKey(keyName, keyDer)