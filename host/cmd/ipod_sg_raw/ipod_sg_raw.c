/*
 * SPDX-License-Identifier: MIT
 *
 * ipod_sg_raw: macOS IOKit SCSITask helper for iPod nano 7G vendor C6.
 *
 * Same idea as Raphmatt/nano7-a1717-dock tools/macos/ipod_sg_raw.c
 * (SCSITaskDeviceInterface, kIOSCSITaskDeviceUserClientTypeID). This is
 * not a new exploit. C6 96 is the ipod_sun / star harness already in
 * RetailOS after the Helvetica CFF untether. Probe (op 0x04) and peek
 * (op 0x02) only. No WRITE(10), no IPodUpdate, no exec, no format.
 *
 * Live test: CreateSCSITask returned NULL after matching
 * com_apple_driver_iPodSBCNub. a1717-dock keeps the IOCFPlugInInterface
 * until teardown; destroying it after QueryInterface can drop the user
 * client so CreateSCSITask fails even when ObtainExclusiveAccess printed
 * success. Exclusive also needs IOMedia/IOBlockStorageServices gone:
 * DADiskClaim + DADiskUnmount (force, whole) before ObtainExclusiveAccess.
 *
 * Live test: C6 96 04 reached the nano (DADiskClaim ok, exclusive ok,
 * ExecuteTaskSync ok) then CHECK CONDITION scsi_status=0x2 with autosense
 * key/asc/ascq 0/0/0 (realized=0). IOKit did not fill SCSI_Sense_Data.
 * Issue REQUEST SENSE (CDB 0x03) explicitly after CHECK CONDITION.
 *
 * Apple QA1179: no public SCSI passthrough ioctl on /dev/rdisk (no DKIOCSCSIUSERCMD).
 * ipod_sun sends C6 with Linux sg_raw only.
 *
 * Exit codes:
 *   0  C6_96_04_OK / INQUIRY_OK / PEEK_OK (CDB sent, status GOOD)
 *   1  DEVICE_REJECTED (CDB sent, SCSI CHECK CONDITION / sense)
 *   2  usage
 *   3  COULD_NOT_SEND (no nub, no plugin, exclusive busy, CreateSCSITask)
 *
 * Never print "handler not active" unless the CDB was sent and rejected.
 *
 * usage:
 *   ipod_sg_raw inquiry
 *   ipod_sg_raw probe
 *   ipod_sg_raw peek 0x09120000 0x200
 *   ipod_sg_raw --bsd disk4 probe
 */
#include <CoreFoundation/CoreFoundation.h>
#include <DiskArbitration/DiskArbitration.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOReturn.h>
#include <IOKit/scsi/SCSITaskLib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef kIOMainPortDefault
#define kIOMainPortDefault kIOMasterPortDefault
#endif
#ifndef kSCSIDataTransfer_NoDataTransfer
#define kSCSIDataTransfer_NoDataTransfer 0x00
#endif
#ifndef kSCSIDataTransfer_FromInitiatorToTarget
#define kSCSIDataTransfer_FromInitiatorToTarget 0x01
#endif
#ifndef kSCSIDataTransfer_FromTargetToInitiator
#define kSCSIDataTransfer_FromTargetToInitiator 0x02
#endif
#ifndef kSCSIServiceResponse_TASK_COMPLETE
#define kSCSIServiceResponse_TASK_COMPLETE 0x02
#endif
#ifndef kSCSITaskStatus_GOOD
#define kSCSITaskStatus_GOOD 0x00
#endif
#ifndef kSCSITaskStatus_CHECK_CONDITION
#define kSCSITaskStatus_CHECK_CONDITION 0x02
#endif

#define RC_OK 0
#define RC_REJECTED 1
#define RC_USAGE 2
#define RC_COULD_NOT_SEND 3

static const char *g_bsd = NULL;
static IOCFPlugInInterface **g_plugin = NULL;
static DASessionRef g_da_session = NULL;
static DADiskRef g_da_disk = NULL;
static volatile int g_da_done = 0;
static volatile int g_da_ok = 0;

static const char *ior_name(kern_return_t kr) {
    if (kr == KERN_SUCCESS) {
        return "success";
    }
    if (kr == kIOReturnBusy) {
        return "busy";
    }
    if (kr == kIOReturnExclusiveAccess) {
        return "exclusive";
    }
    if (kr == kIOReturnNotOpen) {
        return "not_open";
    }
    if (kr == kIOReturnNoDevice) {
        return "no_device";
    }
    if (kr == kIOReturnNotPrivileged) {
        return "not_privileged";
    }
    if (kr == kIOReturnUnsupported) {
        return "unsupported";
    }
    if (kr == kIOReturnError) {
        return "error";
    }
    if (kr == kIOReturnBadArgument) {
        return "bad_argument";
    }
    return "other";
}

static void log_kr(const char *what, kern_return_t kr) {
    fprintf(
        stderr,
        "ipod_sg_raw: %s kr=0x%08x (%s)\n",
        what,
        (unsigned)kr,
        ior_name(kr));
}

static void die_send(const char *msg) {
    fprintf(stderr, "ipod_sg_raw: COULD_NOT_SEND %s\n", msg);
    exit(RC_COULD_NOT_SEND);
}

static int cf_eq_cstr(CFTypeRef value, const char *want) {
    char buf[64];
    if (!value || !want) {
        return 0;
    }
    if (CFGetTypeID(value) == CFStringGetTypeID()) {
        if (!CFStringGetCString((CFStringRef)value, buf, sizeof(buf), kCFStringEncodingUTF8)) {
            return 0;
        }
        return strcasecmp(buf, want) == 0;
    }
    if (CFGetTypeID(value) == CFDataGetTypeID()) {
        CFIndex n = CFDataGetLength((CFDataRef)value);
        if (n <= 0 || n >= (CFIndex)sizeof(buf)) {
            return 0;
        }
        memcpy(buf, CFDataGetBytePtr((CFDataRef)value), (size_t)n);
        buf[n] = 0;
        while (n > 0 && (buf[n - 1] == ' ' || buf[n - 1] == 0)) {
            buf[--n] = 0;
        }
        return strcasecmp(buf, want) == 0;
    }
    return 0;
}

static int media_bsd_matches(io_service_t service, const char *bsd) {
    io_iterator_t it = IO_OBJECT_NULL;
    kern_return_t kr = IORegistryEntryCreateIterator(
        service, kIOServicePlane, kIORegistryIterateRecursively, &it);
    if (kr != KERN_SUCCESS) {
        return 0;
    }
    int hit = 0;
    io_service_t child;
    while ((child = IOIteratorNext(it)) != IO_OBJECT_NULL) {
        CFTypeRef name = IORegistryEntryCreateCFProperty(
            child, CFSTR("BSD Name"), kCFAllocatorDefault, 0);
        if (name) {
            char buf[32] = {0};
            if (CFGetTypeID(name) == CFStringGetTypeID() &&
                CFStringGetCString((CFStringRef)name, buf, sizeof(buf), kCFStringEncodingUTF8)) {
                if (strcmp(buf, bsd) == 0 ||
                    (strncmp(bsd, "r", 1) == 0 && strcmp(buf, bsd + 1) == 0)) {
                    hit = 1;
                }
            }
            CFRelease(name);
        }
        IOObjectRelease(child);
        if (hit) {
            break;
        }
    }
    IOObjectRelease(it);
    return hit;
}

static int is_apple_ipod_nub(io_service_t service) {
    CFTypeRef vendor = IORegistryEntryCreateCFProperty(
        service, CFSTR("Vendor Identification"), kCFAllocatorDefault, 0);
    CFTypeRef product = IORegistryEntryCreateCFProperty(
        service, CFSTR("Product Identification"), kCFAllocatorDefault, 0);
    int ok = cf_eq_cstr(vendor, "Apple") && cf_eq_cstr(product, "iPod");
    if (vendor) {
        CFRelease(vendor);
    }
    if (product) {
        CFRelease(product);
    }
    if (!ok) {
        return 0;
    }
    if (g_bsd && g_bsd[0] && !media_bsd_matches(service, g_bsd)) {
        return 0;
    }
    return 1;
}

static int is_ipod_sbc_nub(io_service_t service) {
    CFTypeRef cat = IORegistryEntryCreateCFProperty(
        service, CFSTR("SCSITaskDeviceCategory"), kCFAllocatorDefault, 0);
    int ok = cf_eq_cstr(cat, "iPodUserClientDevice");
    if (cat) {
        CFRelease(cat);
    }
    if (!ok) {
        return 0;
    }
    if (g_bsd && g_bsd[0] && !media_bsd_matches(service, g_bsd)) {
        return 0;
    }
    return 1;
}

static io_service_t find_matching(const char *cls, int (*pred)(io_service_t)) {
    CFMutableDictionaryRef matching = IOServiceMatching(cls);
    if (!matching) {
        return IO_OBJECT_NULL;
    }
    io_iterator_t it = IO_OBJECT_NULL;
    kern_return_t kr = IOServiceGetMatchingServices(kIOMainPortDefault, matching, &it);
    log_kr(cls, kr);
    if (kr != KERN_SUCCESS || it == IO_OBJECT_NULL) {
        return IO_OBJECT_NULL;
    }
    io_service_t found = IO_OBJECT_NULL;
    io_service_t svc;
    while ((svc = IOIteratorNext(it)) != IO_OBJECT_NULL) {
        if (pred(svc)) {
            found = svc;
            break;
        }
        IOObjectRelease(svc);
    }
    IOObjectRelease(it);
    return found;
}

static io_service_t find_by_task_category(void) {
    CFMutableDictionaryRef matching = CFDictionaryCreateMutable(
        kCFAllocatorDefault,
        0,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    if (!matching) {
        return IO_OBJECT_NULL;
    }
    CFDictionarySetValue(
        matching, CFSTR("SCSITaskDeviceCategory"), CFSTR("iPodUserClientDevice"));
    io_iterator_t it = IO_OBJECT_NULL;
    kern_return_t kr = IOServiceGetMatchingServices(kIOMainPortDefault, matching, &it);
    log_kr("SCSITaskDeviceCategory=iPodUserClientDevice", kr);
    if (kr != KERN_SUCCESS || it == IO_OBJECT_NULL) {
        return IO_OBJECT_NULL;
    }
    io_service_t found = IO_OBJECT_NULL;
    io_service_t svc;
    while ((svc = IOIteratorNext(it)) != IO_OBJECT_NULL) {
        if (!g_bsd || !g_bsd[0] || media_bsd_matches(svc, g_bsd)) {
            found = svc;
            break;
        }
        IOObjectRelease(svc);
    }
    IOObjectRelease(it);
    return found;
}

static io_service_t find_ipod_nub(const char **kind) {
    io_service_t found = find_matching("com_apple_driver_iPodSBCNub", is_ipod_sbc_nub);
    if (found != IO_OBJECT_NULL) {
        *kind = "com_apple_driver_iPodSBCNub";
        return found;
    }
    found = find_by_task_category();
    if (found != IO_OBJECT_NULL) {
        *kind = "SCSITaskDeviceCategory iPodUserClientDevice";
        return found;
    }
    found = find_matching("IOSCSIPeripheralDeviceNub", is_apple_ipod_nub);
    if (found != IO_OBJECT_NULL) {
        *kind = "IOSCSIPeripheralDeviceNub";
        return found;
    }
    found = find_matching("IOSCSIPeripheralDeviceType00", is_apple_ipod_nub);
    if (found != IO_OBJECT_NULL) {
        *kind = "IOSCSIPeripheralDeviceType00";
        return found;
    }
    *kind = NULL;
    return IO_OBJECT_NULL;
}

static void dump_nub(io_service_t service) {
    io_name_t cls;
    if (IOObjectGetClass(service, cls) == KERN_SUCCESS) {
        fprintf(stderr, "ipod_sg_raw: nub class %s\n", cls);
    }
    io_iterator_t it = IO_OBJECT_NULL;
    if (IORegistryEntryGetChildIterator(service, kIOServicePlane, &it) != KERN_SUCCESS) {
        return;
    }
    io_service_t child;
    while ((child = IOIteratorNext(it)) != IO_OBJECT_NULL) {
        io_name_t cname;
        if (IOObjectGetClass(child, cname) == KERN_SUCCESS) {
            fprintf(stderr, "ipod_sg_raw: child %s\n", cname);
        }
        IOObjectRelease(child);
    }
    IOObjectRelease(it);
}

static void bsd_whole(const char *in, char *out, size_t n) {
    const char *p = in ? in : "";
    if (p[0] == '/' ) {
        const char *slash = strrchr(p, '/');
        if (slash && slash[1]) {
            p = slash + 1;
        }
    }
    if (p[0] == 'r' && strncmp(p, "rdisk", 5) == 0) {
        p++;
    }
    snprintf(out, n, "%s", p);
    if (strncmp(out, "disk", 4) == 0) {
        size_t i = 4;
        while (out[i] >= '0' && out[i] <= '9') {
            i++;
        }
        out[i] = 0;
    }
}

static void da_cb(DADiskRef disk, DADissenterRef dissenter, void *ctx) {
    (void)disk;
    (void)ctx;
    g_da_ok = (dissenter == NULL);
    g_da_done = 1;
}

static int da_wait(const char *what, double seconds) {
    CFAbsoluteTime deadline = CFAbsoluteTimeGetCurrent() + seconds;
    while (!g_da_done && CFAbsoluteTimeGetCurrent() < deadline) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, true);
    }
    fprintf(
        stderr,
        "ipod_sg_raw: %s done=%d ok=%d\n",
        what,
        g_da_done,
        g_da_ok);
    return g_da_ok ? 0 : -1;
}

static void dadisk_claim(const char *bsd) {
    char whole[32];
    if (!bsd || !bsd[0]) {
        return;
    }
    bsd_whole(bsd, whole, sizeof(whole));
    if (strncmp(whole, "disk", 4) != 0) {
        fprintf(stderr, "ipod_sg_raw: DADiskClaim skip (bsd %s)\n", whole);
        return;
    }
    g_da_session = DASessionCreate(kCFAllocatorDefault);
    if (!g_da_session) {
        fprintf(stderr, "ipod_sg_raw: DASessionCreate failed\n");
        return;
    }
    DASessionScheduleWithRunLoop(g_da_session, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    g_da_disk = DADiskCreateFromBSDName(kCFAllocatorDefault, g_da_session, whole);
    if (!g_da_disk) {
        fprintf(stderr, "ipod_sg_raw: DADiskCreateFromBSDName %s failed\n", whole);
        return;
    }
    fprintf(stderr, "ipod_sg_raw: DADiskUnmount force whole %s\n", whole);
    g_da_done = 0;
    g_da_ok = 0;
    DADiskUnmount(
        g_da_disk,
        kDADiskUnmountOptionWhole | kDADiskUnmountOptionForce,
        da_cb,
        NULL);
    da_wait("DADiskUnmount", 4.0);
    fprintf(stderr, "ipod_sg_raw: DADiskClaim %s\n", whole);
    g_da_done = 0;
    g_da_ok = 0;
    DADiskClaim(g_da_disk, kDADiskClaimOptionDefault, NULL, NULL, da_cb, NULL);
    da_wait("DADiskClaim", 4.0);
}

static void dadisk_unclaim(void) {
    if (g_da_disk) {
        DADiskUnclaim(g_da_disk);
        CFRelease(g_da_disk);
        g_da_disk = NULL;
    }
    if (g_da_session) {
        DASessionUnscheduleFromRunLoop(
            g_da_session, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
        CFRelease(g_da_session);
        g_da_session = NULL;
    }
}

static UInt8 pad_cdb_len(UInt8 n) {
    /* SCSITaskLib only accepts 6/10/12/16. Vendor C6 96 is 7 bytes; pad to 10. */
    if (n <= 6) {
        return 6;
    }
    if (n <= 10) {
        return 10;
    }
    if (n <= 12) {
        return 12;
    }
    return 16;
}

static SCSITaskDeviceInterface **open_task_device(io_service_t service) {
    IOCFPlugInInterface **plugin = NULL;
    SInt32 score = 0;
    kern_return_t kr = IOCreatePlugInInterfaceForService(
        service,
        kIOSCSITaskDeviceUserClientTypeID,
        kIOCFPlugInInterfaceID,
        &plugin,
        &score);
    log_kr("IOCreatePlugInInterfaceForService kIOSCSITaskDeviceUserClientTypeID", kr);
    fprintf(stderr, "ipod_sg_raw: plugin=%p score=%d\n", (void *)plugin, (int)score);
    if (kr != KERN_SUCCESS || !plugin) {
        die_send(
            "no SCSITask user client (IOCreatePlugInInterfaceForService failed; "
            "need com_apple_driver_iPodSBCNub, not only IOSCSIPeripheralDeviceNub)");
    }
    SCSITaskDeviceInterface **dev = NULL;
    HRESULT hr = (*plugin)->QueryInterface(
        plugin, CFUUIDGetUUIDBytes(kIOSCSITaskDeviceInterfaceID), (LPVOID *)&dev);
    fprintf(
        stderr,
        "ipod_sg_raw: QueryInterface SCSITaskDeviceInterface hr=0x%08x dev=%p\n",
        (unsigned)hr,
        (void *)dev);
    /* Keep plugin until teardown. a1717-dock does this; live test
     * CreateSCSITask failed after we destroyed the plugin early. */
    g_plugin = plugin;
    if (hr != S_OK || !dev) {
        die_send("QueryInterface SCSITaskDeviceInterface failed");
    }
    Boolean avail = (*dev)->IsExclusiveAccessAvailable(dev);
    fprintf(stderr, "ipod_sg_raw: IsExclusiveAccessAvailable=%d\n", (int)avail);
    kr = (*dev)->ObtainExclusiveAccess(dev);
    log_kr("ObtainExclusiveAccess", kr);
    if (kr != KERN_SUCCESS) {
        dump_nub(service);
        IOServiceRequestProbe(service, 0);
        usleep(250000);
        kr = (*dev)->ObtainExclusiveAccess(dev);
        log_kr("ObtainExclusiveAccess retry after IOServiceRequestProbe", kr);
    }
    if (kr != KERN_SUCCESS) {
        (*dev)->Release(dev);
        if (kr == kIOReturnBusy) {
            die_send(
                "ObtainExclusiveAccess busy; unmount the iPod volume first: "
                "diskutil unmountDisk force. IOBlockStorageServices/IOMedia "
                "clients may still hold the disk.");
        }
        if (kr == kIOReturnExclusiveAccess) {
            die_send(
                "ObtainExclusiveAccess exclusive; another SCSITask client holds "
                "the device (quit Music, close Finder IPOD windows)");
        }
        fprintf(
            stderr,
            "ipod_sg_raw: COULD_NOT_SEND ObtainExclusiveAccess 0x%08x (%s); "
            "unmount the iPod volume first: diskutil unmountDisk force\n",
            (unsigned)kr,
            ior_name(kr));
        exit(RC_COULD_NOT_SEND);
    }
    return dev;
}

static int issue_request_sense(
    SCSITaskDeviceInterface **dev,
    UInt8 *buf,
    UInt8 buflen,
    UInt64 *got_out) {
    /* Separate task. Do not call run_cdb: that would recurse on CHECK CONDITION. */
    SCSITaskInterface **task = (*dev)->CreateSCSITask(dev);
    if (!task) {
        fprintf(stderr, "ipod_sg_raw: REQUEST SENSE CreateSCSITask NULL\n");
        return RC_COULD_NOT_SEND;
    }
    UInt8 cdb[6];
    memset(cdb, 0, sizeof(cdb));
    cdb[0] = 0x03; /* REQUEST SENSE */
    cdb[4] = buflen;
    IOVirtualRange range;
    range.address = (IOVirtualAddress)buf;
    range.length = buflen;
    SCSI_Sense_Data autosense;
    SCSITaskStatus status = 0;
    UInt64 got = 0;
    SCSIServiceResponse response = 0;
    memset(&autosense, 0, sizeof(autosense));
    memset(buf, 0, buflen);
    kern_return_t kr = (*task)->SetCommandDescriptorBlock(task, cdb, 6);
    log_kr("REQUEST SENSE SetCommandDescriptorBlock", kr);
    if (kr != KERN_SUCCESS) {
        (*task)->Release(task);
        fprintf(stderr, "ipod_sg_raw: REQUEST SENSE SetCommandDescriptorBlock failed\n");
        return RC_COULD_NOT_SEND;
    }
    kr = (*task)->SetScatterGatherEntries(
        task, &range, 1, buflen, kSCSIDataTransfer_FromTargetToInitiator);
    log_kr("REQUEST SENSE SetScatterGatherEntries", kr);
    if (kr != KERN_SUCCESS) {
        (*task)->Release(task);
        fprintf(stderr, "ipod_sg_raw: REQUEST SENSE SetScatterGatherEntries failed\n");
        return RC_COULD_NOT_SEND;
    }
    (*task)->SetTimeoutDuration(task, 4000);
    kr = (*task)->ExecuteTaskSync(task, &autosense, &status, &got);
    (*task)->GetSCSIServiceResponse(task, &response);
    (*task)->Release(task);
    log_kr("REQUEST SENSE ExecuteTaskSync", kr);
    fprintf(
        stderr,
        "ipod_sg_raw: REQUEST SENSE CDB 0x03 service_response=%u scsi_status=0x%x "
        "realized=%llu\n",
        (unsigned)response,
        (unsigned)status,
        (unsigned long long)got);
    if (got_out) {
        *got_out = got;
    }
    if (kr != KERN_SUCCESS ||
        response != kSCSIServiceResponse_TASK_COMPLETE ||
        status != kSCSITaskStatus_GOOD) {
        fprintf(
            stderr,
            "ipod_sg_raw: REQUEST SENSE failed (live test autosense was 0/0/0)\n");
        return RC_REJECTED;
    }
    {
        UInt8 key = (buflen > 2) ? (UInt8)(buf[2] & 0x0f) : 0;
        UInt8 asc = (buflen > 12) ? buf[12] : 0;
        UInt8 ascq = (buflen > 13) ? buf[13] : 0;
        unsigned i;
        fprintf(
            stderr,
            "ipod_sg_raw: REQUEST SENSE key 0x%x asc 0x%x ascq 0x%x bytes=",
            key,
            asc,
            ascq);
        for (i = 0; i < got && i < buflen; i++) {
            fprintf(stderr, "%02x", buf[i]);
        }
        fprintf(stderr, "\n");
    }
    return RC_OK;
}

static int run_cdb(
    SCSITaskDeviceInterface **dev,
    io_service_t service,
    const UInt8 *cdb,
    UInt8 cdb_len,
    void *buf,
    UInt64 buf_len,
    UInt8 direction,
    UInt64 *transferred) {
    SCSITaskInterface **task = (*dev)->CreateSCSITask(dev);
    if (!task) {
        dump_nub(service);
        fprintf(
            stderr,
            "ipod_sg_raw: COULD_NOT_SEND CreateSCSITask failed "
            "(NULL; exclusive may not be held, or IOCFPlugIn was released). "
            "IOBlockStorageServices/IOMedia clients can deny exclusive even "
            "after diskutil unmountDisk.\n");
        return RC_COULD_NOT_SEND;
    }
    UInt8 padded[16];
    memset(padded, 0, sizeof(padded));
    if (cdb_len > 16) {
        cdb_len = 16;
    }
    memcpy(padded, cdb, cdb_len);
    cdb_len = pad_cdb_len(cdb_len);
    IOVirtualRange range;
    range.address = (IOVirtualAddress)buf;
    range.length = buf_len;
    SCSI_Sense_Data sense;
    SCSITaskStatus status = 0;
    UInt64 got = 0;
    memset(&sense, 0, sizeof(sense));
    kern_return_t kr = (*task)->SetCommandDescriptorBlock(task, padded, cdb_len);
    log_kr("SetCommandDescriptorBlock", kr);
    if (kr != KERN_SUCCESS) {
        (*task)->Release(task);
        fprintf(
            stderr,
            "ipod_sg_raw: COULD_NOT_SEND SetCommandDescriptorBlock 0x%08x\n",
            (unsigned)kr);
        return RC_COULD_NOT_SEND;
    }
    if (buf_len > 0 && buf != NULL && direction != kSCSIDataTransfer_NoDataTransfer) {
        kr = (*task)->SetScatterGatherEntries(task, &range, 1, buf_len, direction);
        log_kr("SetScatterGatherEntries", kr);
        if (kr != KERN_SUCCESS) {
            (*task)->Release(task);
            fprintf(
                stderr,
                "ipod_sg_raw: COULD_NOT_SEND SetScatterGatherEntries 0x%08x\n",
                (unsigned)kr);
            return RC_COULD_NOT_SEND;
        }
    }
    (*task)->SetTimeoutDuration(task, 8000);
    kr = (*task)->ExecuteTaskSync(task, &sense, &status, &got);
    SCSIServiceResponse response = 0;
    (*task)->GetSCSIServiceResponse(task, &response);
    (*task)->Release(task);
    log_kr("ExecuteTaskSync", kr);
    fprintf(
        stderr,
        "ipod_sg_raw: service_response=%u scsi_status=0x%x realized=%llu\n",
        (unsigned)response,
        (unsigned)status,
        (unsigned long long)got);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "ipod_sg_raw: COULD_NOT_SEND ExecuteTaskSync kr=0x%08x\n", (unsigned)kr);
        return RC_COULD_NOT_SEND;
    }
    if (response != kSCSIServiceResponse_TASK_COMPLETE) {
        fprintf(
            stderr,
            "ipod_sg_raw: COULD_NOT_SEND SCSI service response %u\n",
            (unsigned)response);
        return RC_COULD_NOT_SEND;
    }
    if (status != kSCSITaskStatus_GOOD) {
        fprintf(
            stderr,
            "ipod_sg_raw: DEVICE_REJECTED SCSI status 0x%x autosense key 0x%x "
            "asc 0x%x ascq 0x%x (realized=%llu; IOKit often leaves sense 0/0/0)\n",
            status,
            sense.SENSE_KEY & 0x0f,
            sense.ADDITIONAL_SENSE_CODE,
            sense.ADDITIONAL_SENSE_CODE_QUALIFIER,
            (unsigned long long)got);
        /* Live test: CHECK CONDITION 0x2 with empty autosense. SCSI-2
         * REQUEST SENSE (CDB 0x03) is the explicit sense read. Skip if this
         * CDB already was REQUEST SENSE. */
        if (cdb_len == 0 || padded[0] != 0x03) {
            UInt8 rs[18];
            UInt64 rsgot = 0;
            memset(rs, 0, sizeof(rs));
            issue_request_sense(dev, rs, (UInt8)sizeof(rs), &rsgot);
        }
        return RC_REJECTED;
    }
    if (transferred) {
        *transferred = got;
    }
    return RC_OK;
}

static int do_inquiry(SCSITaskDeviceInterface **dev, io_service_t service) {
    UInt8 cdb[6] = {0x12, 0, 0, 0, 36, 0};
    UInt8 data[36];
    UInt64 got = 0;
    memset(data, 0, sizeof(data));
    int rc = run_cdb(
        dev, service, cdb, 6, data, sizeof(data), kSCSIDataTransfer_FromTargetToInitiator, &got);
    if (rc != RC_OK) {
        return rc;
    }
    char vendor[9];
    char product[17];
    memcpy(vendor, data + 8, 8);
    vendor[8] = 0;
    memcpy(product, data + 16, 16);
    product[16] = 0;
    printf("INQUIRY_OK vendor='%s' product='%s' bytes=%llu\n", vendor, product, (unsigned long long)got);
    return RC_OK;
}

static int do_probe(SCSITaskDeviceInterface **dev, io_service_t service) {
    /* ipod_sun: sg_raw ... c6 96 04 00 00 00 00  (enable VROM / harness present) */
    UInt8 cdb[16];
    UInt8 data[512];
    UInt64 got = 0;
    memset(cdb, 0, sizeof(cdb));
    memset(data, 0, sizeof(data));
    cdb[0] = 0xC6;
    cdb[1] = 0x96;
    cdb[2] = 0x04;
    int rc = run_cdb(
        dev, service, cdb, 7, data, sizeof(data), kSCSIDataTransfer_FromTargetToInitiator, &got);
    if (rc == RC_REJECTED) {
        fprintf(stderr, "ipod_sg_raw: C6 96 04 not accepted (untether SCSI handler not active)\n");
        return RC_REJECTED;
    }
    if (rc != RC_OK) {
        return rc;
    }
    printf("C6_96_04_OK bytes=%llu\n", (unsigned long long)got);
    return RC_OK;
}

static int do_peek(
    SCSITaskDeviceInterface **dev, io_service_t service, unsigned addr, unsigned n) {
    if (n == 0 || n > 4096) {
        fprintf(stderr, "ipod_sg_raw: peek length must be 1..4096\n");
        return RC_USAGE;
    }
    unsigned char *all = calloc(1, n);
    if (!all) {
        die_send("out of memory");
    }
    unsigned off = 0;
    while (off < n) {
        unsigned chunk = n - off;
        if (chunk > 512) {
            chunk = 512;
        }
        unsigned cur = addr + off;
        UInt8 cdb[16];
        UInt8 data[512];
        UInt64 got = 0;
        memset(cdb, 0, sizeof(cdb));
        memset(data, 0, sizeof(data));
        cdb[0] = 0xC6;
        cdb[1] = 0x96;
        cdb[2] = 0x02;
        cdb[3] = (UInt8)(cur >> 24);
        cdb[4] = (UInt8)(cur >> 16);
        cdb[5] = (UInt8)(cur >> 8);
        cdb[6] = (UInt8)cur;
        int rc = run_cdb(
            dev, service, cdb, 7, data, 512, kSCSIDataTransfer_FromTargetToInitiator, &got);
        if (rc != RC_OK) {
            free(all);
            return rc;
        }
        memcpy(all + off, data, chunk);
        off += chunk;
    }
    printf("PEEK_OK addr=0x%08x len=%u\n", addr, n);
    static const char b64[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    unsigned i;
    for (i = 0; i < n; i += 3) {
        unsigned a = all[i];
        unsigned b = (i + 1 < n) ? all[i + 1] : 0;
        unsigned c = (i + 2 < n) ? all[i + 2] : 0;
        unsigned triple = (a << 16) | (b << 8) | c;
        putchar(b64[(triple >> 18) & 63]);
        putchar(b64[(triple >> 12) & 63]);
        putchar((i + 1 < n) ? b64[(triple >> 6) & 63] : '=');
        putchar((i + 2 < n) ? b64[triple & 63] : '=');
    }
    putchar('\n');
    free(all);
    return RC_OK;
}

static void usage(void) {
    fprintf(
        stderr,
        "usage: ipod_sg_raw [--bsd diskN] inquiry|probe|peek ADDR [LEN]\n"
        "  probe = C6 96 04 (ipod_sun harness; untether SCSI handler)\n"
        "  peek  = C6 96 02 (read-only memory peek; default 0x200 bytes)\n"
        "  Never writes FAT, firmware, or bootloader.\n"
        "  Exit 0=ok 1=DEVICE_REJECTED 2=usage 3=COULD_NOT_SEND\n");
    exit(RC_USAGE);
}

static void cleanup(SCSITaskDeviceInterface **dev) {
    if (dev) {
        (*dev)->ReleaseExclusiveAccess(dev);
        (*dev)->Release(dev);
    }
    if (g_plugin) {
        IODestroyPlugInInterface(g_plugin);
        g_plugin = NULL;
    }
    dadisk_unclaim();
}

int main(int argc, char **argv) {
    int i = 1;
    if (i < argc && strcmp(argv[i], "--bsd") == 0) {
        if (i + 1 >= argc) {
            usage();
        }
        g_bsd = argv[i + 1];
        i += 2;
    }
    if (i >= argc) {
        usage();
    }
    const char *cmd = argv[i++];
    if (g_bsd && g_bsd[0]) {
        dadisk_claim(g_bsd);
    }
    const char *kind = NULL;
    io_service_t nub = find_ipod_nub(&kind);
    if (nub == IO_OBJECT_NULL) {
        dadisk_unclaim();
        die_send(
            "no Apple iPod SCSI nub (is 05ac:1267 connected? "
            "look for com_apple_driver_iPodSBCNub)");
    }
    fprintf(stderr, "ipod_sg_raw: using %s\n", kind ? kind : "unknown");
    dump_nub(nub);
    SCSITaskDeviceInterface **dev = open_task_device(nub);
    int rc = RC_USAGE;
    if (strcmp(cmd, "inquiry") == 0) {
        rc = do_inquiry(dev, nub);
    } else if (strcmp(cmd, "probe") == 0) {
        rc = do_probe(dev, nub);
    } else if (strcmp(cmd, "peek") == 0) {
        if (i >= argc) {
            IOObjectRelease(nub);
            cleanup(dev);
            usage();
        }
        unsigned addr = (unsigned)strtoul(argv[i++], NULL, 0);
        unsigned n = 0x200;
        if (i < argc) {
            n = (unsigned)strtoul(argv[i], NULL, 0);
        }
        rc = do_peek(dev, nub, addr, n);
    } else {
        IOObjectRelease(nub);
        cleanup(dev);
        usage();
    }
    IOObjectRelease(nub);
    cleanup(dev);
    return rc;
}
