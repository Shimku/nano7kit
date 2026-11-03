// SPDX-License-Identifier: MIT
//go:build !darwin

package main

// nudgeKernelUSB is a Darwin-only IOKit USBDeviceOpenSeize /
// USBInterfaceOpenSeize hint. Linux usbfs uses SetAutoDetach.
func nudgeKernelUSB() {}
