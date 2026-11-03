// SPDX-License-Identifier: MIT
//go:build darwin

package main

/*
#cgo LDFLAGS: -framework IOKit -framework CoreFoundation
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifndef kIOMainPortDefault
#define kIOMainPortDefault kIOMasterPortDefault
#endif

enum {
	appleVIDC  = 0x05ac,
	nano7DiskC = 0x1267
};

static const char *ior_name_usb(IOReturn r) {
	if (r == kIOReturnSuccess) {
		return "success";
	}
	if (r == kIOReturnBusy) {
		return "busy";
	}
	if (r == kIOReturnExclusiveAccess) {
		return "exclusive";
	}
	if (r == kIOReturnNotOpen) {
		return "not_open";
	}
	if (r == kIOReturnNotPrivileged) {
		return "not_privileged";
	}
	if (r == kIOReturnError) {
		return "error";
	}
	return "other";
}

static int seize_usb_interface(io_service_t svc) {
	IOCFPlugInInterface **plugin = NULL;
	SInt32 score = 0;
	kern_return_t kr = IOCreatePlugInInterfaceForService(
		svc, kIOUSBInterfaceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
	fprintf(stderr, "usbscsi: USBInterface plugin kr=0x%08x score=%d plugin=%p\n",
		(unsigned)kr, (int)score, (void *)plugin);
	if (kr != KERN_SUCCESS || plugin == NULL) {
		return -1;
	}
	IOUSBInterfaceInterface **iface = NULL;
	HRESULT hr = (*plugin)->QueryInterface(
		plugin, CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID), (LPVOID *)&iface);
	fprintf(stderr, "usbscsi: USBInterface QueryInterface hr=0x%08x iface=%p\n",
		(unsigned)hr, (void *)iface);
	(*plugin)->Release(plugin);
	if (hr != S_OK || iface == NULL) {
		return -2;
	}
	IOReturn r = (*iface)->USBInterfaceOpenSeize(iface);
	fprintf(stderr, "usbscsi: USBInterfaceOpenSeize kr=0x%08x (%s)\n",
		(unsigned)r, ior_name_usb(r));
	if (r == kIOReturnSuccess) {
		(*iface)->USBInterfaceClose(iface);
		(*iface)->Release(iface);
		return 0;
	}
	(*iface)->Release(iface);
	return -3;
}

static int seize_usb_device(io_service_t svc) {
	IOCFPlugInInterface **plugin = NULL;
	SInt32 score = 0;
	kern_return_t kr = IOCreatePlugInInterfaceForService(
		svc, kIOUSBDeviceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
	if (kr != KERN_SUCCESS || plugin == NULL) {
		return -1;
	}
	IOUSBDeviceInterface **dev = NULL;
	HRESULT hr = (*plugin)->QueryInterface(
		plugin, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID), (LPVOID *)&dev);
	(*plugin)->Release(plugin);
	if (hr != S_OK || dev == NULL) {
		return -2;
	}
	IOReturn dr = (*dev)->USBDeviceOpenSeize(dev);
	fprintf(stderr, "usbscsi: USBDeviceOpenSeize kr=0x%08x (%s)\n",
		(unsigned)dr, ior_name_usb(dr));
	IOUSBFindInterfaceRequest req;
	req.bInterfaceClass = kIOUSBFindInterfaceDontCare;
	req.bInterfaceSubClass = kIOUSBFindInterfaceDontCare;
	req.bInterfaceProtocol = kIOUSBFindInterfaceDontCare;
	req.bAlternateSetting = kIOUSBFindInterfaceDontCare;
	io_iterator_t ifaces = 0;
	int seized = 0;
	if ((*dev)->CreateInterfaceIterator(dev, &req, &ifaces) == kIOReturnSuccess) {
		io_service_t isvc;
		while ((isvc = IOIteratorNext(ifaces))) {
			if (seize_usb_interface(isvc) == 0) {
				seized = 1;
			}
			IOObjectRelease(isvc);
		}
		IOObjectRelease(ifaces);
	}
	(void)(*dev)->USBDeviceClose(dev);
	(*dev)->Release(dev);
	return seized ? 0 : -3;
}

static int vid_pid_match(io_service_t svc) {
	CFNumberRef v = (CFNumberRef)IORegistryEntryCreateCFProperty(
		svc, CFSTR(kUSBVendorID), kCFAllocatorDefault, 0);
	CFNumberRef p = (CFNumberRef)IORegistryEntryCreateCFProperty(
		svc, CFSTR(kUSBProductID), kCFAllocatorDefault, 0);
	int32_t vid = 0, pid = 0;
	int ok = 0;
	if (v && p) {
		CFNumberGetValue(v, kCFNumberSInt32Type, &vid);
		CFNumberGetValue(p, kCFNumberSInt32Type, &pid);
		ok = (vid == appleVIDC && pid == nano7DiskC);
	}
	if (v) {
		CFRelease(v);
	}
	if (p) {
		CFRelease(p);
	}
	if (ok) {
		return 1;
	}
	io_service_t parent = 0;
	if (IORegistryEntryGetParentEntry(svc, kIOServicePlane, &parent) == KERN_SUCCESS) {
		int nested = vid_pid_match(parent);
		IOObjectRelease(parent);
		return nested;
	}
	return 0;
}

static int iterate_class(const char *className, int asInterface) {
	CFMutableDictionaryRef matching = IOServiceMatching(className);
	if (matching == NULL) {
		return 0;
	}
	io_iterator_t iter = 0;
	if (IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iter) != KERN_SUCCESS) {
		return 0;
	}
	int seized = 0;
	io_service_t svc;
	while ((svc = IOIteratorNext(iter))) {
		if (vid_pid_match(svc)) {
			int rc = asInterface ? seize_usb_interface(svc) : seize_usb_device(svc);
			if (rc == 0) {
				seized = 1;
			}
		}
		IOObjectRelease(svc);
	}
	IOObjectRelease(iter);
	return seized;
}

int darwin_usb_seize_nudge(void) {
	int seized = 0;
	if (iterate_class("IOUSBHostDevice", 0)) {
		seized = 1;
	}
	if (iterate_class("IOUSBDevice", 0)) {
		seized = 1;
	}
	if (iterate_class("IOUSBHostInterface", 1)) {
		seized = 1;
	}
	if (iterate_class("IOUSBInterface", 1)) {
		seized = 1;
	}
	return seized ? 0 : 1;
}
*/
import "C"

import (
	"fmt"
	"os"
)

// nudgeKernelUSB seizes the nano 7G mass-storage interface from
// IOUSBMassStorageDriver, then closes so libusb can claim. RetailOS BOT
// on macOS 13 often needs USBInterfaceOpenSeize; SetAutoDetach alone can
// still lose the race. No NAND write. No kext unload.
func nudgeKernelUSB() {
	rc := C.darwin_usb_seize_nudge()
	if rc == 0 {
		fmt.Fprintln(os.Stderr, "usbscsi: Darwin USBDeviceOpenSeize/USBInterfaceOpenSeize nudge ok")
		return
	}
	fmt.Fprintln(os.Stderr, "usbscsi: Darwin USBInterfaceOpenSeize nudge did not seize (libusb claim still tried)")
}
