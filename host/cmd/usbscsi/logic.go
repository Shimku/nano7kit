// SPDX-License-Identifier: MIT
package main

import (
	"fmt"
	"regexp"
	"strings"
)

// CSW status 1 = Failed / CHECK CONDITION. CSW 2 is phase error (could not
// complete the transfer), not a device CHECK CONDITION. IOKit SCSITask
// CHECK CONDITION is status 0x2 plus a sense key; that path is ipod_sg_raw.
var checkConditionRe = regexp.MustCompile(`(?i)check condition|sense key|csw status 1\b|scsi status (?:0x0*1\b|1\b)`)

const (
	outcomeOK             = "ok"
	outcomeCouldNotSend   = "could_not_send"
	outcomeDeviceRejected = "device_rejected"
)

// RC10 last_lba is 32-bit. 0xFFFFFFFF means "overflow, look at RC16".
// MKN52ZP recovery diskmode reports last_lba=3859038, so RC16 is pointless
// and on this device it stalls bulk-IN (then the next CBW is cancelled).
func shouldSendCapacity16(lastLBA uint32) bool {
	return lastLBA == 0xFFFFFFFF
}

func isPipeErr(err error) bool {
	if err == nil {
		return false
	}
	s := strings.ToLower(err.Error())
	for _, n := range []string{
		"stall",
		"pipe",
		"cancelled",
		"canceled",
		"halt",
		"libusb_error_pipe",
		"libusb_transfer_cancelled",
		"endpoint stalled",
	} {
		if strings.Contains(s, n) {
			return true
		}
	}
	return false
}

// retryReadAfterPipe is true for reads and no-data commands (SYNCHRONIZE CACHE).
// WRITE(10) is never retried blindly after a pipe error.
func retryReadAfterPipe(inLen, outLen int) bool {
	return outLen == 0
}

// formatLibusbErr tags LIBUSB_ERROR_ACCESS (-3). Claiming iface 0 while
// IOUSBMassStorageDriver holds it is the usual macOS RetailOS miss.
func formatLibusbErr(err error) string {
	if err == nil {
		return ""
	}
	s := err.Error()
	low := strings.ToLower(s)
	access := strings.Contains(low, "libusb_error_access") ||
		strings.Contains(low, "access denied") ||
		strings.Contains(low, "permission denied") ||
		strings.Contains(low, "[code -3]") ||
		strings.Contains(low, "error_access")
	if access && !strings.Contains(s, "LIBUSB_ERROR_ACCESS") {
		return fmt.Sprintf("%s (LIBUSB_ERROR_ACCESS; IOUSBMassStorageDriver still owns iface 0)", s)
	}
	return s
}

// formatCheckCondition builds the DEVICE_REJECTED line after REQUEST SENSE.
// Fixed-format sense: key at byte 2, ASC at 12, ASCQ at 13.
func formatCheckCondition(sense []byte) string {
	var key, asc, ascq byte
	if len(sense) > 2 {
		key = sense[2] & 0x0f
	}
	if len(sense) > 12 {
		asc = sense[12]
	}
	if len(sense) > 13 {
		ascq = sense[13]
	}
	return fmt.Sprintf(
		"SCSI CHECK CONDITION CSW status 1 REQUEST SENSE key 0x%02x asc 0x%02x ascq 0x%02x",
		key, asc, ascq)
}

func classifyXferErr(err error) string {
	if err == nil {
		return outcomeOK
	}
	s := strings.ToLower(err.Error())
	if strings.Contains(s, "unknown command") || strings.HasPrefix(s, "usage:") {
		return outcomeCouldNotSend
	}
	if checkConditionRe.MatchString(s) {
		return outcomeDeviceRejected
	}
	return outcomeCouldNotSend
}

func isUsageErr(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.HasPrefix(s, "usage:") || strings.HasPrefix(s, "unknown command")
}

func exitCodeForErr(err error) int {
	if err == nil {
		return 0
	}
	if isUsageErr(err) {
		return 2
	}
	if classifyXferErr(err) == outcomeDeviceRejected {
		return 1
	}
	return 3
}
