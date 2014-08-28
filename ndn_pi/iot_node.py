
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
import logging
import time
import sys

from pyndn import Name, Face, Interest, Data, ThreadsafeFace
from pyndn.security import KeyChain
from pyndn.security.identity import IdentityManager
from pyndn.security.policy import ConfigPolicyManager
from pyndn.security.certificate import IdentityCertificate
from pyndn.encoding import ProtobufTlv
from pyndn.transport import UdpTransport

from iot_identity_storage import IotIdentityStorage
from iot_policy_manager import IotPolicyManager

from commands.cert_request_pb2 import CertificateRequestMessage
from commands.update_capabilities_pb2 import UpdateCapabilitiesCommandMessage

from pyndn.util.boost_info_parser import BoostInfoParser
from pyndn.security.security_exception import SecurityException

try:
    import asyncio
except ImportError:
    import trollius as asyncio

class IotNode(object):
    """
    TBD
    """
    def __init__(self, configFilename):
        super(IotNode, self).__init__()

        self.config = BoostInfoParser()
        self.config.read(configFilename)

        self._identityStorage = IotIdentityStorage()
        self._identityManager = IdentityManager(self._identityStorage)
        self._policyManager = IotPolicyManager(self._identityStorage, configFilename)

        deviceSuffix = self.config["device/deviceName"][0].value
        self.prefix = Name(self._policyManager.getEnvironmentPrefix()).append(deviceSuffix)
        
        self._keyChain = KeyChain(self._identityManager, self._policyManager)
        self._identityStorage.setDefaultIdentity(self.prefix)

        self._registrationFailures = 0
        self._certificateTimeouts = 0
        self._prepareLogging()

        self._setupComplete = False

##
# Logging
##

    def _prepareLogging(self):
        self.log = logging.getLogger(str(self.__class__))
        self.log.setLevel(logging.DEBUG)
        logFormat = "%(asctime)-15s %(name)-20s %(funcName)-20s (%(levelname)-8s):\n\t%(message)s"
        self._console = logging.StreamHandler()
        self._console.setFormatter(logging.Formatter(logFormat))
        self._console.setLevel(logging.INFO)
        # without this, a lot of ThreadsafeFace errors get swallowed up
        logging.getLogger("trollius").addHandler(self._console)
        self.log.addHandler(self._console)

    def setLogLevel(self, l):
        self._console.setLevel(l)

    def getLogger(self):
        return self.log

###
# Startup and shutdown
###

    def start(self):
        self._loop = asyncio.get_event_loop()
        self._face = ThreadsafeFace(self._loop, '')
        self._face.setCommandSigningInfo(self._keyChain, self._keyChain.getDefaultCertificateName())
        self._face.registerPrefix(self.prefix, self._onCommandReceived, self.onRegisterFailed)
        self._keyChain.setFace(self._face)

        self._loop.call_soon(self.onStartup)

        self._isStopped = False
        self._face.stopWhen(lambda:self._isStopped)
        try:
            self._loop.run_forever()
        except Exception as e:
            self.log.exception(exc_info=True)
        finally:
            self.stop()

    def onRegisterFailed(self, prefix):
        self.log.warn("Could not register " + prefix.toUri())
        if self._registrationFailures < 5:
            self._registrationFailures += 1
            self.log.warn("Retry: {}/{}".format(self._registrationFailures, 5)) 
            self._face.registerPrefix(self.prefix, self._onCommandReceived, self.onRegisterFailed)
        else:
            self.log.critical("Could not register device prefix, ABORTING")
            self._isStopped = True


    def stop(self):
        self.log.info("Shutting down")
        self._loop.close()
        self._face.shutdown()

    def onStartup(self):
        if not self._policyManager.hasRootSignedCertificate():
            self._loop.call_soon(self._sendCertificateRequest)
        else:
            self._loop.call_soon(self._updateCapabilities)

    def setupComplete(self):
        """
        Entry point for user-defined behavior. After this is called, the 
        certificates are in place and capabilities have been sent to the 
        controller. The node can now search for other devices, set up
        control logic, etc
        """
        pass

###
# Device capabilities
# On startup, tell the controller what types of commands are available
##

    def _onCapabilitiesAck(self, interest, data):
        self.log.debug('Received {}'.format(data.getName().toUri()))
        if not self._setupComplete:
            self._setupComplete = True
            self._loop.call_soon(self.setupComplete)

    def _onCapabilitiesTimeout(self, interest):
        #try again in 30s
        self.log.info('Timeout waiting for capabilities update')
        self._loop.call_later(30, self._updateCapabilities)

    def _updateCapabilities(self):
        """
        Send the controller a list of our commands.
        """ 
        fullCommandName = Name(self._policyManager.getTrustRootIdentity()
                ).append('updateCapabilities')
        capabilitiesMessage = UpdateCapabilitiesCommandMessage()
        try:
            allCommands = self.config["device/command"]
        except KeyError:
            pass # no commands
        else:
            for command in allCommands:
                commandName = Name(self.prefix).append(Name(command["name"][0].value))
                capability = capabilitiesMessage.capabilities.add()
                for i in range(commandName.size()):
                    capability.commandPrefix.components.append(
                            str(commandName.get(i).getValue()))
                for node in command["keyword"]:
                    capability.keywords.append(node.value)
                try:
                    command["authorize"]
                    capability.needsSignature = True
                except KeyError:
                    pass

        encodedCapabilities = ProtobufTlv.encode(capabilitiesMessage)
        fullCommandName.append(encodedCapabilities)
        interest = Interest(fullCommandName)
        interest.setInterestLifetimeMilliseconds(3000)
        self._face.makeCommandInterest(interest)
        self.log.info("Sending capabilities to controller")
        self._face.expressInterest(interest, self._onCapabilitiesAck, self._onCapabilitiesTimeout)
     
###
# Certificate signing requests
# On startup, if we don't have a certificate signed by the controller, we request one.
###
       
    def _sendCertificateRequest(self):
        """
        We compose a command interest with our public key info so the trust 
        anchor can sign us a certificate
        
        """

        defaultKey = self._identityStorage.getDefaultKeyNameForIdentity(self.prefix)
        self.log.debug("Found key: " + defaultKey.toUri())

        message = CertificateRequestMessage()
        message.command.keyType = self._identityStorage.getKeyType(defaultKey)
        message.command.keyBits = self._identityStorage.getKey(defaultKey).toRawStr()

        for component in range(defaultKey.size()):
            message.command.keyName.components.append(defaultKey.get(component).toEscapedString())

        paramComponent = ProtobufTlv.encode(message)

        interestName = Name(self._policyManager.getTrustRootIdentity()).append("certificateRequest").append(paramComponent)
        interest = Interest(interestName)
        interest.setInterestLifetimeMilliseconds(10000) # takes a tick to verify and sign
        self._face.makeCommandInterest(interest)

        self.log.info("Sending certificate request to controller")
        self.log.debug("Certificate request: "+interest.getName().toUri())
        self._face.expressInterest(interest, self._onCertificateReceived, self._onCertificateTimeout)
   

    def _onCertificateTimeout(self, interest):
        #give up?
        self.log.warn("Timed out trying to get certificate")
        if self._certificateTimeouts > 5:
            self.log.critical("Trust root cannot be reached, exiting")
            self._isStopped = True
        else:
            self._certificateTimeouts += 1
            self._loop.call_soon(self._sendCertificateRequest)
        pass


    def _processValidCertificate(self, data):
        # if we were successful, the content of this data is a signed cert
        try:
            newCert = IdentityCertificate()
            newCert.wireDecode(data.getContent())
            self.log.info("Received certificate from controller")
            self.log.debug(str(newCert))
            try:
                self._identityManager.addCertificate(newCert)
            except SecurityException:
                pass # can't tell existing certificat from another error
            self._identityManager.setDefaultCertificateForKey(newCert)
        except Exception as e:
            self.log.exception("Could not import new certificate", exc_info=True)

    def _certificateValidationFailed(self, data):
        self.log.error("Certificate from controller is invalid!")

    def _onCertificateReceived(self, interest, data):

        self._keyChain.verifyData(data, self._processValidCertificate, self._certificateValidationFailed)
        self._loop.call_later(5, self._updateCapabilities)

###
# Interest handling
# Verification of and responses to incoming (command) interests
##

    def sendData(self, data, transport, sign=True):
        if sign:
            self._keyChain.sign(data, self._keyChain.getDefaultCertificateName())
        transport.send(data.wireEncode().buf())

    def verificationFailed(dataOrInterest):
        self.log.info("Received invalid" + dataOrInterest.getName().toUri())

    def _makeVerifiedCommandDispatch(function, transport):
        def onVerified(interest):
            self.log.info("Verified: " + interest.getName().toUri())
            responseData = function(interest)
            self.sendData(responseData, transport)
        return onVerified

    def unknownCommandResponse(self, interest):
        responseData = Data(Name(interest.getName()).append("unknown"))
        responseData.setContent("Unknown command name")
        responseData.getMetaInfo().setFreshnessPeriod(1000) # expire soon

        return responseData

    def _onCommandReceived(self, prefix, interest, transport, prefixId):
        # if this is a cert request, we can serve it from our store (if it exists)
        # else we must look in our command list to see if this requires verification
        # we dispatch directly or after verification as necessary
        certData = self._identityStorage.getCertificate(interest.getName())
        if certData is not None:
            # if we sign the certificate, we lose the controller's signature!
            self.sendData(certData, transport, False)
            return

        # now we look for the first command that matches in our config
        allCommands = self.config["device/command"]
        
        for command in allCommands:
            fullCommandName = Name(self.prefix).append(Name(str(command["name"][0].value)))
            if fullCommandName.match(interest.getName()):
                dispatchFunctionName = command["functionName"][0].value
                try:
                    func = self.__getattribute__(dispatchFunctionName)
                except AttributeError:
                    # command not implemented
                    responseData = self.unknownCommandResponse(interest)
                    self.sendData(responseData, transport)
                    return
            
                try:
                    command["authorize"][0]
                except KeyError:
                    # no need to authorize, just run
                    responseData = func(interest)
                    self.sendData(responseData, transport)
                    return 
            
                # requires verification
                try:
                    self._keyChain.verifyInterest(interest, 
                            self._makeVerifiedCommandDispatch(func, transport),
                            self.verificationFailed)
                    # if verification fails, it will time out
                    return
                except Exception as e:
                    self.log.exception("Exception while verifying command", exc_info=True)
                    responseData = self.unknownCommandResponse(interest)
                    self.sendData(responseData, transport)
                    return
        #if we get here, we really don't know this command
        responseData = self.unknownCommandResponse(interest)
        self.sendData(responseData, transport)
        return
                


    @staticmethod
    def getSerial():
        """
            Find and return the serial number of the Raspberry Pi
        """
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('Serial'):
                    return line.split(':')[1].strip()

