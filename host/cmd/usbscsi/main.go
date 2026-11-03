// SPDX-License-Identifier: MIT
//
// usbscsi talks to an iPod nano 7G over USB mass-storage BOT (05ac:1267).
//
// Vendor C6 (RetailOS NanoApps):
//
//	usbscsi peek 0x09120000 0x200     # base64 on stdout
//	usbscsi probe                     # C6 96 04; exit 0/1/3 = ok/rejected/could-not-send
//	usbscsi write 0xADDR file.bin
//	usbscsi exec 0xADDR
//
// Standard SCSI (recovery diskmode; same family wInd3x uses for --backup READ):
//
//	usbscsi reset                     # libusb_reset_device; does not claim iface
//	usbscsi capacity                  # READ CAPACITY (10); RC16 only if last_lba==0xFFFFFFFF
//	usbscsi read10 LBA COUNT          # READ (10); raw blocks on stdout
//	usbscsi write10 LBA FILE          # WRITE (10); FILE must be a block multiple
//	usbscsi synccache                 # SYNCHRONIZE CACHE (10) 0x35; flush writes
//	usbscsi session                   # stdin commands; one USB claim for the whole flow
//
// WRITE(10) is not a new exploit. It is the same BOT path as READ(10). C6
// vendor peek/write/exec at ARM addresses stay separate commands.
package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/google/gousb"
)

const (
	appleVID  = 0x05ac
	nano7Disk = 0x1267
	cbwSig    = 0x43425355
	cswSig    = 0x53425355
	xferTO    = 30 * time.Second
	// Match wInd3x backup reads; 256 * 512 = 128 KiB per BOT transfer.
	maxBlocks = 256

	// USB mass-storage BOT recovery (USBDeviceReEnumerate-free).
	// CLEAR_FEATURE ENDPOINT_HALT: bmRequestType 0x02, bRequest 0x01, wValue 0.
	reqClearFeature = 0x01
	featureHalt     = 0
	rtClearHalt     = 0x02 // host-to-device | standard | endpoint
	// Bulk-Only Mass Storage Reset: class request 0xFF to the interface.
	rtBotReset  = 0x21 // host-to-device | class | interface
	reqBotReset = 0xFF
)

func main() {
	if err := run(); err != nil {
		code := exitCodeForErr(err)
		if isUsageErr(err) {
			fmt.Fprintf(os.Stderr, "usbscsi: %v\n", err)
			os.Exit(code)
		}
		if classifyXferErr(err) == outcomeDeviceRejected {
			fmt.Fprintf(os.Stderr, "usbscsi: DEVICE_REJECTED %s\n", formatLibusbErr(err))
		} else {
			printCouldNotSend(err)
		}
		os.Exit(code)
	}
}

func run() error {
	if len(os.Args) < 2 {
		return fmt.Errorf("usage: usbscsi peek|probe|write|exec|reset|capacity|read10|write10|synccache|session ...")
	}
	ctx := gousb.NewContext()
	defer ctx.Close()

	if os.Args[1] == "reset" {
		return doReset(ctx)
	}

	h, cleanup, err := openDiskBot(ctx)
	if err != nil {
		return err
	}
	defer cleanup()

	switch os.Args[1] {
	case "probe":
		chunk, err := h.c6(0x04, 0, nil, 512)
		if err != nil {
			return err
		}
		fmt.Printf("C6_96_04_OK bytes=%d\n", len(chunk))
		return nil
	case "peek":
		if len(os.Args) < 3 {
			return fmt.Errorf("usage: usbscsi peek ADDR [LEN]")
		}
		addr, err := strconv.ParseUint(os.Args[2], 0, 32)
		if err != nil {
			return err
		}
		n := uint(0x200)
		if len(os.Args) >= 4 {
			v, err := strconv.ParseUint(os.Args[3], 0, 32)
			if err != nil {
				return err
			}
			n = uint(v)
		}
		var all []byte
		for off := uint(0); off < n; off += 0x200 {
			chunk, err := h.c6(0x02, uint32(addr)+uint32(off), nil, 512)
			if err != nil {
				return err
			}
			want := int(n - off)
			if want > 512 {
				want = 512
			}
			all = append(all, chunk[:want]...)
		}
		os.Stdout.WriteString(base64.StdEncoding.EncodeToString(all) + "\n")
		return nil
	case "write":
		if len(os.Args) < 4 {
			return fmt.Errorf("usage: usbscsi write ADDR FILE")
		}
		addr, err := strconv.ParseUint(os.Args[2], 0, 32)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(os.Args[3])
		if err != nil {
			return err
		}
		const ch = 512
		pad := (len(data) + ch - 1) / ch * ch
		buf := make([]byte, pad)
		copy(buf, data)
		for i := 0; i < pad; i += ch {
			if _, err := h.c6(0x01, uint32(addr)+uint32(i), buf[i:i+ch], 0); err != nil {
				return err
			}
		}
		return nil
	case "exec":
		if len(os.Args) < 3 {
			return fmt.Errorf("usage: usbscsi exec ADDR")
		}
		addr, err := strconv.ParseUint(os.Args[2], 0, 32)
		if err != nil {
			return err
		}
		_, err = h.c6(0x03, uint32(addr), nil, 512)
		return err
	case "capacity":
		return printCapacity(h)
	case "read10":
		if len(os.Args) < 4 {
			return fmt.Errorf("usage: usbscsi read10 LBA COUNT")
		}
		lba, err := strconv.ParseUint(os.Args[2], 0, 32)
		if err != nil {
			return err
		}
		count, err := strconv.ParseUint(os.Args[3], 0, 32)
		if err != nil {
			return err
		}
		if count < 1 {
			return fmt.Errorf("COUNT must be >= 1")
		}
		bs, err := h.needBlockSize()
		if err != nil {
			return err
		}
		data, err := h.read10(uint32(lba), uint32(count), bs)
		if err != nil {
			return err
		}
		_, err = os.Stdout.Write(data)
		return err
	case "write10":
		if len(os.Args) < 4 {
			return fmt.Errorf("usage: usbscsi write10 LBA FILE")
		}
		lba, err := strconv.ParseUint(os.Args[2], 0, 32)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(os.Args[3])
		if err != nil {
			return err
		}
		bs, err := h.needBlockSize()
		if err != nil {
			return err
		}
		if bs == 0 || int(bs) > 1<<20 {
			return fmt.Errorf("refusing implausible block size %d", bs)
		}
		if len(data)%int(bs) != 0 {
			return fmt.Errorf("FILE length %d is not a multiple of block size %d", len(data), bs)
		}
		return h.write10(uint32(lba), data, bs)
	case "synccache":
		return h.synccache()
	case "session":
		return runSession(h)
	default:
		return fmt.Errorf("unknown command %s", os.Args[1])
	}
}

func doReset(ctx *gousb.Context) error {
	dev, err := ctx.OpenDeviceWithVIDPID(appleVID, nano7Disk)
	if err != nil {
		return fmt.Errorf("open nano 7G disk (05ac:1267): %w", err)
	}
	if dev == nil {
		return fmt.Errorf("no iPod nano 7G mass-storage device (05ac:1267)")
	}
	defer dev.Close()
	if err := dev.Reset(); err != nil {
		return fmt.Errorf("libusb_reset_device: %w", err)
	}
	fmt.Fprintln(os.Stderr, "usbscsi: reset ok")
	return nil
}

type bot struct {
	dev       *gousb.Device
	in        *gousb.InEndpoint
	out       *gousb.OutEndpoint
	inAddr    uint8
	outAddr   uint8
	ifaceNum  uint16
	tag       uint32
	blockSize uint32
}

func openDiskBot(ctx *gousb.Context) (*bot, func(), error) {
	// RetailOS MSC (05ac:1267) while IOUSBMassStorageDriver holds the
	// interface: IOKit USBInterfaceOpenSeize, then libusb SetAutoDetach +
	// claim. Live test opened BOT (CLEAR_FEATURE) then died on argv
	// because the Mac binary was stale. Do not libusb_reset_device on
	// the first tries (RetailOS Connected can drop). No NAND write.
	nudgeKernelUSB()
	const attempts = 4
	var last error
	for i := 1; i <= attempts; i++ {
		h, cleanup, err := openDiskBotOnce(ctx)
		if err == nil {
			return h, cleanup, nil
		}
		last = err
		printClaimAttempt(i, attempts, err)
		if i == attempts {
			break
		}
		time.Sleep(time.Duration(200*i) * time.Millisecond)
		nudgeKernelUSB()
	}
	return nil, nil, fmt.Errorf(
		"claim interface 0 after %d attempts: %s (LIBUSB_ERROR_ACCESS typical while IOUSBMassStorageDriver owns iface 0; USBInterfaceOpenSeize + SetAutoDetach used; unmount first)",
		attempts, formatLibusbErr(last),
	)
}

func printClaimAttempt(i, attempts int, err error) {
	text := formatLibusbErr(err)
	fmt.Fprintf(os.Stderr, "usbscsi: claim attempt %d/%d\n", i, attempts)
	for _, line := range strings.Split(text, "\n") {
		fmt.Fprintf(os.Stderr, "usbscsi:   %s\n", line)
	}
}

func printCouldNotSend(err error) {
	text := formatLibusbErr(err)
	lines := strings.Split(text, "\n")
	fmt.Fprintf(os.Stderr, "usbscsi: COULD_NOT_SEND %s\n", lines[0])
	for _, line := range lines[1:] {
		fmt.Fprintf(os.Stderr, "usbscsi:   %s\n", line)
	}
}

func openDiskBotOnce(ctx *gousb.Context) (*bot, func(), error) {
	dev, err := ctx.OpenDeviceWithVIDPID(appleVID, nano7Disk)
	if err != nil {
		return nil, nil, fmt.Errorf("open nano 7G disk (05ac:1267): %w", err)
	}
	if dev == nil {
		return nil, nil, fmt.Errorf("no iPod nano 7G mass-storage device (05ac:1267); is it Connected, and unmounted?")
	}
	if err := dev.SetAutoDetach(true); err != nil {
		fmt.Fprintf(os.Stderr, "usbscsi: SetAutoDetach (kernel detach) failed, continuing with seize/claim: %v\n", err)
	} else {
		fmt.Fprintln(os.Stderr, "usbscsi: SetAutoDetach ok")
	}
	cfgNum, err := dev.ActiveConfigNum()
	if err != nil {
		dev.Close()
		return nil, nil, err
	}
	cfg, err := dev.Config(cfgNum)
	if err != nil {
		dev.Close()
		return nil, nil, fmt.Errorf("config: %w (unmount the nano, run as root, retry)", err)
	}
	iface, err := cfg.Interface(0, 0)
	if err != nil {
		cfg.Close()
		dev.Close()
		return nil, nil, fmt.Errorf("claim interface 0: %s (Apple's mass-storage driver still owns the device; USBInterfaceOpenSeize nudge already tried)", formatLibusbErr(err))
	}

	var inEP *gousb.InEndpoint
	var outEP *gousb.OutEndpoint
	var inDesc, outDesc gousb.EndpointDesc
	eps := dev.Desc.Configs[cfg.Desc.Number].Interfaces[0].AltSettings[0].Endpoints
	for _, ep := range eps {
		switch ep.Direction {
		case gousb.EndpointDirectionIn:
			inDesc = ep
			inEP, err = iface.InEndpoint(ep.Number)
		case gousb.EndpointDirectionOut:
			outDesc = ep
			outEP, err = iface.OutEndpoint(ep.Number)
		}
		if err != nil {
			iface.Close()
			cfg.Close()
			dev.Close()
			return nil, nil, err
		}
	}
	if inEP == nil || outEP == nil {
		iface.Close()
		cfg.Close()
		dev.Close()
		return nil, nil, fmt.Errorf("missing bulk IN/OUT endpoints")
	}
	h := &bot{
		dev:      dev,
		in:       inEP,
		out:      outEP,
		inAddr:   endpointAddr(inDesc),
		outAddr:  endpointAddr(outDesc),
		ifaceNum: 0,
		tag:      1,
	}
	fmt.Fprintf(os.Stderr, "usbscsi: claimed iface=%d cfg=%d IN=0x%02x OUT=0x%02x\n", h.ifaceNum, cfg.Desc.Number, h.inAddr, h.outAddr)
	h.clearHalts("open")
	cleanup := func() {
		iface.Close()
		cfg.Close()
		dev.Close()
	}
	return h, cleanup, nil
}

func endpointAddr(desc gousb.EndpointDesc) uint8 {
	if desc.Address != 0 {
		return uint8(desc.Address)
	}
	n := uint8(desc.Number)
	if desc.Direction == gousb.EndpointDirectionIn {
		n |= 0x80
	}
	return n
}

func (h *bot) c6(op byte, addr uint32, outData []byte, inLen int) ([]byte, error) {
	var cdb [16]byte
	cdb[0] = 0xC6
	cdb[1] = 0x96
	cdb[2] = op
	cdb[3] = byte(addr >> 24)
	cdb[4] = byte(addr >> 16)
	cdb[5] = byte(addr >> 8)
	cdb[6] = byte(addr)
	return h.xfer(cdb[:], outData, inLen)
}

func (h *bot) capacity() (lastLBA, blockSize uint32, err error) {
	cdb := make([]byte, 10)
	cdb[0] = 0x25
	data, err := h.xfer(cdb, nil, 8)
	if err != nil {
		return 0, 0, err
	}
	if len(data) < 8 {
		return 0, 0, fmt.Errorf("READ CAPACITY short response: %d bytes", len(data))
	}
	last := binary.BigEndian.Uint32(data[0:4])
	bs := binary.BigEndian.Uint32(data[4:8])
	h.blockSize = bs
	return last, bs, nil
}

func (h *bot) needBlockSize() (uint32, error) {
	if h.blockSize != 0 {
		return h.blockSize, nil
	}
	_, bs, err := h.capacity()
	return bs, err
}

func printCapacity(h *bot) error {
	last, bs, err := h.capacity()
	if err != nil {
		return err
	}
	blocks := uint64(last) + 1
	fmt.Printf("read_capacity_10 last_lba=%d block_size=%d bytes=%d\n", last, bs, blocks*uint64(bs))
	if !shouldSendCapacity16(last) {
		fmt.Printf("read_capacity_16 skipped (last_lba=%d < 0xFFFFFFFF)\n", last)
		return nil
	}
	last16, bs16, err16 := h.capacity16()
	if err16 != nil {
		fmt.Printf("read_capacity_16 error=%v\n", err16)
		if isPipeErr(err16) {
			h.recover("READ CAPACITY(16) stall")
		}
		return nil
	}
	blocks16 := last16 + 1
	fmt.Printf("read_capacity_16 last_lba=%d block_size=%d bytes=%d\n", last16, bs16, blocks16*uint64(bs16))
	return nil
}

func (h *bot) capacity16() (lastLBA uint64, blockSize uint32, err error) {
	// READ CAPACITY (16): opcode 0x9E, service action 0x10, 32-byte parameter.
	cdb := make([]byte, 16)
	cdb[0] = 0x9E
	cdb[1] = 0x10
	binary.BigEndian.PutUint32(cdb[10:14], 32)
	data, err := h.xferTimeout(cdb, nil, 32, 5*time.Second)
	if err != nil {
		return 0, 0, err
	}
	if len(data) < 12 {
		return 0, 0, fmt.Errorf("READ CAPACITY(16) short response: %d bytes", len(data))
	}
	return binary.BigEndian.Uint64(data[0:8]), binary.BigEndian.Uint32(data[8:12]), nil
}

func (h *bot) read10(lba, count, blockSize uint32) ([]byte, error) {
	if count == 0 {
		return nil, fmt.Errorf("count must be non-zero")
	}
	if blockSize == 0 || blockSize > 1<<20 {
		return nil, fmt.Errorf("refusing implausible block size %d", blockSize)
	}
	var out []byte
	for count > 0 {
		n := count
		if n > maxBlocks {
			n = maxBlocks
		}
		cdb := cdbRW10(0x28, lba, uint16(n))
		chunk, err := h.xfer(cdb, nil, int(n*blockSize))
		if err != nil {
			return nil, fmt.Errorf("READ(10) lba %d count %d: %w", lba, n, err)
		}
		if uint32(len(chunk)) != n*blockSize {
			return nil, fmt.Errorf("READ(10) lba %d returned %d bytes, want %d", lba, len(chunk), n*blockSize)
		}
		out = append(out, chunk...)
		lba += n
		count -= n
	}
	return out, nil
}

func (h *bot) synccache() error {
	// SYNCHRONIZE CACHE (10): opcode 0x35. LBA 0, block count 0 = whole cache.
	// Not a firmware write. Recovery diskmode WRITE(10) can sit in a write
	// cache; a verify READ then hits cache, and unplug loses the FAT.
	cdb := make([]byte, 10)
	cdb[0] = 0x35
	if _, err := h.xferTimeout(cdb, nil, 0, 60*time.Second); err != nil {
		return fmt.Errorf("SYNCHRONIZE CACHE(10): %w", err)
	}
	fmt.Fprintln(os.Stderr, "usbscsi: synccache ok")
	return nil
}

func (h *bot) write10(lba uint32, data []byte, blockSize uint32) error {
	if blockSize == 0 {
		return fmt.Errorf("block size is zero")
	}
	if len(data)%int(blockSize) != 0 {
		return fmt.Errorf("data length %d is not a multiple of block size %d", len(data), blockSize)
	}
	total := uint32(len(data) / int(blockSize))
	off := 0
	for done := uint32(0); done < total; {
		n := total - done
		if n > maxBlocks {
			n = maxBlocks
		}
		chunk := data[off : off+int(n*blockSize)]
		cdb := cdbRW10(0x2A, lba, uint16(n))
		if _, err := h.xfer(cdb, chunk, 0); err != nil {
			return fmt.Errorf("WRITE(10) lba %d count %d: %w", lba, n, err)
		}
		lba += n
		done += n
		off += int(n * blockSize)
	}
	return nil
}

func cdbRW10(op byte, lba uint32, count uint16) []byte {
	cdb := make([]byte, 10)
	cdb[0] = op
	binary.BigEndian.PutUint32(cdb[2:6], lba)
	binary.BigEndian.PutUint16(cdb[7:9], count)
	return cdb
}

func (h *bot) clearHalt(epAddr uint8) error {
	_, err := h.dev.Control(rtClearHalt, reqClearFeature, featureHalt, uint16(epAddr), nil)
	return err
}

func (h *bot) massStorageReset() error {
	_, err := h.dev.Control(rtBotReset, reqBotReset, 0, h.ifaceNum, nil)
	return err
}

func (h *bot) clearHalts(why string) {
	fmt.Fprintf(os.Stderr, "usbscsi: CLEAR_FEATURE(ENDPOINT_HALT) IN=0x%02x OUT=0x%02x (%s)\n", h.inAddr, h.outAddr, why)
	if err := h.clearHalt(h.inAddr); err != nil {
		fmt.Fprintf(os.Stderr, "usbscsi: clear halt IN: %v\n", err)
	}
	if err := h.clearHalt(h.outAddr); err != nil {
		fmt.Fprintf(os.Stderr, "usbscsi: clear halt OUT: %v\n", err)
	}
}

func (h *bot) drainCSW() {
	tctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	buf := make([]byte, 13)
	n, err := h.in.ReadContext(tctx, buf)
	if err != nil {
		fmt.Fprintf(os.Stderr, "usbscsi: drain CSW: %v\n", err)
		return
	}
	if n == 13 && binary.LittleEndian.Uint32(buf[0:4]) == cswSig {
		fmt.Fprintf(os.Stderr, "usbscsi: drained CSW status=%d\n", buf[12])
	}
}

func (h *bot) recover(why string) {
	fmt.Fprintf(os.Stderr, "usbscsi: BOT recover: %s\n", why)
	if err := h.clearHalt(h.inAddr); err != nil {
		fmt.Fprintf(os.Stderr, "usbscsi: clear halt IN before drain: %v\n", err)
	}
	h.drainCSW()
	if err := h.massStorageReset(); err != nil {
		fmt.Fprintf(os.Stderr, "usbscsi: Bulk-Only Mass Storage Reset (0xFF): %v\n", err)
	} else {
		fmt.Fprintln(os.Stderr, "usbscsi: Bulk-Only Mass Storage Reset (0xFF) ok")
	}
	h.clearHalts("after BOT reset")
}

func (h *bot) xfer(cdb []byte, outData []byte, inLen int) ([]byte, error) {
	return h.xferTimeout(cdb, outData, inLen, xferTO)
}

func (h *bot) xferTimeout(cdb []byte, outData []byte, inLen int, timeout time.Duration) ([]byte, error) {
	return h.xferTimeoutRetry(cdb, outData, inLen, timeout, false)
}

func (h *bot) requestSense() ([]byte, error) {
	// SCSI-2 REQUEST SENSE. Live test: IOKit autosense was 0/0/0; BOT CSW 1
	// is the same empty-sense case unless we issue CDB 0x03 ourselves.
	cdb := []byte{0x03, 0, 0, 0, 18, 0}
	return h.xferTimeoutRetrySense(cdb, nil, 18, xferTO, false, false)
}

func (h *bot) xferTimeoutRetry(cdb []byte, outData []byte, inLen int, timeout time.Duration, retried bool) ([]byte, error) {
	return h.xferTimeoutRetrySense(cdb, outData, inLen, timeout, retried, true)
}

func (h *bot) xferTimeoutRetrySense(cdb []byte, outData []byte, inLen int, timeout time.Duration, retried bool, doSense bool) ([]byte, error) {
	if len(cdb) > 16 || len(cdb) < 6 {
		return nil, fmt.Errorf("CDB length %d is not 6..16", len(cdb))
	}
	h.tag++
	dirIn := inLen > 0
	xferLen := uint32(inLen)
	if !dirIn {
		xferLen = uint32(len(outData))
	}
	cbw := make([]byte, 31)
	binary.LittleEndian.PutUint32(cbw[0:4], cbwSig)
	binary.LittleEndian.PutUint32(cbw[4:8], h.tag)
	binary.LittleEndian.PutUint32(cbw[8:12], xferLen)
	if dirIn {
		cbw[12] = 0x80
	}
	cbw[13] = 0
	cbw[14] = byte(len(cdb))
	copy(cbw[15:], cdb)

	if timeout <= 0 {
		timeout = xferTO
	}
	tctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	if _, err := h.out.WriteContext(tctx, cbw); err != nil {
		return h.afterPipe("CBW", err, cdb, outData, inLen, timeout, retried)
	}
	var data []byte
	if dirIn && inLen > 0 {
		buf := make([]byte, inLen)
		n, err := h.in.ReadContext(tctx, buf)
		if err != nil {
			h.clearHalt(h.inAddr)
			h.drainCSW()
			return h.afterPipe("data IN", err, cdb, outData, inLen, timeout, retried)
		}
		data = buf[:n]
	} else if len(outData) > 0 {
		if _, err := h.out.WriteContext(tctx, outData); err != nil {
			return h.afterPipe("data OUT", err, cdb, outData, inLen, timeout, retried)
		}
	}
	csw := make([]byte, 13)
	if _, err := h.in.ReadContext(tctx, csw); err != nil {
		h.clearHalt(h.inAddr)
		return h.afterPipe("CSW", err, cdb, outData, inLen, timeout, retried)
	}
	if binary.LittleEndian.Uint32(csw[0:4]) != cswSig {
		if !retried {
			h.recover("bad CSW signature (phase error)")
			if retryReadAfterPipe(inLen, len(outData)) {
				return h.xferTimeoutRetrySense(cdb, outData, inLen, timeout, true, doSense)
			}
		}
		return nil, fmt.Errorf("bad CSW signature")
	}
	if csw[12] != 0 {
		if csw[12] == 1 {
			if doSense && (len(cdb) == 0 || cdb[0] != 0x03) {
				sense, serr := h.requestSense()
				if serr == nil {
					fmt.Fprintf(os.Stderr, "usbscsi: REQUEST SENSE CDB 0x03 %s\n", formatCheckCondition(sense))
					return nil, fmt.Errorf("%s", formatCheckCondition(sense))
				}
				fmt.Fprintf(os.Stderr, "usbscsi: REQUEST SENSE CDB 0x03 failed: %v\n", serr)
				return nil, fmt.Errorf("SCSI CHECK CONDITION CSW status 1 (REQUEST SENSE failed: %v)", serr)
			}
			return nil, fmt.Errorf("SCSI CHECK CONDITION CSW status 1")
		}
		return nil, fmt.Errorf("SCSI CSW status %d", csw[12])
	}
	return data, nil
}

func (h *bot) afterPipe(stage string, err error, cdb, outData []byte, inLen int, timeout time.Duration, retried bool) ([]byte, error) {
	wrapped := fmt.Errorf("%s: %w", stage, err)
	if retried || !isPipeErr(err) {
		return nil, wrapped
	}
	h.recover(wrapped.Error())
	if retryReadAfterPipe(inLen, len(outData)) {
		return h.xferTimeoutRetry(cdb, outData, inLen, timeout, true)
	}
	return nil, fmt.Errorf("%w (BOT recovered; WRITE not retried)", wrapped)
}

func sessionOK() {
	fmt.Println("OK")
	_ = os.Stdout.Sync()
}

func sessionERR(err error) {
	fmt.Printf("ERR %v\n", err)
	_ = os.Stdout.Sync()
}

func runSession(h *bot) error {
	sc := bufio.NewScanner(os.Stdin)
	buf := make([]byte, 0, 64*1024)
	sc.Buffer(buf, 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if line == "quit" {
			sessionOK()
			return nil
		}
		if err := handleSessionLine(h, line); err != nil {
			sessionERR(err)
			continue
		}
		sessionOK()
	}
	return sc.Err()
}

func handleSessionLine(h *bot, line string) error {
	fields := strings.Fields(line)
	if len(fields) == 0 {
		return fmt.Errorf("empty command")
	}
	switch fields[0] {
	case "capacity":
		return printCapacity(h)
	case "clearhalt":
		h.clearHalts("session clearhalt")
		return nil
	case "botreset":
		h.recover("session botreset")
		return nil
	case "synccache":
		return h.synccache()
	case "read10":
		if len(fields) != 4 {
			return fmt.Errorf("usage: read10 LBA COUNT FILE")
		}
		lba, err := strconv.ParseUint(fields[1], 0, 32)
		if err != nil {
			return err
		}
		count, err := strconv.ParseUint(fields[2], 0, 32)
		if err != nil {
			return err
		}
		if count < 1 {
			return fmt.Errorf("COUNT must be >= 1")
		}
		bs, err := h.needBlockSize()
		if err != nil {
			return err
		}
		data, err := h.read10(uint32(lba), uint32(count), bs)
		if err != nil {
			return err
		}
		if err := os.WriteFile(fields[3], data, 0o600); err != nil {
			return err
		}
		fmt.Printf("read10_bytes=%d\n", len(data))
		return nil
	case "write10":
		if len(fields) != 3 {
			return fmt.Errorf("usage: write10 LBA FILE")
		}
		lba, err := strconv.ParseUint(fields[1], 0, 32)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(fields[2])
		if err != nil {
			return err
		}
		bs, err := h.needBlockSize()
		if err != nil {
			return err
		}
		if bs == 0 || len(data)%int(bs) != 0 {
			return fmt.Errorf("FILE length %d is not a multiple of block size %d", len(data), bs)
		}
		return h.write10(uint32(lba), data, bs)
	default:
		return fmt.Errorf("unknown session command %s", fields[0])
	}
}
