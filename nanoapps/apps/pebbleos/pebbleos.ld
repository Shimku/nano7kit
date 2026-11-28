/* Relocatable link of the hosted PebbleOS NanoApps image (base 0).
 * Iterable sections keep their names so GNU ld defines __start_i_* /
 * __stop_i_*. ARM unwinder tables are dropped: the device layout does
 * not use a host libc unwinder. Writable data after the iterable sections
 * is snapshotted on first launch (see __pristine_start__ below).
 */
ENTRY(payload_entry)
SECTIONS {
    . = 0;
    .text : {
        *(.text.entry)
        *(.text*)
        *(.rodata*)
        . = ALIGN(16);
        __snap_start__ = .;
    }
    i_analytics : {
        PROVIDE(__start_i_analytics = .);
        KEEP(*(i_analytics))
        PROVIDE(__stop_i_analytics = .);
    }
    i_bt_bond : {
        PROVIDE(__start_i_bt_bond = .);
        KEEP(*(i_bt_bond))
        PROVIDE(__stop_i_bt_bond = .);
    }
    i_bt_gattc_ntf : {
        PROVIDE(__start_i_bt_gattc_ntf = .);
        KEEP(*(i_bt_gattc_ntf))
        PROVIDE(__stop_i_bt_gattc_ntf = .);
    }
    i_bt_gatts : {
        PROVIDE(__start_i_bt_gatts = .);
        KEEP(*(i_bt_gatts))
        PROVIDE(__stop_i_bt_gatts = .);
    }
    i_bt_pairing : {
        PROVIDE(__start_i_bt_pairing = .);
        KEEP(*(i_bt_pairing))
        PROVIDE(__stop_i_bt_pairing = .);
    }
    i_cd_backends : {
        PROVIDE(__start_i_cd_backends = .);
        KEEP(*(i_cd_backends))
        PROVIDE(__stop_i_cd_backends = .);
    }
    i_cd_regions : {
        PROVIDE(__start_i_cd_regions = .);
        KEEP(*(i_cd_regions))
        PROVIDE(__stop_i_cd_regions = .);
    }
    i_dev_deps : {
        PROVIDE(__start_i_dev_deps = .);
        KEEP(*(i_dev_deps))
        PROVIDE(__stop_i_dev_deps = .);
    }
    i_devices : {
        PROVIDE(__start_i_devices = .);
        KEEP(*(i_devices))
        PROVIDE(__stop_i_devices = .);
    }
    i_flash_nor_part : {
        PROVIDE(__start_i_flash_nor_part = .);
        KEEP(*(i_flash_nor_part))
        PROVIDE(__stop_i_flash_nor_part = .);
    }
    i_idle_hooks : {
        PROVIDE(__start_i_idle_hooks = .);
        KEEP(*(i_idle_hooks))
        PROVIDE(__stop_i_idle_hooks = .);
    }
    i_idle_late : {
        PROVIDE(__start_i_idle_late = .);
        KEEP(*(i_idle_late))
        PROVIDE(__stop_i_idle_late = .);
    }
    i_input_cbs : {
        PROVIDE(__start_i_input_cbs = .);
        KEEP(*(i_input_cbs))
        PROVIDE(__stop_i_input_cbs = .);
    }
    i_irq_prio : {
        PROVIDE(__start_i_irq_prio = .);
        KEEP(*(i_irq_prio))
        PROVIDE(__stop_i_irq_prio = .);
    }
    i_log_backends : {
        PROVIDE(__start_i_log_backends = .);
        KEEP(*(i_log_backends))
        PROVIDE(__stop_i_log_backends = .);
    }
    i_pp_endpoints : {
        PROVIDE(__start_i_pp_endpoints = .);
        KEEP(*(i_pp_endpoints))
        PROVIDE(__stop_i_pp_endpoints = .);
    }
    i_pp_listeners : {
        PROVIDE(__start_i_pp_listeners = .);
        KEEP(*(i_pp_listeners))
        PROVIDE(__stop_i_pp_listeners = .);
    }
    i_profiler_nodes : {
        PROVIDE(__start_i_profiler_nodes = .);
        KEEP(*(i_profiler_nodes))
        PROVIDE(__stop_i_profiler_nodes = .);
    }
    i_shell_root : {
        PROVIDE(__start_i_shell_root = .);
        KEEP(*(i_shell_root))
        PROVIDE(__stop_i_shell_root = .);
    }
    i_shell_subcmds : {
        PROVIDE(__start_i_shell_subcmds = .);
        KEEP(*(i_shell_subcmds))
        PROVIDE(__stop_i_shell_subcmds = .);
    }
    i_sys_off : {
        PROVIDE(__start_i_sys_off = .);
        KEEP(*(i_sys_off))
        PROVIDE(__stop_i_sys_off = .);
    }
    i_usb_dfu_regions : {
        PROVIDE(__start_i_usb_dfu_regions = .);
        KEEP(*(i_usb_dfu_regions))
        PROVIDE(__stop_i_usb_dfu_regions = .);
    }
    i_usbd_functions : {
        PROVIDE(__start_i_usbd_functions = .);
        KEEP(*(i_usbd_functions))
        PROVIDE(__stop_i_usbd_functions = .);
    }
    i_usbd_listeners : {
        PROVIDE(__start_i_usbd_listeners = .);
        KEEP(*(i_usbd_listeners))
        PROVIDE(__stop_i_usbd_listeners = .);
    }
    .data : {
        *(.data*)
        . = ALIGN(16);
        __snap_end__ = .;
    }
    .bss : {
        __bss_start__ = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(8);
        __bss_end__ = .;
        /* A copy of everything from __snap_start__ to __snap_end__ (the
         * iterable sections and .data), taken on the first launch. It sits
         * after __bss_end__ so the per-launch .bss clear leaves it alone.
         * If the launcher reuses the loaded image, the app puts .data back
         * from here before starting the kernel again. */
        __pristine_start__ = .;
        . += (__snap_end__ - __snap_start__);
        __pristine_end__ = .;
    }
    /DISCARD/ : {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.note*)
        *(.comment)
        *(.init_array*)
        *(.fini_array*)
    }
}
