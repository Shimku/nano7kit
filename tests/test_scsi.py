#!/usr/bin/env python3
"""SCSI outcome classification: could-not-send vs device-rejected."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.scsi import (
    OUTCOME_COULD_NOT_SEND,
    OUTCOME_DEVICE_REJECTED,
    OUTCOME_OK,
    classify_scsi_result,
    join_helper_detail,
)


class ClassifyScsiResultTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(classify_scsi_result(0, "C6_96_04_OK bytes=512"), OUTCOME_OK)

    def test_live_missing_user_client_is_could_not_send(self):
        text = (
            "ipod_sg_raw: no SCSITask user client "
            "(kernel mass-storage driver may own the disk)"
        )
        self.assertEqual(classify_scsi_result(1, text), OUTCOME_COULD_NOT_SEND)

    def test_tagged_could_not_send_exit_3(self):
        text = (
            "ipod_sg_raw: COULD_NOT_SEND ObtainExclusiveAccess busy; "
            "unmount the iPod volume first: diskutil unmountDisk"
        )
        self.assertEqual(classify_scsi_result(3, text), OUTCOME_COULD_NOT_SEND)

    def test_tagged_device_rejected(self):
        text = (
            "ipod_sg_raw: DEVICE_REJECTED SCSI status 0x2 sense key 0x5 "
            "asc 0x20 ascq 0x0\n"
            "ipod_sg_raw: C6 96 04 not accepted (untether SCSI handler not active)"
        )
        self.assertEqual(classify_scsi_result(1, text), OUTCOME_DEVICE_REJECTED)

    def test_usbscsi_claim_fail(self):
        text = (
            "usbscsi: COULD_NOT_SEND claim interface 0: "
            "Apple's mass-storage driver still owns the device"
        )
        self.assertEqual(classify_scsi_result(3, text), OUTCOME_COULD_NOT_SEND)

    def test_usbscsi_scsi_status(self):
        text = "usbscsi: DEVICE_REJECTED SCSI status 1"
        self.assertEqual(classify_scsi_result(1, text), OUTCOME_DEVICE_REJECTED)

    def test_sg_raw_check_condition(self):
        text = "SCSI Status: Check Condition  sense key: Illegal Request"
        self.assertEqual(classify_scsi_result(5, text), OUTCOME_DEVICE_REJECTED)

    def test_live_unknown_command_probe_is_could_not_send(self):
        text = (
            "ipod_sg_raw: no SCSITask user client "
            "(kernel mass-storage driver may own the disk)\n"
            "usbscsi: CLEAR_FEATURE(ENDPOINT_HALT) IN=0x83 OUT=0x02 (open)\n"
            "usbscsi: unknown command probe"
        )
        self.assertEqual(classify_scsi_result(1, text), OUTCOME_COULD_NOT_SEND)

    def test_csw_status_1_is_rejected_status_2_is_not(self):
        self.assertEqual(
            classify_scsi_result(1, "usbscsi: DEVICE_REJECTED SCSI CHECK CONDITION CSW status 1"),
            OUTCOME_DEVICE_REJECTED,
        )
        self.assertEqual(
            classify_scsi_result(3, "usbscsi: COULD_NOT_SEND SCSI CSW status 2"),
            OUTCOME_COULD_NOT_SEND,
        )
        self.assertEqual(
            classify_scsi_result(1, "usbscsi: DEVICE_REJECTED SCSI status 2"),
            OUTCOME_COULD_NOT_SEND,
        )
        self.assertEqual(classify_scsi_result(3, "SCSI status 12"), OUTCOME_COULD_NOT_SEND)

    def test_usage_exit_2_is_could_not_send(self):
        self.assertEqual(
            classify_scsi_result(2, "usbscsi: unknown command probe"),
            OUTCOME_COULD_NOT_SEND,
        )

    def test_live_createscsitask_is_could_not_send(self):
        text = (
            "ipod_sg_raw: using com_apple_driver_iPodSBCNub\n"
            "ipod_sg_raw: COULD_NOT_SEND CreateSCSITask failed\n"
            "usbscsi: Darwin USBInterfaceOpenSeize nudge did not seize "
            "(libusb claim still tried)\n"
            "usbscsi: SetAutoDetach ok\n"
            "usbscsi: claim attempt 1/4\n"
            "usbscsi:   libusb: access denied [code -3] (LIBUSB_ERROR_ACCESS; "
            "IOUSBMassStorageDriver still owns iface 0)\n"
        )
        self.assertEqual(classify_scsi_result(3, text), OUTCOME_COULD_NOT_SEND)
        self.assertNotEqual(classify_scsi_result(3, text), OUTCOME_DEVICE_REJECTED)

    def test_live_check_condition_is_rejected(self):
        text = (
            "ipod_sg_raw: DEVICE_REJECTED SCSI status 0x2 autosense key 0x0 "
            "asc 0x0 ascq 0x0 (realized=0; IOKit often leaves sense 0/0/0)\n"
            "ipod_sg_raw: REQUEST SENSE CDB 0x03 service_response=2 "
            "scsi_status=0x0 realized=18\n"
            "ipod_sg_raw: REQUEST SENSE key 0x5 asc 0x20 ascq 0x0\n"
            "ipod_sg_raw: C6 96 04 not accepted (untether SCSI handler not active)"
        )
        self.assertEqual(classify_scsi_result(1, text), OUTCOME_DEVICE_REJECTED)

    def test_join_helper_detail_does_not_double_prefix(self):
        joined = join_helper_detail(
            "ipod_sg_raw: using com_apple_driver_iPodSBCNub",
            "usbscsi: SetAutoDetach ok\nusbscsi: claimed iface=0",
        )
        self.assertIn("ipod_sg_raw: using", joined)
        self.assertIn("usbscsi: claimed iface=0", joined)
        self.assertNotIn("usbscsi: usbscsi:", joined)
        self.assertEqual(join_helper_detail("", None, "  "), "")


class EnsureScsiHelpersTests(unittest.TestCase):
    def test_ensure_scsi_helpers_rebuilds_on_macos(self):
        from unittest import mock
        from host.scsi import ensure_scsi_helpers

        calls = []

        def fake_sg():
            calls.append("sg")
            return Path("/tmp/ipod_sg_raw-hashed")

        def fake_usb():
            calls.append("usb")
            return Path("/tmp/usbscsi-hashed")

        with mock.patch("host.scsi.is_macos", return_value=True), mock.patch(
            "host.scsi.build_ipod_sg_raw", side_effect=fake_sg
        ), mock.patch("host.scsi.build_usbscsi", side_effect=fake_usb), mock.patch(
            "builtins.print"
        ):
            ensure_scsi_helpers()
        self.assertEqual(calls, ["sg", "usb"])

    def test_ensure_scsi_helpers_skips_linux(self):
        from unittest import mock
        from host.scsi import ensure_scsi_helpers

        with mock.patch("host.scsi.is_macos", return_value=False), mock.patch(
            "host.scsi.build_ipod_sg_raw"
        ) as sg, mock.patch("host.scsi.build_usbscsi") as usb:
            ensure_scsi_helpers()
        sg.assert_not_called()
        usb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
