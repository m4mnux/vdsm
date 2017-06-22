#
# Copyright 2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

"""
This module allows to store and retrieve key/value pairs into the etree
representation of a libvirt domain XML. Each set of key/value pairs will be
stored under one first-level child of the metadata. Example:

  <metadata>
    <group1>
      <a>1</a>
      <b>2</b>
    </group1>
    <group2>
      <c>3</c>
      <d>4</d>
    </group2>
  <metadata>

The key/value pairs must comply with those requirements:
- keys must be python basestrings
- values must be one of: basestring, int, float
- containers are not supported values; the metadata
  namespace is flat, and you cannot nest objects.
- partial updates are forbidden. You must overwrite all the key/value
  pairs in a given set (hereafter referred as 'group') at the same time.

The flow is:
1. read the metadata using this module
2. update the data you need to work with
3. send back the metadata using this module
"""

from contextlib import contextmanager
import xml.etree.ElementTree as ET

import libvirt
import six

from vdsm.common import conv
from vdsm.common import errors
from vdsm.virt import vmxml
from vdsm.virt import xmlconstants


_CUSTOM = 'custom'
_DEVICE = 'device'


class Error(errors.Base):
    """
    Generic metadata error
    """


class UnsupportedType(Error):
    """
    Unsupported python type. Supported python types are:
    * ints
    * floats
    * string
    """


class MissingDevice(Error):
    """
    Failed to uniquely identify one device using the given attributes.
    """


class Metadata(object):
    """
    Use this class to load or dump a group (see the module docstring) from
    or to a metadata element.
    Optionally handles the XML namespaces. You will need the namespace
    handling when building XML for the VM startup; when updating the
    metadata, libvirt will take care of that.
    See also the docstring of the `create` function.
    """

    def __init__(self, namespace=None, namespace_uri=None):
        """
        :param namespace: namespace to use
        :type namespace: text string
        :param namespace_uri: URI of the namespace to use
        :type namespace_uri: text string
        """
        self._namespace = namespace
        self._namespace_uri = namespace_uri
        self._prefix = None
        if namespace is not None:
            ET.register_namespace(namespace, namespace_uri)
            self._prefix = '{%s}' % self._namespace_uri

    def load(self, elem):
        """
        Load the content of the given metadata element `elem`
        into a python object, trying to recover the correct types.
        To recover the types, this function relies on the element attributes
        added by the `dump` method. Without them, the function will
        still load the content, but everything will be a string.
        Example:

        <example>
            <a>some value</a>
            <b type="int">1</b>
        </example>

        elem = vmxml.parse_xml(...)

        md = Metadata()
        md.load(elem) -> {'a': 'some value', 'b': 1}

        :param elem: root of the ElementTree to load
        :type elem: ElementTree.Element
        :returns: content of the group
        :rtype: dict of key/value pairs. See the module docstring for types
        """
        values = {}
        for child in elem:
            key, val = _elem_to_keyvalue(child)
            values[self._strip_ns(key)] = val
        return values

    def dump(self, name, **kwargs):
        """
        Dump the given arguments into the `name` metadata element.
        This function transparently adds the type hints as element attributes,
        so `load` can restore them.

        Example:

        md = Metadata()
        md.dump('test', bar=42) -> elem

        vmxml.format_xml(elem) ->

        <test>
          <bar type="int">42</bar>
        </test>

        :param name: group to put in the metadata
        :type name: text string
        :param namespace: namespace to use
        :type namespace: text string
        :param namespace_uri: URI of the namespace to use
        :type namespace_uri: text string
        :return: the corresponding element
        :rtype: ElementTree.Element

        kwargs: stored as subelements
        """
        elem = ET.Element(self._add_ns(name))
        for key, value in kwargs.items():
            _keyvalue_to_elem(self._add_ns(key), value, elem)
        return elem

    def find(self, elem, tag):
        """
        Namespace-aware wrapper for elem.find()
        """
        return elem.find(self._add_ns(tag))

    def findall(self, elem, tag):
        """
        Namespace-aware wrapper for elem.findall()
        """
        for elt in elem.findall(self._add_ns(tag)):
            yield elt

    def _add_ns(self, tag):
        """
        Decorate the given tag with the namespace, if used
        """
        return (self._prefix or '') + tag

    def _strip_ns(self, tag):
        """
        Remove the namespace from the given tag
        """
        return tag.replace(self._prefix, '') if self._prefix else tag


def create(name, namespace, namespace_uri, **kwargs):
    """
    Create one `name` element.
    Use this function to initialize one empty metadata element,
    at XML creation time.

    Example:

    metadata.create('vm', 'ovirt-vm', 'http://ovirt.org/vm/1.0',
                    version=4.2) -> elem

    vmxml.format_xml(elem) ->

    <ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
    </ovirt-vm:vm>

    :param name: group to put in the metadata
    :type name: text string
    :param namespace: namespace to use
    :type namespace: text string
    :param namespace_uri: URI of the namespace to use
    :type namespace_uri: text string
    :return: the corresponding element
    :rtype: ElementTree.Element

    kwargs: stored as subelements
    """
    # here we must add the namespaces ourselves
    metadata_obj = Metadata(namespace, namespace_uri)
    return metadata_obj.dump(name, **kwargs)


def from_xml(xml_str):
    """
    Helper function to parse the libvirt domain metadata used by oVirt
    form one domain XML. Useful in the VM creation flow, when the
    libvirt Domain is not yet started.

    Example:

    given this XML:

    test_xml ->
    <?xml version="1.0" encoding="utf-8"?>
    <domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
      <metadata>
        <ovirt-vm:vm>
          <ovirt-vm:version type="float">4.2</ovirt-vm:version>
          <ovirt-vm:custom>
            <ovirt-vm:foo>bar</ovirt-vm:foo>
          </ovirt-vm:custom>
        </ovirt-vm:vm>
      </metadata.>
    </domain>

    metadata.from_xml(test_xml) ->
    {
      'version': 4.2,
      'custom':
      {
        'foo': 'bar'
      },
    }

    :param xml_str: domain XML to parse
    :type name: text string
    :return: the parsed metadata
    :rtype: Python dict, whose keys are always strings.
            No nested objects are allowed, with the only exception of
            the special 'custom' key, whose value will be another
            Python dictionary whose keys are strings, with no
            further nesting allowed.
    """
    metadata_obj = Metadata(
        xmlconstants.METADATA_VM_VDSM_PREFIX,
        xmlconstants.METADATA_VM_VDSM_URI
    )
    root = vmxml.parse_xml(xml_str)
    md_elem = root.find(
        './metadata/{%s}%s' % (
            xmlconstants.METADATA_VM_VDSM_URI,
            xmlconstants.METADATA_VM_VDSM_ELEMENT
        )
    )
    if md_elem is None:
        return {}
    md_data = metadata_obj.load(md_elem)
    custom_elem = root.find(
        './metadata/{%s}%s/{%s}custom' % (
            xmlconstants.METADATA_VM_VDSM_URI,
            xmlconstants.METADATA_VM_VDSM_ELEMENT,
            xmlconstants.METADATA_VM_VDSM_URI,
        )
    )
    if custom_elem is not None:
        md_data['custom'] = metadata_obj.load(custom_elem)
    return md_data


@contextmanager
def domain(dom, name, namespace, namespace_uri):
    """
    Helper context manager to simplify the get the instance of Metadata
    from a libvirt Domain object.

    Example:

    let's start with
    dom.metadata() -> <vm/>

    let's run this code
    with metadata.domain(dom, 'vm', 'ovirt-vm',
                         'http://ovirt.org/vm/1.0')
    ) as vm:
        vm['my_awesome_key'] = some_awesome_value()  # returns 42

    now we will have
    dom.metadata() ->
    <vm>
      <my_awesome_key type="int">42</my_awesome_key>
    </vm>

    but if you look in the domain XML (e.g. virsh dumpxml) you will
    have, courtesy of libvirt:

    <metadata>
      <ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
        <ovirt-vm:my_awesome_key type="int">42</ovirt-vm:my_awesome_key>
      </ovirt-vm:vm>
    </metadata>

    :param dom: domain to access
    :type dom: libvirt.Domain
    :param name: metadata group to access
    :type name: text string
    :param namespace: metadata namespace to use
    :type namespace: text string
    :param namespace_uri: metadata namespace URI to use
    :type namespace_uri: text string
    """
    with _metadata_xml(dom, name, namespace, namespace_uri) as md:
        # we DO NOT want to handle namespaces ourselves; libvirt does
        # it automatically for us.
        metadata_obj = Metadata()
        content = metadata_obj.load(md[0])
        yield content
        md[0] = metadata_obj.dump(name, **content)


@contextmanager
def _metadata_xml(dom, tag, namespace, namespace_uri):
    md_xml = "<{tag}/>".format(tag=tag)
    try:
        md_xml = dom.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                              namespace_uri,
                              0)

    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_METADATA:
            raise

    md_elem = [vmxml.parse_xml(md_xml)]
    # we do this because we need to receive back the updated element
    yield md_elem

    dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                    vmxml.format_xml(md_elem[0]),
                    namespace,
                    namespace_uri,
                    0)


def _find_device(vm_elem, attrs, namespace_uri=None):
    """
    Find one device in the vm metadata, matching all the given attributes.
    This function expect to work with a XML structure like:

    <vm>
      <device id="dev0">
        <foo>bar</foo>
      </device>
      <device addr="0xF00" class="pci">
        <number type="int">42</number>
      </device>
    </vm>

    All the attributes given in `attrs` must match.
    If the device element has more attributes, they are ignored.
    Return None if no match is found, but raise MissingDevice if no device
    is uniquely identified using the given `attrs`.

    :param vm_elem: root of the vm metadata including the device metadata
    :type vm_elem: ElementTree.Element
    :param attrs: attributes to match to identify the device
    :type attrs: dict, each item is string both for key and value
    :param namespace_uri: optional URI of the namespace on which the `device`
           element resides. Use 'None' to disable the namespace support.
    :type namespace_uri: text string
    :return: the device element, or None if no device data found
    :rtype: ElementTree.Element, or None
    """
    xpath_attrs = []
    for key, value in attrs.items():
        xpath_attrs.append(
            '[@{key}="{value}"]'.format(key=key, value=value)
        )

    prefix = '' if namespace_uri is None else '{%s}' % namespace_uri
    devices = vm_elem.findall(
        './{}device{}'.format(prefix, ''.join(xpath_attrs))
    )
    if len(devices) > 1:
        raise MissingDevice()
    if not devices:
        return None
    return devices[0]


@contextmanager
def device(dom, **kwargs):
    """
    Helper context manager to get the metadata of a given device
    from a libvirt Domain object.
    Please make sure to check the IMPORTANT WARNING below.

    Example:

    let's start with
    dom.metadata() ->
    <vm>
      <device id="dev0">
        <foo>bar</foo>
      </device>
      <device id="dev1">
        <number type="int">42</number>
      </device>
    </vm>

    let's run this code
    with metadata.device(dom, 'dev0') as dev:
        buzz = do_some_work(dev['foo'])
        dev['fizz'] = buzz

    now we will have
    dom.metadata() ->
    <vm>
      <device id="dev0">
        <foo>bar</foo>
        <fizz>sometimes_buzz</fizz>
      </device>
      <device id="dev1">
        <number type="int">42</number>
      </device>
    </vm>

    *** IMPORTANT WARNING ***
    This context manager will provide the client access only to the metadata
    of one device. Once it is done, it will update only that device, leaving
    metadata of the other devices, or the VM, unchanged. But under the hood,
    this context manager will *rewrite all the VM metadata*.
    You will need to make sure *every* usage of metadata (either per-vm or
    per-device) on the same libvirt.Domain is protected by one exclusive lock.

    Synchronization is intentionally not done in this module, it should be
    done at the same layer who owns the libvirt.Domain object.

    :param dom: domain to access
    :type dom: libvirt.Domain

    kwargs: attributes to match to identify the device; values are expected to
    be string.
    """
    with _metadata_xml(
        dom,
        xmlconstants.METADATA_VM_VDSM_ELEMENT,
        xmlconstants.METADATA_VM_VDSM_PREFIX,
        xmlconstants.METADATA_VM_VDSM_URI
    ) as md:
        vm_elem = md[0]
        attrs = kwargs
        dev_elem = _find_device(vm_elem, attrs)
        if dev_elem is not None:
            attrs = dev_elem.attrib.copy()
            dev_found = True
        else:
            dev_found = False
            dev_elem = ET.Element(_DEVICE, **attrs)

        metadata_obj = Metadata()
        content = metadata_obj.load(dev_elem)

        yield content

        # we want to completely replace the device metadata - not update
        # the existing one - to not leave garbage behind
        if dev_found:
            vmxml.remove_child(vm_elem, dev_elem)
        dev_elem = metadata_obj.dump(_DEVICE, **content)
        dev_elem.attrib.update(attrs)
        vmxml.append_child(vm_elem, etree_child=dev_elem)
        md[0] = vm_elem


def device_from_xml_tree(root, **kwargs):
    """
    Helper function to get the metadata of a given device
    from one DOM subtree, obtained from the parsed XML of a libvirt Domain.
    The DOM subtree is expected to have its root at the 'metadata' element
    of the libvirt domain,

    Example:

    Let's start with this domain_xml:
    <?xml version="1.0" encoding="utf-8"?>
    <domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
      <metadata>
        <ovirt-vm:vm>
          <ovirt-vm:device id="mydev">
            <ovirt-vm:foo>bar</ovirt-vm:foo>
          </ovirt-vm:device>
        </ovirt-vm:vm>
      </metadata>
    </domain>

    Let's run this code:
    dom = vmxml.parse_xml(domain_xml)
    md_elem = vmxml.find_first(dom, 'metadata')

    Now we will have:
    metadata.device_from_xml_tree(md_elem, id='mydev') ->
    { 'foo': 'bar' }

    :param root: DOM element, corresponding to the 'metadata' element of the
                 Domain XML.
    :type: DOM element.

    :param kwargs: attributes to match to identify the device;
                   the values are expected to be strings, much like the
                   `device` context manager

    :return: the parsed metadata.
    :rtype: Python dict, whose keys are always strings.
            No nested objects are allowed.
    """
    md_elem = root.find(
        './{%s}%s' % (
            xmlconstants.METADATA_VM_VDSM_URI,
            xmlconstants.METADATA_VM_VDSM_ELEMENT
        )
    )
    if md_elem is None:
        return {}

    dev_elem = _find_device(
        md_elem, kwargs, xmlconstants.METADATA_VM_VDSM_URI
    )
    if dev_elem is None:
        return {}

    metadata_obj = Metadata(
        xmlconstants.METADATA_VM_VDSM_PREFIX,
        xmlconstants.METADATA_VM_VDSM_URI
    )
    return metadata_obj.load(dev_elem)


class Descriptor(object):

    def __init__(
        self,
        name=xmlconstants.METADATA_VM_VDSM_ELEMENT,
        namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
        namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        """
        Initializes one empty descriptor.

        :param name: metadata group to access
        :type name: text string
        :param namespace: metadata namespace to use
        :type namespace: text string
        :param namespace_uri: metadata namespace URI to use
        :type namespace_uri: text string

        Example:

        given this XML:

        test_xml ->
        <?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <metadata>
            <ovirt-vm:vm>
              <ovirt-vm:version type="float">4.2</ovirt-vm:version>
              <ovirt-vm:custom>
                <ovirt-vm:foo>bar</ovirt-vm:foo>
              </ovirt-vm:custom>
            </ovirt-vm:vm>
          </metadata.>
        </domain>

        md_desc = Descriptor.from_xml(
            test_xml, 'vm', 'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        with md_desc.values() as vm:
          print(vm)

        will emit
        {
          'version': 4.2,
        }

        print(md_desc.custom())

        will emit
        {
          'foo': 'bar'
        }
        """
        self._name = name
        self._namespace = namespace
        self._namespace_uri = namespace_uri
        self._values = {}
        self._custom = {}
        self._devices = []

    @classmethod
    def from_xml(
        cls,
        xml_str,
        name=xmlconstants.METADATA_VM_VDSM_ELEMENT,
        namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
        namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        """
        Initializes one descriptor given the namespace-prefixed metadata
        snippet. Useful in the VM creation flow, when the
        libvirt Domain is not yet started.

        :param xml_str: domain XML to parse
        :type name: text string
        :param name: metadata group to access
        :type name: text string
        :param namespace: metadata namespace to use
        :type namespace: text string
        :param namespace_uri: metadata namespace URI to use
        :type namespace_uri: text string
        """
        obj = cls(name, namespace, namespace_uri)
        obj._parse_xml(xml_str)
        return obj

    def load(self, dom):
        """
        Reads the content of the metadata section from the given libvirt
        domain. This will fully overwrite any existing content stored in the
        Descriptor. The data in the libvirt domain is not changed at all.

        :param dom: domain to access
        :type dom: libvirt.Domain
        """
        md_xml = "<{tag}/>".format(tag=self._name)
        try:
            md_xml = dom.metadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                self._namespace_uri,
                0
            )
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_METADATA:
                raise
            # else `md_xml` not reassigned, so we will parse empty section
            # and that's exactly what we want.

        self._load(vmxml.parse_xml(md_xml))

    def dump(self, dom):
        """
        Serializes all the content stored in the descriptor, completely
        overwriting the content of the libvirt domain.

        :param dom: domain to access
        :type dom: libvirt.Domain
        """
        dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                        self._build_xml(),
                        self._namespace,
                        self._namespace_uri,
                        0)

    def to_xml(self):
        """
        Produces the namespace-prefixed XML representation of the full content
        of this Descriptor.

        :rtype: string
        """
        return self._build_xml(self._namespace, self._namespace_uri)

    @contextmanager
    def device(self, **kwargs):
        """
        Helper context manager to get and update the metadata of
        a given device.
        Any change performed to the device metadata is not committed
        to the underlying libvirt.Domain until dump() is called.

        :param dom: domain to access
        :type dom: libvirt.Domain

        kwargs: attributes to match to identify the device;
        values are expected to be strings.

        Example:

        let's start with
        dom.metadata() ->
        <vm>
          <device id="dev0">
            <foo>bar</foo>
          </device>
          <device id="dev1">
            <number type="int">42</number>
          </device>
        </vm>

        let's run this code
        md_desc = Descriptor('vm')
        md_desc.load(dom)
        with md_desc.device(id='dev0') as vm:
           print(vm)

        will emit

        {
          'foo': 'bar'
        }
        """
        dev_data = self._find_device(kwargs)
        if dev_data is None:
            dev_data = self._add_device(kwargs)
        data = dev_data.copy()
        yield data
        dev_data.clear()
        dev_data.update(data)

    @contextmanager
    def values(self):
        """
        Helper context manager to get and update the metadata of the vm.
        Any change performed to the device metadata is not committed
        to the underlying libvirt.Domain until dump() is called.

        :rtype: Python dict, whose keys are always strings.
                No nested objects are allowed.
        """
        data = self._values.copy()
        yield data
        self._values.clear()
        self._values.update(data)

    @property
    def custom(self):
        """
        Return the custom properties, as dict.
        The custom properties are sent by Engine and read-only.

        :rtype: Python dict, whose keys are always strings.
                No nested objects are allowed.
        """
        return self._custom.copy()

    def _parse_xml(self, xml_str):
        root = vmxml.parse_xml(xml_str)
        md_elem = root.find(
            './metadata/{%s}%s' % (
                self._namespace_uri,
                self._name
            )
        )
        if md_elem is not None:
            self._load(md_elem, self._namespace, self._namespace_uri)

    def _load(self, md_elem, namespace=None, namespace_uri=None):
        metadata_obj = Metadata(namespace, namespace_uri)
        md_data = metadata_obj.load(md_elem)
        custom_elem = metadata_obj.find(md_elem, _CUSTOM)
        if custom_elem is not None:
            self._custom = metadata_obj.load(custom_elem)
        else:
            self._custom = {}
        self._devices = [
            (dev.attrib.copy(), metadata_obj.load(dev))
            for dev in metadata_obj.findall(md_elem, _DEVICE)
        ]
        md_data.pop(_CUSTOM, None)
        md_data.pop(_DEVICE, None)
        self._values = md_data

    def _build_xml(self, namespace=None, namespace_uri=None):
        metadata_obj = Metadata(namespace, namespace_uri)
        md_elem = metadata_obj.dump(self._name, **self._values)
        for (attrs, data) in self._devices:
            if data:
                dev_elem = metadata_obj.dump(_DEVICE, **data)
                dev_elem.attrib.update(attrs)
                vmxml.append_child(md_elem, etree_child=dev_elem)
        if self._custom:
            custom_elem = metadata_obj.dump(_CUSTOM, **self._custom)
            vmxml.append_child(md_elem, etree_child=custom_elem)
        return vmxml.format_xml(md_elem, pretty=True)

    def _find_device(self, kwargs):
        devices = [
            data
            for (attrs, data) in self._devices
            if _match_args(kwargs, attrs)
        ]
        if len(devices) > 1:
            raise MissingDevice()
        if not devices:
            return None
        return devices[0]

    def _add_device(self, attrs):
        data = {}
        self._devices.append((attrs.copy(), data))
        # yes, we want to return a mutable reference.
        return data


def _match_args(kwargs, attrs):
    for key, value in kwargs.items():
        if key not in attrs or attrs[key] != value:
            return False
    return True


def _elem_to_keyvalue(elem):
    key = elem.tag
    value = elem.text
    data_type = elem.attrib.get('type')
    if data_type is not None:
        if data_type == 'bool':
            value = conv.tobool(value)
        elif data_type == 'int':
            value = int(value)
        elif data_type == 'float':
            value = float(value)
        # elif data_type == 'str': do nothing
    return key, value


def _keyvalue_to_elem(key, value, elem):
    subelem = ET.SubElement(elem, key)
    if isinstance(value, bool):
        subelem.attrib['type'] = 'bool'
    elif isinstance(value, int):
        subelem.attrib['type'] = 'int'
    elif isinstance(value, float):
        subelem.attrib['type'] = 'float'
    elif isinstance(value, six.string_types):
        pass
    else:
        raise UnsupportedType(value)
    subelem.text = str(value)
    return subelem