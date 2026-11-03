// SPDX-License-Identifier: MIT
package main

import (
	"fmt"
	"strings"
	"testing"
)

func TestShouldSendCapacity16(t *testing.T) {
	if shouldSendCapacity16(3859038) {
		t.Fatal("MKN52 last_lba=3859038 must not send READ CAPACITY(16)")
	}
	if shouldSendCapacity16(0) {
		t.Fatal("last_lba=0 must not send RC16")
	}
	if shouldSendCapacity16(0xFFFFFFFE) {
		t.Fatal("last_lba just below 0xFFFFFFFF must not send RC16")
	}
	if !shouldSendCapacity16(0xFFFFFFFF) {
		t.Fatal("RC10 max last_lba must send RC16")
	}
}

func TestIsPipeErr(t *testing.T) {
	if isPipeErr(nil) {
		t.Fatal("nil is not a pipe error")
	}
	for _, s := range []string{
		"CBW: transfer was cancelled",
		"data IN: halt condition detected (endpoint stalled)",
		"pipe error",
		"LIBUSB_ERROR_PIPE",
		"LIBUSB_TRANSFER_CANCELLED",
	} {
		if !isPipeErr(fmt.Errorf("%s", s)) {
			t.Fatalf("expected pipe err: %s", s)
		}
	}
	if isPipeErr(fmt.Errorf("SCSI status 2")) {
		t.Fatal("SCSI status is not a pipe error")
	}
	if isPipeErr(fmt.Errorf("context deadline exceeded")) {
		t.Fatal("timeout is not a pipe error")
	}
}

func TestClassifyXferErr(t *testing.T) {
	if classifyXferErr(nil) != outcomeOK {
		t.Fatal("nil is ok")
	}
	if classifyXferErr(fmt.Errorf("DEVICE_REJECTED SCSI status 1")) != outcomeDeviceRejected {
		t.Fatal("SCSI status 1 is device_rejected")
	}
	if classifyXferErr(fmt.Errorf("SCSI CHECK CONDITION CSW status 1")) != outcomeDeviceRejected {
		t.Fatal("CSW status 1 CHECK CONDITION is device_rejected")
	}
	if classifyXferErr(fmt.Errorf("SCSI CSW status 2")) != outcomeCouldNotSend {
		t.Fatal("CSW status 2 phase error is could_not_send")
	}
	if classifyXferErr(fmt.Errorf("SCSI status 12")) != outcomeCouldNotSend {
		t.Fatal("scsi status 12 must not match status 1")
	}
	if classifyXferErr(fmt.Errorf("unknown command probe")) != outcomeCouldNotSend {
		t.Fatal("argv unknown command is could_not_send")
	}
	if classifyXferErr(fmt.Errorf("claim interface 0: Apple's mass-storage driver still owns the device")) != outcomeCouldNotSend {
		t.Fatal("claim failure is could_not_send")
	}
	if classifyXferErr(fmt.Errorf("SetAutoDetach (needs root on macOS to capture the kernel driver): boom")) != outcomeCouldNotSend {
		t.Fatal("SetAutoDetach is could_not_send")
	}
	if exitCodeForErr(fmt.Errorf("usage: usbscsi peek|probe")) != 2 {
		t.Fatal("usage is exit 2")
	}
	if exitCodeForErr(fmt.Errorf("unknown command foo")) != 2 {
		t.Fatal("unknown command is exit 2")
	}
	if exitCodeForErr(fmt.Errorf("SCSI status 1")) != 1 {
		t.Fatal("rejected is exit 1")
	}
	if exitCodeForErr(fmt.Errorf("claim interface 0: busy")) != 3 {
		t.Fatal("could_not_send is exit 3")
	}
}

func TestFormatLibusbErrAccess(t *testing.T) {
	got := formatLibusbErr(fmt.Errorf("libusb: access denied [code -3]"))
	if !strings.Contains(got, "LIBUSB_ERROR_ACCESS") {
		t.Fatalf("expected LIBUSB_ERROR_ACCESS tag, got %q", got)
	}
	if !strings.Contains(got, "IOUSBMassStorageDriver") {
		t.Fatalf("expected driver hint, got %q", got)
	}
	plain := formatLibusbErr(fmt.Errorf("libusb: pipe error [code -9]"))
	if strings.Contains(plain, "LIBUSB_ERROR_ACCESS") {
		t.Fatalf("pipe is not ACCESS: %q", plain)
	}
	if formatLibusbErr(nil) != "" {
		t.Fatal("nil formats empty")
	}
}

func TestFormatCheckCondition(t *testing.T) {
	got := formatCheckCondition([]byte{
		0x70, 0, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x20, 0x00, 0, 0, 0, 0,
	})
	if !strings.Contains(got, "REQUEST SENSE") {
		t.Fatalf("expected REQUEST SENSE tag, got %q", got)
	}
	if !strings.Contains(got, "key 0x05") {
		t.Fatalf("expected sense key, got %q", got)
	}
	if !strings.Contains(got, "asc 0x20") {
		t.Fatalf("expected ASC, got %q", got)
	}
	if classifyXferErr(fmt.Errorf("%s", got)) != outcomeDeviceRejected {
		t.Fatalf("REQUEST SENSE CHECK CONDITION must stay device_rejected: %q", got)
	}
	empty := formatCheckCondition(nil)
	if !strings.Contains(empty, "key 0x00") {
		t.Fatalf("empty sense is 0/0/0, got %q", empty)
	}
}

func TestRetryReadAfterPipe(t *testing.T) {
	if !retryReadAfterPipe(8, 0) {
		t.Fatal("READ CAPACITY (data IN) may retry")
	}
	if !retryReadAfterPipe(4096, 0) {
		t.Fatal("READ(10) may retry")
	}
	if !retryReadAfterPipe(0, 0) {
		t.Fatal("SYNCHRONIZE CACHE (no data) may retry")
	}
	if retryReadAfterPipe(0, 4096) {
		t.Fatal("WRITE(10) must not retry")
	}
}
