#
# Copyright (c) 2004-2008 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.rpath.com/permanent/licenses/CPL-1.0.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#

import os
import re
import stat
import string
import xml.dom.minidom
import zipfile
import gzip as gzip_module
import bz2

from conary import rpmhelper
from conary.lib import elf
from conary.lib import javadeps
from conary.lib import util

class Magic(object):
    __slots__ = ['path', 'basedir', 'contents', 'name']
    # The file type is a generic string for a specific file type
    def __init__(self, path, basedir):
	self.path = path
	self.basedir = basedir
        if not hasattr(self, 'contents'):
            self.contents = {}
	self.name = self.__class__.__name__


class ELF(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)
        fullpath = basedir+path
	self.contents['stripped'] = elf.stripped(fullpath)
        if self.__class__ is ELF:
            # ar doesn't deal with hasDebug or RPATH
            self.contents['hasDebug'] = elf.hasDebug(fullpath)
            self.contents['RPATH'] = elf.getRPATH(fullpath)
            self.contents['Type'] = elf.getType(fullpath)
	requires, provides = elf.inspect(fullpath)
        # Filter None abi flags
        requires = [ x for x in requires
                     if x[0] != 'abi' or x[2][0] is not None ]
        self.contents['requires'] = requires
        self.contents['provides'] = provides
        for req in requires:
            if req[0] == 'abi':
                self.contents['abi'] = req[1:]
                self.contents['isnset'] = req[2][1]
        for prov in provides:
            if prov[0] == 'soname':
                self.contents['soname'] = prov[1]

class ar(ELF):
    def __init__(self, path, basedir='', buffer=''):
	ELF.__init__(self, path, basedir)
	# no point in looking for __.SYMDEF because GNU ar always keeps
	# symbol table up to date
        # ar archives, like ELF files, are investigated by our elf module.
        # We do still want to be able to distinguish between them via magic,
        # thus the two classes.

class tar(Magic):
    def __init__(self, path, basedir = '', buffer = ''):
        Magic.__init__(self, path, basedir)
        self.contents['GNU'] = (buffer[257:265] == 'ustar  \0')

class gzip(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)
	if buffer[3] == '\x08':
	    self.contents['name'] = _string(buffer[10:])
	if buffer[8] == '\x02':
	    self.contents['compression'] = '9'
	else:
	    self.contents['compression'] = '1'

class tar_gz(gzip, tar):
    def __init__(self, path, basedir = '', gzipBuffer = '', tarBuffer = ''):
        gzip.__init__(self, path, basedir = basedir, buffer = gzipBuffer)
        tar.__init__(self, path, basedir = basedir, buffer = tarBuffer)

class bzip(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)
	self.contents['compression'] = buffer[3]

class tar_bz2(bzip, tar):
    def __init__(self, path, basedir = '', bzipBuffer = '', tarBuffer = ''):
        bzip.__init__(self, path, basedir = basedir, buffer = bzipBuffer)
        tar.__init__(self, path, basedir = basedir, buffer = tarBuffer)

class changeset(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)


class jar(Magic):
    def __init__(self, path, basedir='', zipFileObj = None, fileList = []):
	Magic.__init__(self, path, basedir)
        self.contents['files'] = filesMap = {}
        self.contents['provides'] = set()
        self.contents['requires'] = set()

        if zipFileObj is None:
            return

        try:
            for name in fileList:
                contents = zipFileObj.read(name)
                if not _javaMagic(contents):
                    continue
                prov, req = javadeps.getDeps(contents)
                filesMap[name] = (prov, req)
                if prov:
                    self.contents['provides'].add(prov)
                if req:
                    self.contents['requires'].update(req)
        except (IOError, zipfile.BadZipfile):
            # zipfile raises IOError on some malformed zip files
            pass

class WAR(Magic):
    _xmlMetadataFile = "WEB-INF/web.xml"
    def __init__(self, path, basedir='', zipFileObj = None, fileList = []):
        Magic.__init__(self, path, basedir)
        if zipFileObj is None:
            raise ValueError("Expected a Zip file object")
        # Get the contents of the deployment descriptor
        ddcontent = zipFileObj.read(self._xmlMetadataFile)
        try:
            dom = xml.dom.minidom.parseString(ddcontent)
        except Exception, e:
            # Error parsing the XML, move on
            return
        # Grab data from the DOM
        val = dom.getElementsByTagName('display-name')
        if val:
            self.contents['displayName'] = self._getNodeData(val[0])
        val = dom.getElementsByTagName('description')
        if val:
            self.contents['description'] = self._getNodeData(val[0])
        dom.unlink()

    @staticmethod
    def _getNodeData(node):
        node.normalize()
        if not node.hasChildNodes():
            return ''
        return node.childNodes[0].data

class EAR(WAR):
    _xmlMetadataFile = "META-INF/application.xml"

class ZIP(Magic):
    def __init__(self, path, basedir='', zipFileObj = None, fileList = []):
	Magic.__init__(self, path, basedir)


class java(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)
        fullpath = basedir+path
        prov, req = javadeps.getDeps(file(fullpath).read())
        if prov:
            self.contents['provides'] = set([prov])
        if req:
            self.contents['requires'] = req
        self.contents['files'] = { path : (prov, req) }


class script(Magic):
    interpreterRe = re.compile(r'^#!\s*([^\s]*)')
    lineRe = re.compile(r'^#!\s*(.*)')
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)
        m = self.interpreterRe.match(buffer)
        self.contents['interpreter'] = m.group(1)
        m = self.lineRe.match(buffer)
        self.contents['line'] = m.group(1)


class ltwrapper(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)


class CIL(Magic):
    def __init__(self, path, basedir='', buffer=''):
	Magic.__init__(self, path, basedir)

class RPM(Magic):
    _tagMap = [
        ("name",    rpmhelper.NAME, str),
        ("version", rpmhelper.VERSION, str),
        ("release", rpmhelper.RELEASE, str),
        ("epoch",   rpmhelper.EPOCH, int),
        ("arch",    rpmhelper.ARCH, str),
        ("summary", rpmhelper.SUMMARY, str),
        ("description", rpmhelper.DESCRIPTION, str),
        ("license", rpmhelper.LICENSE, str),
    ]
    def __init__(self, path, basedir=''):
	Magic.__init__(self, path, basedir)
        try:
            f = file(path)
        except:
            return None
        # Convert list of objects to simple types
        hdr = rpmhelper.readHeader(f)
        for key, tagName, valType in self._tagMap:
            val = hdr.get(tagName, None)
            if isinstance(val, list):
                if not val:
                    val = None
                else:
                    val = val[0]
            if val is not None:
                if valType == int:
                    val = int(val)
                elif valType == str:
                    val = str(val)
            self.contents[key] = val
        self.contents['isSource'] = hdr.isSource

def _javaMagic(b):
    if len(b) > 4 and b[0:4] == "\xCA\xFE\xBA\xBE":
        return True
    return False

def _tarMagic(b):
    return len(b) > 262 and b[257:262]

def magic(path, basedir=''):
    """
    Returns a magic class with information about the file mentioned
    """
    if basedir and not basedir.endswith('/'):
	basedir += '/'

    n = basedir+path
    if not util.exists(n) or not util.isregular(n):
	return None

    oldmode = None
    mode = os.lstat(n)[stat.ST_MODE]
    if (mode & 0400) != 0400:
        oldmode = mode
        os.chmod(n, mode | 0400)

    f = file(n)
    if oldmode is not None:
        os.chmod(n, oldmode)

    b = f.read(4096)
    f.close()

    if len(b) > 4 and b[0] == '\x7f' and b[1:4] == "ELF":
	return ELF(path, basedir, b)
    elif len(b) > 7 and b[0:7] == "!<arch>":
	return ar(path, basedir, b)
    elif len(b) > 2 and b[0] == '\x1f' and b[1] == '\x8b':
        try:
            uncompressedBuffer = gzip_module.GzipFile(n).read(4096)
            if _tarMagic(uncompressedBuffer):
                return tar_gz(path, basedir, b, uncompressedBuffer)
        except IOError:
            # gzip raises IOError instead of any module specific errors
            pass
        return gzip(path, basedir, b)
    elif len(b) > 3 and b[0:3] == "BZh":
        try:
            uncompressedBuffer = bz2.BZ2File(n).read(4096)
            if _tarMagic(uncompressedBuffer):
                return tar_bz2(path, basedir, b, uncompressedBuffer)
        except IOError:
            # bz2 raises IOError instead of any module specific errors
            pass
        return bzip(path, basedir, b)
    elif len(b) > 4 and b[0:4] == "\xEA\x3F\x81\xBB":
	return changeset(path, basedir, b)
    elif len(b) > 4 and b[0:4] == "PK\x03\x04":
        # Zip file. Peek inside the file to extract the file list
        try:
            zf = zipfile.ZipFile(n)
            namelist = set(i.filename for i in zf.infolist()
                         if not i.filename.endswith('/') and i.file_size > 0)
        except (IOError, zipfile.BadZipfile):
            # zipfile raises IOError on some malformed zip files
            # We are producing a dummy jar or ZIP with no contents
            if path.endswith('.jar'):
                return jar(path, basedir)
            return ZIP(path, basedir)
        if 'META-INF/application.xml' in namelist:
            return EAR(path, basedir, zipFileObj = zf, fileList = namelist)
        elif 'WEB-INF/web.xml' in namelist:
            return WAR(path, basedir, zipFileObj = zf, fileList = namelist)
        elif 'META-INF/MANIFEST.MF' in namelist:
            return jar(path, basedir, zipFileObj = zf, fileList = namelist)
        #elif path.endswith('.par'):
        #    perl archive
        else:
            return ZIP(path, basedir, zipFileObj = zf, fileList = namelist)
    elif _javaMagic(b):
        return java(path, basedir, b)
    elif len(b) > 4 and b[0:2] == "#!":
        if b.find(
            '# This wrapper script should never be moved out of the build directory.\n'
            '# If it is, it will not operate correctly.') > 0:
            return ltwrapper(path, basedir, b)
        return script(path, basedir, _line(b))
    elif (len(b) > 130
          and b[0:2] == 'MZ'
          and b[78:117] == "This program cannot be run in DOS mode."
          and b[128:130] == "PE"):
        # FIXME - this is not sufficient to detect a CIL file this
        # will match all PE executables.  See ECMA-335, partition ii,
        # section 25
        return CIL(path, basedir, b)
    elif (len(b) > 4 and b[:4] == "\xed\xab\xee\xdb"):
        return RPM(path, basedir)
    elif _tarMagic(b):
        return tar(path, basedir, b)

    return None

class magicCache(dict):
    def __init__(self, basedir=''):
	self.basedir = basedir
    def __getitem__(self, name):
	if name not in self:
	    self[name] = magic(name, self.basedir)
	return dict.__getitem__(self, name)

# internal helpers

def _string(buffer):
    return buffer[:string.find(buffer, '\0')]

def _line(buffer):
    return buffer[:string.find(buffer, '\n')]
