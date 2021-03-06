# -*- coding: utf-8 -*-
#
# archipelHypervisor.py
#
# Copyright (C) 2010 Antoine Mercadal <antoine.mercadal@inframonde.eu>
# Copyright, 2011 - Franck Villaume <franck.villaume@trivialdev.com>
# This file is part of ArchipelProject
# http://archipelproject.org
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Contains L{TNArchipelHypervisor}, the entities uses for hypervisor

This provides the possibility to instanciate TNArchipelVirtualMachines
"""
import datetime
import libvirt
import random
import sqlite3
import string
import uuid as moduuid
import xmpp
from threading import Thread

from archipelcore.archipelAvatarControllableEntity import TNAvatarControllableEntity
from archipelcore.archipelEntity import TNArchipelEntity
from archipelcore.archipelHookableEntity import TNHookableEntity
from archipelcore.archipelPermissionCenter import TNArchipelPermissionCenter
from archipelcore.archipelTaggableEntity import TNTaggableEntity
from archipelcore.utils import build_error_iq, build_error_message

from archipelLibvirtEntity import ARCHIPEL_NS_LIBVIRT_GENERIC_ERROR
from archipelVirtualMachine import TNArchipelVirtualMachine
import archipelLibvirtEntity


# Errors
ARCHIPEL_ERROR_CODE_HYPERVISOR_ALLOC            = -9001
ARCHIPEL_ERROR_CODE_HYPERVISOR_FREE             = -9002
ARCHIPEL_ERROR_CODE_HYPERVISOR_ROSTER           = -9003
ARCHIPEL_ERROR_CODE_HYPERVISOR_CLONE            = -9004
ARCHIPEL_ERROR_CODE_HYPERVISOR_IP               = -9005
ARCHIPEL_ERROR_CODE_HYPERVISOR_LIBVIRT_URI      = -9006
ARCHIPEL_ERROR_CODE_HYPERVISOR_ALLOC_MIGRATION  = -9007
ARCHIPEL_ERROR_CODE_HYPERVISOR_FREE_MIGRATION   = -9008
ARCHIPEL_ERROR_CODE_HYPERVISOR_CAPABILITIES     = -9009

# Namespace
ARCHIPEL_NS_HYPERVISOR_CONTROL                  = "archipel:hypervisor:control"

# XMPP shows
ARCHIPEL_XMPP_SHOW_ONLINE                       = "Online"

# XMPP groups
ARCHIPEL_XMPP_GROUP_VM                          = "virtualmachines"
ARCHIPEL_XMPP_GROUP_HYPERVISOR                  = "hypervisors"
ARCHIPEL_XMPP_GROUP_CONTROLLER                  = "controllers"


class TNThreadedVirtualMachine (Thread):
    """
    This class is used to run L{ArchipelVirtualMachine} main loop
    in a thread.
    """

    def __init__(self, jid, password, hypervisor, configuration, name):
        """
        The contructor of the class.
        @type jid: string
        @param jid: the jid of the L{TNArchipelVirtualMachine}
        @type password: string
        @param password: the password associated to the JID
        @type hypervisor: L{TNArchipelHypervisor}
        @param hypervisor: the hypervisor of the VM
        @type name: string
        @param name: the name of the VM
        """
        Thread.__init__(self)
        self.jid = jid
        self.password = password
        self.xmppvm = TNArchipelVirtualMachine(self.jid, self.password, hypervisor, configuration, name)

    def get_instance(self):
        """
        This method returns the current L{TNArchipelVirtualMachine} instance.
        @rtype: ArchipelVirtualMachine
        @return: the L{ArchipelVirtualMachine} instance
        """
        return self.xmppvm

    def run(self):
        """
        Overiddes sur super class method. Do the L{TNArchipelVirtualMachine} main loop.
        """
        self.xmppvm.connect()
        self.xmppvm.loop()


class TNArchipelHypervisor (TNArchipelEntity, archipelLibvirtEntity.TNArchipelLibvirtEntity, TNHookableEntity, TNAvatarControllableEntity, TNTaggableEntity):
    """
    This class represents an Hypervisor XMPP Capable. This is an XMPP client
    that allows to alloc threaded instance of XMPP Virtual Machine, destroy already
    active XMPP VM, and remember which have been created.
    """

    def __init__(self, jid, password, configuration, name, database_file="./database.sqlite3"):
        """
        This is the constructor of the class.
        @type jid: string
        @param jid: the jid of the hypervisor
        @type password: string
        @param password: the password associated to the JID
        @type name: string
        @param name: the name of the hypervisor
        @type database_file: string
        @param database_file: the sqlite3 file to store existing VM for persistance
        """
        TNArchipelEntity.__init__(self, jid, password, configuration, name)
        archipelLibvirtEntity.TNArchipelLibvirtEntity.__init__(self, configuration)

        self.virtualmachines            = {}
        self.database_file              = database_file
        self.xmppserveraddr             = self.jid.getDomain()
        self.entity_type                = "hypervisor"
        self.default_avatar             = self.configuration.get("HYPERVISOR", "hypervisor_default_avatar")
        self.libvirt_event_callback_id  = None

        # libvirt connection
        self.connect_libvirt()

        self.vcard_infos                = {}
        if (self.configuration.has_section("VCARD")):
            for key in ("orgname", "userid", "locality", "url"):
                if self.configuration.has_option("VCARD", key):
                    self.vcard_infos[key.upper()] = self.configuration.get("VCARD", key)
        self.vcard_infos["TITLE"] = "Hypervisor (%s)" % self.current_hypervisor()

        # permissions
        permission_db_file              = self.configuration.get("HYPERVISOR", "hypervisor_permissions_database_path")
        permission_admin_names          = self.configuration.get("GLOBAL", "archipel_root_admins").split()
        self.permission_center          = TNArchipelPermissionCenter(permission_db_file, permission_admin_names)
        self.init_permissions()

        names_file = open(self.configuration.get("HYPERVISOR", "name_generation_file"), 'r')
        self.generated_names = names_file.readlines()
        names_file.close()
        self.number_of_names = len(self.generated_names) - 1

        self.log.info("Server address defined as %s" % self.xmppserveraddr)

        # hooks
        self.create_hook("HOOK_HYPERVISOR_ALLOC")
        self.create_hook("HOOK_HYPERVISOR_FREE")
        self.create_hook("HOOK_HYPERVISOR_MIGRATEDVM_LEAVE")
        self.create_hook("HOOK_HYPERVISOR_MIGRATEDVM_ARRIVE")
        self.create_hook("HOOK_HYPERVISOR_CLONE")

        # vocabulary
        self.init_vocabulary()

        # module inits
        self.initialize_modules('archipel.plugin.core')
        self.initialize_modules('archipel.plugin.hypervisor')

        if self.is_hypervisor((archipelLibvirtEntity.ARCHIPEL_HYPERVISOR_TYPE_QEMU, archipelLibvirtEntity.ARCHIPEL_HYPERVISOR_TYPE_XEN)):
            try:
                self.libvirt_event_callback_id = self.libvirt_connection.domainEventRegisterAny(None, libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE, self.hypervisor_on_domain_event, None)
            except libvirt.libvirtError:
                self.log.error("We are sorry. But your hypervisor doesn't support libvirt virConnectDomainEventRegisterAny. And this really bad. I'm sooo sorry")
        else:
            self.log.warning("Your hypervisor doesn't support libvirt eventing. Using fake event loop.")
        self.capabilities = self.get_capabilities()

        # persistance
        self.manage_persistance()

        # action on auth
        self.register_hook("HOOK_ARCHIPELENTITY_XMPP_AUTHENTICATED", method=self.manage_vcard_hook)
        self.register_hook("HOOK_ARCHIPELENTITY_XMPP_AUTHENTICATED", method=self.update_presence)

    def update_presence(self, origin=None, user_info=None, parameters=None):
        """
        Set the presence of the hypervisor.
        @type origin: L{TNArchipelEntity}
        @param origin: the origin of the hook
        @type user_info: object
        @param user_info: random user info
        @type parameters: object
        @param parameters: runtime arguments
        """
        count   = len(self.virtualmachines)
        status  = ARCHIPEL_XMPP_SHOW_ONLINE + " (" + str(count) + ")"
        self.change_presence(self.xmppstatusshow, status)

    def register_handlers(self):
        """
        This method overrides the defaut register_handler of the super class.
        """
        TNArchipelEntity.register_handlers(self)
        self.xmppclient.RegisterHandler('iq', self.process_iq, ns=ARCHIPEL_NS_HYPERVISOR_CONTROL)

    def unregister_handlers(self):
        """
        Unregister the handlers.
        """
        TNArchipelEntity.unregister_handlers(self)
        self.xmppclient.UnregisterHandler('iq', self.process_iq, ns=ARCHIPEL_NS_HYPERVISOR_CONTROL)


    def init_vocabulary(self):
        """
        Initialize the base vocabulary.
        """
        TNArchipelEntity.init_vocabulary(self)
        registrar_items = [
                            {   "commands" : ["capabilities"],
                                "parameters": [],
                                "method": self.message_capabilities,
                                "permissions": ["capabilities"],
                                "description": "Get my libvirt capabilities" },
                            {   "commands" : ["libvirt uri"],
                                "parameters": [],
                                "method": self.message_libvirt_uri,
                                "permissions": ["uri"],
                                "description": "Get my libvirt URI" },
                            {   "commands" : ["ip"],
                                "parameters": [],
                                "method": self.message_ip,
                                "permissions": ["ip"],
                                "description": "Get my IP address" },
                            {   "commands" : ["roster", "vms", "virtual machines", "domains"],
                                "parameters": [],
                                "method": self.message_roster,
                                "permissions": ["rostervm"],
                                "description": "Get the content of my roster" },
                            {   "commands" : ["alloc"],
                                "parameters": [{"name": "name", "description": "The name of the vm. If not given, it will be generated"}],
                                "method": self.message_alloc,
                                "permissions": ["alloc"],
                                "description": "Allocate a new virtual machine" },
                            {   "commands" : ["free"],
                                "parameters": [{"name": "identifier", "description": "The name or the UUID of the vm to free"}],
                                "method": self.message_free,
                                "permissions": ["free"],
                                "description": "Free a virtual machine" },
                            {   "commands" : ["clone"],
                                "parameters": [{"name": "identifier", "description": "The name or the UUID of the vm to clone"}],
                                "method": self.message_clone,
                                "permissions": ["clone"],
                                "description": "Clone a virtual machine" }
                            ]

        self.add_message_registrar_items(registrar_items)

    def init_permissions(self):
        """
        Initialize the permissions.
        """
        TNArchipelEntity.init_permissions(self)
        self.permission_center.create_permission("alloc", "Authorizes users to allocate new virtual machines", False)
        self.permission_center.create_permission("free", "Authorizes users to free allocated virtual machines", False)
        self.permission_center.create_permission("rostervm", "Authorizes users to access the hypervisor's roster", False)
        self.permission_center.create_permission("clone", "Authorizes users to clone virtual machines", False)
        self.permission_center.create_permission("ip", "Authorizes users to get hypervisor's IP address", False)
        self.permission_center.create_permission("uri", "Authorizes users to get the hypervisor's libvirt URI", False)
        self.permission_center.create_permission("capabilities", "Authorizes users to access the hypervisor capabilities", False)

    def manage_persistance(self):
        """
        If the database_file parameter contains a valid populated sqlite3 database,
        this method will recreate all the old L{TNArchipelVirtualMachine}. If not, it will create a
        blank database file.
        """
        self.log.info("opening database file %s" % self.database_file)
        self.database = sqlite3.connect(self.database_file, check_same_thread=False)
        self.log.info("Populating database if not exists.")
        self.database.execute("create table if not exists virtualmachines (jid text, password text, creation_date date, comment text, name text)")
        c = self.database.cursor()
        c.execute("select * from virtualmachines")
        for vm in c:
            string_jid, password, date, comment, name = vm
            jid = xmpp.JID(string_jid)
            jid.setResource(self.jid.getNode())
            vm_thread = self.create_threaded_vm(jid, password, name)
            self.virtualmachines[vm_thread.jid.getNode()] = vm_thread.get_instance()
            vm_thread.start()

    def create_threaded_vm(self, jid, password, name):
        """
        This method creates a threaded L{TNArchipelVirtualMachine}, starts it and returns the Thread instance.
        @type jid: string
        @param jid: the JID of the L{TNArchipelVirtualMachine}
        @type password: string
        @param password: the password associated to the JID
        @rtype: L{TNThreadedVirtualMachine}
        @return: a L{TNThreadedVirtualMachine} instance of the virtual machine
        """
        return TNThreadedVirtualMachine(jid, password, self, self.configuration, name)

    def generate_name(self):
        """
        Get a random name from the names file.
        @rtype: string
        @return: a generated name
        """
        search = True
        currentName = None
        while search:
            currentName = self.generated_names[random.randint(0, self.number_of_names)].replace("\n", "")
            if not self.get_vm_by_name(currentName):
                self.log.info("Trying to use generate name %s but it's already taken. Generating another one." % currentName)
                search = False
        return currentName

    def get_vm_by_name(self, name):
        """
        Return the vm object by name.
        @type name : string
        @param name: the name of the vm
        @rtype: L{TNArchipelVirtualMachine}
        @return: the virtual machine or None
        """
        for uuid, vm in self.virtualmachines.iteritems():
            if vm.name.upper() == name.upper():
                return vm
        return None

    def get_vm_by_uuid(self, uuid):
        """
        Return the vm object by uuid.
        @type uuid : string
        @param uuid: the uuid of the vm
        @rtype: L{TNArchipelVirtualMachine}
        @return: the virtual machine or None
        """
        if not uuid.lower() in self.virtualmachines:
            return None
        return self.virtualmachines[uuid.lower()]

    def get_vm_by_identifer(self, identifier):
        """
        Return the vm object by identifier. Identifier can be the UUID of the name.
        @type identifier : string
        @param identifier: the identifier of the vm
        @rtype: L{TNArchipelVirtualMachine}
        @return: the virtual machine or None
        """
        vm = self.get_vm_by_name(identifier)
        if not vm:
            vm = self.get_vm_by_uuid(identifier)
        return vm


    ### LIBVIRT events Processing

    def hypervisor_on_domain_event(self, conn, dom, event, detail, opaque):
        """
        Trigger when a domain trigger vbent. We care only about RESUMED and SHUTDOWNED from MIGRATED.
        """
        if event == libvirt.VIR_DOMAIN_EVENT_STOPPED and detail == libvirt.VIR_DOMAIN_EVENT_STOPPED_MIGRATED:
            try:
                strdesc = dom.XMLDesc(0)
                desc    = xmpp.simplexml.NodeBuilder(data=strdesc).getDom()
                vmjid   = desc.getTag(name="description").getCDATA().split("::::")[0]
                self.log.info("MIGRATION: Virtual machine %s stopped because of live migration. Freeing softly." % vmjid)
                self.free_for_migration(xmpp.JID(vmjid))
                self.perform_hooks("HOOK_HYPERVISOR_MIGRATEDVM_LEAVE", vmjid)
            except Exception as ex:
                self.log.error("MIGRATION: Can't free softly this virtual machine: %s" % str(ex))

        elif event == libvirt.VIR_DOMAIN_EVENT_RESUMED and detail == libvirt.VIR_DOMAIN_EVENT_RESUMED_MIGRATED:
            try:
                strdesc = dom.XMLDesc(0)
                desc    = xmpp.simplexml.NodeBuilder(data=strdesc).getDom()
                vmjid   = desc.getTag(name="description").getCDATA().split("::::")[0]
                vmpass  = desc.getTag(name="description").getCDATA().split("::::")[1]
                vmname  = desc.getTag(name="name").getCDATA()
                self.log.info("MIGRATION: Virtual machine %s resumed from live migration. Allocating softly." % vmjid)
                self.alloc_for_migration(xmpp.JID(vmjid), vmname, vmpass)
                self.perform_hooks("HOOK_HYPERVISOR_MIGRATEDVM_ARRIVE", vmjid)
            except Exception as ex:
                self.log.warning("MIGRATION: Can't alloc softly this virtual machine. Maybe it is not an archipel VM: %s" % str(ex))


    ### XMPP Processing

    def process_iq(self, conn, iq):
        """
        This method is invoked when a ARCHIPEL_NS_HYPERVISOR_CONTROL IQ is received.
        It understands IQ of type:
            - alloc
            - free
        @type conn: xmpp.Dispatcher
        @param conn: ths instance of the current connection that send the stanza
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        """
        reply = None
        action = self.check_acp(conn, iq)

        # temp fix to authorize migration
        # We should find a way to authorize
        # hypervisors to ask uri with another way
        if not action in ('uri'):
            self.check_perm(conn, iq, action, -1)

        if action == "alloc":
            reply = self.iq_alloc(iq)
        elif action == "free":
            reply = self.iq_free(iq)
        elif action == "rostervm":
            reply = self.iq_roster(iq)
        elif action == "clone":
            reply = self.iq_clone(iq)
        elif action == "ip":
            reply = self.iq_ip(iq)
        elif action == "uri":
            reply = self.iq_libvirt_uri(iq)
        elif action == "capabilities":
            reply = self.iq_capabilities(iq)

        if reply:
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed


    ###  Hypervisor controls

    def alloc(self, requester=None, requested_name=None, start=True):
        """
        Alloc a new XMPP entity.
        @type requester: xmpp.JID
        @param requester: the JID of the migrated VM to alloc
        @type requested_name: string
        @param requested_name: the requested name for the VM if None, will be generated
        @type start: Boolean
        @param start: if True, start the vm immediatly
        @rtype: L{TNArchipelVirtualMachine} or L{TNThreadedVirtualMachine}
        @return: L{TNArchipelVirtualMachine} if start==True or L{TNThreadedVirtualMachine} if start==False
        """
        vmuuid = str(moduuid.uuid1())
        vm_password = ''.join([random.choice(string.letters + string.digits) for i in range(self.configuration.getint("VIRTUALMACHINE", "xmpp_password_size"))])
        vm_jid = xmpp.JID(node=vmuuid.lower(), domain=self.xmppserveraddr.lower(), resource=self.jid.getNode().lower())
        disallow_spaces_in_name = (self.configuration.has_option("VIRTUALMACHINE", "allow_blank_space_in_vm_name") and not self.configuration.getboolean("VIRTUALMACHINE", "allow_blank_space_in_vm_name"))

        if not requested_name:
            name = self.generate_name()
        else:
            if disallow_spaces_in_name:
                requested_name = requested_name.replace(" ", "-")
            if not self.get_vm_by_name(requested_name):
                name = requested_name
            else:
                raise Exception("This hypervisor already has virtual machine named %s. Please, choose another one." % requested_name)

        if disallow_spaces_in_name:
            name = name.replace(" ", "-")

        self.log.info("Starting xmpp threaded virtual machine.")
        vm_thread = self.create_threaded_vm(vm_jid, vm_password, name)
        vm = vm_thread.get_instance()

        if requester:
            self.log.info("Adding the requesting controller %s to the VM's roster." % (str(requester)))
            vm.register_hook("HOOK_ARCHIPELENTITY_XMPP_AUTHENTICATED", method=vm.add_jid_hook, user_info=xmpp.JID(requester), oneshot=True)
            vm.permission_center.grant_permission_to_user("all", requester.getStripped())

        self.log.info("Registering the new VM in hypervisor's database.")
        self.database.execute("insert into virtualmachines values(?,?,?,?,?)", (str(vm_jid.getStripped()), vm_password, datetime.datetime.now(), '', name))
        self.database.commit()
        self.virtualmachines[vmuuid] = vm

        self.update_presence()
        self.log.info("XMPP Virtual Machine instance sucessfully initialized.")
        self.perform_hooks("HOOK_HYPERVISOR_ALLOC", vm)
        self.push_change("hypervisor", "alloc")
        if start:
            vm_thread.start()
            return vm
        else:
            return vm_thread

    def alloc_for_migration(self, jid, name, password):
        """
        Perform light allocation (no registration, no subscription).
        @type jid: xmpp.JID
        @param jid: the JID of the migrated VM to alloc
        @type name: string
        @param name: the name of the migrated VM to alloc
        @type password: string
        @param password: the password of the migrated VM to alloc
        """
        uuid = jid.getNode()

        jid.setResource(self.jid.getNode())
        self.log.info("Starting xmpp threaded virtual machine with incoming jid : %s" % jid)
        vm_thread = self.create_threaded_vm(jid, password, name)
        vm = vm_thread.get_instance()
        vm_thread.start()
        self.log.info("Registering the new VM in hypervisor's database.")
        self.database.execute("insert into virtualmachines values(?,?,?,?,?)", (str(jid.getStripped()), password, datetime.datetime.now(), '', name))
        self.database.commit()
        self.virtualmachines[uuid] = vm

        self.update_presence()
        self.log.info("Migrated XMPP VM is ready.")
        return vm

    def free(self, jid):
        """
        Remove the XMPP container of VM with given jid.
        @type jid: xmpp.JID
        @param jid: the JID of the VM to free
        """
        uuid    = jid.getNode()
        vm      = self.virtualmachines[uuid]

        if vm.is_migrating:
            raise Exception("Virtual machine is migrating. Can't free.")
        if vm.domain and (vm.domain.info()[0] == 1 or vm.domain.info()[0] == 2 or vm.domain.info()[0] == 3):
            vm.domain.destroy()
        if vm.domain:
            vm.domain.undefine()

        self.log.info("Launch %s's terminate method." % jid)
        vm.terminate()

        self.log.info("Unregistering the VM from hypervisor's database.")
        self.database.execute("delete from virtualmachines where jid=?", (jid.getStripped(),))
        self.database.commit()

        del self.virtualmachines[uuid]

        self.log.info("Starting the vm removing procedure.")
        vm.inband_unregistration()
        self.perform_hooks("HOOK_HYPERVISOR_FREE", vm)
        self.log.info("XMPP Virtual Machine %s sucessfully destroyed." % jid)
        self.push_change("hypervisor", "free")
        self.update_presence()

    def free_for_migration(self, jid):
        """
        Perform light free (no removing of account, no unsubscription).
        @type jid: xmpp.JID
        @param jid: the JID of the migrated VM to free
        """
        uuid    = jid.getNode()
        vm      = self.virtualmachines[uuid]

        vm.undefine_and_disconnect()

        self.log.info("Unregistering the VM from hypervisor's database.")
        self.database.execute("delete from virtualmachines where jid='%s'" % jid.getStripped())
        self.database.commit()
        del self.virtualmachines[uuid]
        self.update_presence()

    def clone(self, uuid, requester, wanted_name=None):
        """
        Clone a existing virtual machine.
        @type uuid: string
        @param uuid: the uuid of the VM to clone
        @type requester: xmpp.JID
        @param requester: JID of the requester
        """
        xmppvm = self.get_vm_by_uuid(uuid)
        xmldesc = xmppvm.definition

        if not xmldesc:
            raise Exception('The mother vm has to be defined to be cloned.')

        dominfo = xmppvm.domain.info()
        if not (dominfo[0] == libvirt.VIR_DOMAIN_SHUTOFF or dominfo[0] == libvirt.VIR_DOMAIN_SHUTDOWN):
            raise Exception('The mother vm has to be stopped to be cloned.')

        if not wanted_name:
            name = "%s (clone of %s)" % (self.generate_name(), xmppvm.name)
        else:
            name = wanted_name

        newvm_thread = self.alloc(requester, requested_name=name, start=False)
        newvm = newvm_thread.get_instance()
        newvm.register_hook("HOOK_VM_INITIALIZE",
                            method=newvm.clone,
                            user_info={"definition": xmldesc, "path": xmppvm.folder, "parentuuid": uuid, "parentname": self.name},
                            oneshot=True)
        newvm_thread.start()
        self.perform_hooks("HOOK_HYPERVISOR_CLONE", newvm)
        self.push_change("hypervisor", "clone")

    def get_capabilities(self):
        """
        Return hypervisor's capabilities.
        """
        capp = xmpp.simplexml.NodeBuilder(data=self.libvirt_connection.getCapabilities()).getDom()
        return capp

    def migration_libvirt_uri(self):
        """
        Return the libvirt URI
        """
        if self.configuration.has_option("GLOBAL", "migration_uri"):
            return self.configuration.get("GLOBAL", "migration_uri")
        else:
            return self.local_libvirt_uri.replace("///", "//%s/" % self.ipaddr)


    ###  Hypervisor IQs

    def iq_alloc(self, iq):
        """
        This method creates a threaded L{TNArchipelVirtualMachine} with UUID given
        as payload in IQ and register the hypervisor and the iq sender in
        the VM's roster.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            try:
                requested_name = iq.getTag("query").getTag("archipel").getAttr("name")
            except:
                requested_name = None
            vm = self.alloc(iq.getFrom(), requested_name=requested_name)
            reply = iq.buildReply("result")
            payload = xmpp.Node("virtualmachine", attrs={"jid": str(vm.jid.getStripped())})
            reply.setQueryPayload([payload])
            self.shout("virtualmachine", "A new Archipel Virtual Machine has been created by %s with uuid %s" % (iq.getFrom(), vm.uuid))
        except libvirt.libvirtError as ex:
            reply = build_error_iq(self, ex, iq, ex.get_error_code(), ns=ARCHIPEL_NS_LIBVIRT_GENERIC_ERROR)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_ALLOC)
        return reply

    def message_alloc(self, msg):
        """
        Handle the allocation request message.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            tokens = msg.getBody().split(None, 1)
            name = None
            if len(tokens) == 2:
                name = tokens[1]
            else:
                name = None
            vm = self.alloc(msg.getFrom(), name)
            return "Archipel VM with name %s has been allocated using JID %s" % (vm.name, vm.jid)
        except Exception as ex:
            return build_error_message(self, ex)

    def iq_alloc_for_migration(self, iq):
        """
        Perform light allocation for handler migrating vm.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            reply = iq.buildReply("result")
            vmjid = xmpp.JID(iq.getTag("query").getTag("archipel").getAttr("jid"))
            name = iq.getTag("query").getTag("archipel").getAttr("name")
            password = iq.getTag("query").getTag("archipel").getAttr("password")

            self.alloc_for_migration(vmjid, name, password)

            self.push_change("hypervisor", "migrate")
            self.shout("virtualmachine", "The virtual machine %s has been migrated from hypervisor %s" % (vmjid, iq.getFrom()))
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_ALLOC_MIGRATION)
        return reply

    def iq_free(self, iq):
        """
        This method destroy a threaded L{TNArchipelVirtualMachine} with UUID given
        as payload in IQ and remove it from the hypervisor roster.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        reply = iq.buildReply("result")
        try:
            vm_jid      = xmpp.JID(jid=iq.getTag("query").getTag("archipel").getAttr("jid"))
            domain_uuid = vm_jid.getNode()
            self.free(vm_jid)
            reply.setQueryPayload([xmpp.Node(tag="virtualmachine", attrs={"jid": vm_jid})])
            self.shout("virtualmachine", "The Archipel Virtual Machine %s has been destroyed by %s" % (domain_uuid, iq.getFrom()))
        except libvirt.libvirtError as ex:
            reply = build_error_iq(self, ex, iq, ex.get_error_code(), ns=ARCHIPEL_NS_LIBVIRT_GENERIC_ERROR)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_FREE)
        return reply

    def message_free(self, msg):
        """
        Handle the free request message.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            tokens = msg.getBody().split(None, 1)
            if not len(tokens) == 2: return "I'm sorry, you use a wrong format. You can type 'help' to get help"
            identifier = tokens[1]
            vm = self.get_vm_by_identifer(identifier)
            if not vm: return "It seems that vm with identifer %s doesn't exists." % identifier
            self.free(vm.jid)
            return "Archipel VM with JID %s has been freed." % (vm.jid)
        except Exception as ex:
            return build_error_message(self, ex)

    def iq_free_for_migration(self, iq):
        """
        Perform light free for virtual machine migration.
        """
        try:
            reply = iq.buildReply("result")
            vmjid = xmpp.JID(iq.getTag("query").getTag("archipel").getAttr("jid"))
            self.free_for_migration(vmjid)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_FREE_MIGRATION)
        return reply

    def iq_clone(self, iq):
        """
        Alloc a virtual as a clone of another.
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            wanted_name = None
            reply = iq.buildReply("result")
            vmjid = xmpp.JID(jid=iq.getTag("query").getTag("archipel").getAttr("jid"))
            vmuuid = vmjid.getNode()
            if iq.getTag("query").getTag("archipel").getAttr("name"):
                wanted_name = iq.getTag("query").getTag("archipel").getAttr("name")
            self.clone(vmuuid, iq.getFrom(), wanted_name)
            self.shout("virtualmachine", "The Archipel Virtual Machine %s has been cloned by %s" % (vmuuid, iq.getFrom()))
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_CLONE)
        return reply

    def message_clone(self, msg):
        """
        Handle the clone request message.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            tokens = msg.getBody().split(None, 1)
            if not len(tokens) == 2: return "I'm sorry, you use a wrong format. You can type 'help' to get help"
            identifier = tokens[1]
            vm = self.get_vm_by_identifer(identifier)
            if not vm: return "It seems that vm with identifer %s doesn't exists." % identifier
            self.clone(vm.uuid, msg.getFrom())
            return "Cloning of virtual machine %s has started." % (vm.jid)
        except Exception as ex:
            return build_error_message(self, ex)

    def iq_roster(self, iq):
        """
        Send the hypervisor roster content.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            reply = iq.buildReply("result")
            nodes = []
            for uuid, vm in self.virtualmachines.iteritems():
                n = xmpp.Node("item")
                n.addData(vm.jid.getStripped())
                nodes.append(n)
            reply.setQueryPayload(nodes)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_ROSTER)
        return reply

    def message_roster(self, msg):
        """
        Process the roster message request.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            ret = "Here is the content of my roster:\n"
            for uuid, vm in self.virtualmachines.iteritems():
                ret += " - %s (%s)\n" % (vm.name, vm.jid)
            return ret
        except Exception as ex:
            return build_error_message(self, ex)

    def iq_ip(self, iq):
        """
        Send the hypervisor IP address.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            reply = iq.buildReply("result")
            reply.getTag("query").addChild(name="ip", payload=self.ipaddr)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_IP)
        return reply

    def message_ip(self, msg):
        """
        Process the IP message request.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            return "Sure, my IP is %s" % self.ipaddr
        except Exception as ex:
            return build_error_message(self, ex)

    def iq_libvirt_uri(self, iq):
        """
        Send the hypervisor IP address.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            network_libvirt_uri = self.migration_libvirt_uri()
            reply = iq.buildReply("result")
            reply.getTag("query").addChild(name="uri", payload=network_libvirt_uri)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_LIBVIRT_URI)
        return reply

    def message_libvirt_uri(self, msg):
        """
        Process the libvirt URI message request.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            return "Sure, my libvirt URI is %s" % self.migration_libvirt_uri()
        except Exception as ex:
            return build_error_message(self, ex)

    def iq_capabilities(self, iq):
        """
        Send the hypervisor capabilities.
        @type iq: xmpp.Protocol.Iq
        @param iq: the sender request IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready-to-send IQ containing the results
        """
        try:
            reply = iq.buildReply("result")
            reply.setQueryPayload(self.capabilities)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_HYPERVISOR_CAPABILITIES)
        return reply

    def message_capabilities(self, msg):
        """
        Process the capabilities message request.
        @type msg: xmpp.Protocol.Message
        @param msg: the received message
        @rtype: xmpp.Protocol.Message
        @return: a ready to send Message containing the result of the action
        """
        try:
            return str(self.capabilities)
        except Exception as ex:
            return build_error_message(self, ex)