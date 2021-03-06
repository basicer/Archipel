# -*- coding: utf-8 -*-
#
# appliancecompresser.py
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

import gzip
import os
import shutil
import tarfile
import tempfile
from threading import Thread


class TNApplianceCompresser (Thread):

    def __init__(self, name, paths, xml_definition, xml_snapshots, working_dir, vm_dir, hypervisor_repo_path, callback, entity):
        """
        Initialize a TNApplianceCompresser.
        @type name: string
        @param name: the name of the package
        @type paths: array
        @param paths: the paths of things to compress
        @type xml_definition: xmpp.Node
        @param xml_definition: the XML description of virtual machine
        @type xml_snapshots: xmpp.Node
        @param xml_snapshots: the XML description of snapshot
        @type working_dir: string
        @param working_dir: the path of the working dir
        @type vm_dir: string
        @param vm_dir: the path of the vm folder
        @type hypervisor_repo_path: string
        @param hypervisor_repo_path: the path for the hypervisor repository
        @type callback: function
        @param callback: called when compression is done
        """
        Thread.__init__(self)
        self.name                   = name.replace(" ", "_").replace("/", "_").replace("\\", "_").replace("..", "_")
        self.paths                  = paths
        self.callback               = callback
        self.xml_definition         = xml_definition
        self.xml_snapshots          = xml_snapshots
        self.vm_dir                 = vm_dir
        self.working_dir            = tempfile.mkdtemp(dir=working_dir)
        self.hypervisor_repo_path   = hypervisor_repo_path
        self.entity                 = entity
        self.entity.log.debug("TNApplianceCompresser: working temp dir is: %s" % self.working_dir)

    def run(self):
        """
        run the thread
        """
        self.entity.log.info("TNApplianceCompresser: packaging appliance %s" % self.name)
        self.entity.log.info("TNApplianceCompresser: creating tar file at : %s/%s.xvm2" % (self.working_dir, self.name))
        tar_file = self.working_dir + "/" + self.name + ".xvm2"
        tar = tarfile.open(tar_file, "w")
        self.xml_definition.getTag("description").setData("")
        definitionXML = str(self.xml_definition).replace('xmlns="http://www.gajim.org/xmlns/undeclared" ', '')
        self.entity.log.info("TNApplianceCompresser: writing definion at path  %s/description.xml" % self.working_dir)
        f = open(self.working_dir + "/description.xml", 'w')
        f.write(definitionXML)
        f.close()
        tar.add(self.working_dir + "/description.xml", "/description.xml")
        os.unlink(self.working_dir + "/description.xml")
        for i, snapshot in enumerate(self.xml_snapshots):
            snapshotXML = str(snapshot).replace('xmlns="http://www.gajim.org/xmlns/undeclared" ', '')
            descName = "/snapshot-%d.xml" % i
            descPath = "%s/%s" % (self.working_dir, descName)
            f = open(descPath, 'w')
            f.write(snapshotXML)
            f.close()
            tar.add(descPath, descName)
            os.unlink(descPath)
        zipped_file_paths = []
        for path in self.paths:
            self.entity.log.info("TNApplianceCompresser: zipping file %s" % path)
            zipped_file_path = self.compress_disk(path)
            definitionXML = definitionXML.replace(path, zipped_file_path.split('/')[-1])
            zipped_file_paths.append(zipped_file_path)
            self.entity.log.info("TNApplianceCompresser: file zipped %s" % zipped_file_path)
        for zipped_file_path in zipped_file_paths:
            self.entity.log.info("TNApplianceCompresser: adding to tar file %s" % zipped_file_path)
            tar.add(zipped_file_path, "/" + zipped_file_path.split("/")[-1])
            os.unlink(zipped_file_path)
        tar.close()
        self.entity.log.info("TNApplianceCompresser: moving the tar file %s to repo %s" % (tar_file, self.hypervisor_repo_path))
        shutil.move(tar_file, self.hypervisor_repo_path)
        self.entity.log.info("TNApplianceCompresser: cleaning the working temp dir")
        shutil.rmtree(self.working_dir)
        self.callback()

    def compress_disk(self, path):
        """
        Perform compression of disk.
        @type path: string
        @param path: the relative disk path
        @rtype: string
        @return: zipped disk path
        """
        zip_path = self.working_dir + "/" + path.split("/")[-1] + ".gz"
        f_in = open(path, 'rb')
        f_out = gzip.open(zip_path, 'wb')
        f_out.writelines(f_in)
        f_out.close()
        f_in.close()
        return zip_path