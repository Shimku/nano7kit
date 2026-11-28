#!/usr/bin/env python3
"""Copy the nano7_emery overlay onto a fetched PebbleOS tree and patch hooks."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
OVERLAY = APP / "overlay"


def _replace(path: Path, old: str, new: str, *, required: bool = True) -> None:
    text = path.read_text()
    if old not in text:
        if required:
            raise SystemExit(f"{path}: patch anchor not found:\n{old[:120]}")
        return
    if text.count(old) != 1 and required:
        raise SystemExit(f"{path}: patch anchor is not unique ({text.count(old)} times)")
    path.write_text(text.replace(old, new))


def apply(tree: Path) -> None:
    tree = Path(tree)
    if not (tree / "pbl.yml").is_file() or not (tree / "kernel").is_dir():
        raise SystemExit(f"not a PebbleOS tree: {tree}")
    for src in OVERLAY.rglob("*"):
        if not src.is_file():
            continue
        dest = tree / src.relative_to(OVERLAY)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    _replace(
        tree / "kernel/arch/posix/Kconfig",
        """config ARCH_POSIX_FIBERS
    bool
    depends on ARCH_POSIX
    help
      Kernel threads are Emscripten fibers on a single host thread,
      which runs the kernel until it idles. Selected by hosts without
      threads to spare, such as a browser.
""",
        """config ARCH_POSIX_FIBERS
    bool
    depends on ARCH_POSIX
    help
      Kernel threads are Emscripten fibers on a single host thread,
      which runs the kernel until it idles. Selected by hosts without
      threads to spare, such as a browser.

config ARCH_POSIX_FIBERS_SETJMP
    bool
    depends on ARCH_POSIX_FIBERS
    help
      Fiber backend that switches with setjmp/longjmp on one thread,
      using the kernel's own stacks. Used by the hosted nano 7G board.

config ARCH_POSIX_ARM
    bool
    depends on ARCH_POSIX
    help
      Compile the POSIX-hosted kernel as Cortex-A5 Thumb with
      VFPv4-D16, for a NanoApps .hbapp, instead of a host libc binary.
""",
    )

    _replace(
        tree / "kernel/arch/posix/CMakeLists.txt",
        """pbl_library_sources(
  cycle.c
  mcu.c
)
if(CONFIG_ARCH_POSIX_FIBERS)
  pbl_library_sources(fiber.c)
else()
  pbl_library_sources(
    arch.c
    native.c
  )
endif()
""",
        """if(CONFIG_NANO7)
  pbl_library_sources(
    cycle_nano.c
    mcu.c
  )
else()
  pbl_library_sources(
    cycle.c
    mcu.c
  )
endif()
if(CONFIG_ARCH_POSIX_FIBERS_SETJMP)
  pbl_library_sources(fiber_setjmp.c)
elseif(CONFIG_ARCH_POSIX_FIBERS)
  pbl_library_sources(fiber.c)
else()
  pbl_library_sources(
    arch.c
    native.c
  )
endif()
""",
    )

    _replace(
        tree / "soc/native/posix/Kconfig",
        """config SDL
    bool
    depends on SOC_POSIX
    help
      SDL, for the host halves of the drivers of a native build that
      need a window or its input. Selected by those drivers.
""",
        """config SDL
    bool
    depends on SOC_POSIX
    help
      SDL, for the host halves of the drivers of a native build that
      need a window or its input. Selected by those drivers.

config NANO7
    bool
    depends on SOC_POSIX
    select ARCH_POSIX_ARM
    select ARCH_POSIX_FIBERS
    select ARCH_POSIX_FIBERS_SETJMP
    help
      Host the firmware as a NanoApps app on the iPod nano 7G.
""",
    )

    _replace(
        tree / "soc/native/posix/CMakeLists.txt",
        """pbl_library()
pbl_include_directories(include)
pbl_library_sources(
  memory.c
  soc.c
)

pbl_host_include_directories(include)
pbl_host_sources(host/main.c)

if(CONFIG_SDL)
  add_subdirectory(sdl)
endif()
""",
        """pbl_library()
pbl_include_directories(include)
if(CONFIG_NANO7)
  pbl_library_sources(
    memory_nano.c
    nano_host.c
    soc.c
  )
else()
  pbl_library_sources(
    memory.c
    soc.c
  )
  pbl_host_include_directories(include)
  pbl_host_sources(host/main.c)
endif()

if(CONFIG_SDL)
  add_subdirectory(sdl)
endif()
""",
    )

    _replace(
        tree / "cmake/modules/compiler.cmake",
        """if(CONFIG_ARCH_POSIX)
  # Host clang does not know some of the GCC-only flags shared with ARM.
""",
        """if(CONFIG_ARCH_POSIX_ARM)
  set(pbl_arch_flags
    -mcpu=cortex-a5
    -mthumb
    -mfloat-abi=soft
    -mfpu=vfpv4-d16
    -mword-relocations
    -ffreestanding
    -ffunction-sections
    -fdata-sections
    -fbuiltin
    -fno-exceptions
  )
  if(NOT CONFIG_BT)
    include_directories(${PBL_BASE}/include/nimble_stubs)
  endif()
elseif(CONFIG_ARCH_POSIX)
  # Host clang does not know some of the GCC-only flags shared with ARM.
""",
    )

    fw = tree / "cmake/modules/firmware.cmake"
    _replace(
        fw,
        """# Without the linker script, the image is a host executable: no images.
function(pbl_link_native)
""",
        """# Hosted NanoApps library: objects plus generated resources, no executable.
function(pbl_link_nano_library)
  get_property(object_libs GLOBAL PROPERTY PBL_OBJECT_LIBS)
  get_property(static_libs GLOBAL PROPERTY PBL_STATIC_LIBS)

  add_library(pebbleos STATIC ${PBL_RESOURCE_SOURCES} ${PBL_FIRMWARE_SOURCES})
  set_target_properties(pebbleos PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY ${PROJECT_BINARY_DIR}
    OUTPUT_NAME pebbleos
  )
  add_dependencies(pebbleos pbl_generated_headers)

  foreach(lib ${object_libs})
    target_sources(pebbleos PRIVATE $<TARGET_OBJECTS:${lib}>)
  endforeach()

  # The .a is consumed by the NanoApps link, which supplies newlib-nano.
  # Kernel, util, crc and third-party code live in other static libraries;
  # cmake does not merge those into this archive, so write their paths
  # for the NanoApps link line.
  target_link_libraries(pebbleos PRIVATE pbl_interface ${static_libs})
  set(_nano_lib_paths)
  foreach(_lib ${static_libs})
    if(TARGET ${_lib})
      list(APPEND _nano_lib_paths "$<TARGET_FILE:${_lib}>")
    endif()
  endforeach()
  file(GENERATE OUTPUT ${PROJECT_BINARY_DIR}/pebbleos-static-libs.txt
       CONTENT "$<JOIN:${_nano_lib_paths},\n>\n")

  set(artifacts pebbleos)
  if(PBL_PBPACK)
    list(APPEND artifacts ${PBL_PBPACK} ${PBL_LAYOUTS})
  endif()
  add_custom_target(pbl_firmware ALL DEPENDS ${artifacts})
endfunction()

# Without the linker script, the image is a host executable: no images.
function(pbl_link_native)
""",
    )
    _replace(
        fw,
        """function(pbl_link_firmware)
  if(NOT PBL_LINKER_SCRIPT)
    pbl_link_native()
    return()
  endif()
""",
        """function(pbl_link_firmware)
  if(CONFIG_NANO7)
    pbl_link_nano_library()
    return()
  endif()
  if(NOT PBL_LINKER_SCRIPT)
    pbl_link_native()
    return()
  endif()
""",
    )

    _replace(
        tree / "include/pbl/board/partitions.h",
        """#elifdef CONFIG_BOARD_PARTITIONS_DUAL_SLOT
#include "partitions_dual_slot.h"
#elifdef CONFIG_BOARD_PARTITIONS_SINGLE_SLOT
#include "partitions_single_slot.h"
#endif
""",
        """#elifdef CONFIG_BOARD_PARTITIONS_DUAL_SLOT
#include "partitions_dual_slot.h"
#elifdef CONFIG_BOARD_PARTITIONS_SINGLE_SLOT
#include "partitions_single_slot.h"
#elifdef CONFIG_BOARD_PARTITIONS_NANO7
#include "partitions_nano7.h"
#endif
""",
    )

    _replace(
        tree / "Kconfig.pebbleos",
        """config BOARD_PARTITIONS_SINGLE_SLOT
    bool
    help
      The board uses the flash partitions of include/pbl/board/partitions_single_slot.h.
""",
        """config BOARD_PARTITIONS_SINGLE_SLOT
    bool
    help
      The board uses the flash partitions of include/pbl/board/partitions_single_slot.h.

config BOARD_PARTITIONS_NANO7
    bool
    help
      The board uses the compact 4 MiB partitions of
      include/pbl/board/partitions_nano7.h.
""",
    )

    _replace(
        tree / "kernel/malloc.c",
        """void pbl_malloc_init(void) {
  extern int _heap_start;
  extern int _heap_end;

  pbl_heap_init(&s_kernel_heap, &_heap_start, &_heap_end, true);
""",
        """void pbl_malloc_init(void) {
#ifdef CONFIG_NANO7
  extern char *_heap_start;
  extern char *_heap_end;

  pbl_heap_init(&s_kernel_heap, _heap_start, _heap_end, true);
#else
  extern int _heap_start;
  extern int _heap_end;

  pbl_heap_init(&s_kernel_heap, &_heap_start, &_heap_end, true);
#endif
""",
    )

    _replace(
        tree / "drivers/display/CMakeLists.txt",
        "pbl_library_sources_ifdef(CONFIG_DISPLAY_SHELL shell.c)\n",
        "pbl_library_sources_ifdef(CONFIG_DISPLAY_SHELL shell.c)\n"
        "pbl_library_sources_ifdef(CONFIG_DISPLAY_NANO nano/display_nano.c)\n",
    )
    _replace(
        tree / "drivers/display/Kconfig",
        'rsource "Kconfig.sdl"\n',
        'rsource "Kconfig.sdl"\nrsource "Kconfig.nano"\n',
    )
    _replace(
        tree / "drivers/flash/CMakeLists.txt",
        "pbl_library_sources_ifdef(CONFIG_FLASH_SHELL shell.c)\n",
        "pbl_library_sources_ifdef(CONFIG_FLASH_SHELL shell.c)\n"
        "pbl_library_sources_ifdef(CONFIG_FLASH_NANO nano.c)\n",
    )
    _replace(
        tree / "drivers/flash/Kconfig",
        'rsource "Kconfig.posix"\n',
        'rsource "Kconfig.posix"\nrsource "Kconfig.nano"\n',
    )
    _replace(
        tree / "drivers/rtc/CMakeLists.txt",
        "pbl_library_sources_ifdef(CONFIG_RTC_POSIX posix.c)\n",
        "pbl_library_sources_ifdef(CONFIG_RTC_POSIX posix.c)\n"
        "pbl_library_sources_ifdef(CONFIG_RTC_NANO nano.c)\n",
    )
    _replace(
        tree / "drivers/rtc/Kconfig",
        'rsource "Kconfig.posix"\n',
        'rsource "Kconfig.posix"\nrsource "Kconfig.nano"\n',
    )
    _replace(
        tree / "drivers/input/CMakeLists.txt",
        "pbl_library_sources_ifdef(CONFIG_INPUT_SDL sdl/input_sdl.c)\n",
        "pbl_library_sources_ifdef(CONFIG_INPUT_SDL sdl/input_sdl.c)\n"
        "pbl_library_sources_ifdef(CONFIG_INPUT_NANO nano/input_nano.c)\n",
    )
    _replace(
        tree / "drivers/input/Kconfig",
        'rsource "Kconfig.sdl"\n',
        'rsource "Kconfig.sdl"\nrsource "Kconfig.nano"\n',
    )
    _replace(
        tree / "drivers/timer/CMakeLists.txt",
        "pbl_library_sources_ifdef(CONFIG_TIMER_POSIX posix.c)\n",
        "if(CONFIG_NANO7)\n"
        "  pbl_library_sources(nano.c)\n"
        "else()\n"
        "  pbl_library_sources_ifdef(CONFIG_TIMER_POSIX posix.c)\n"
        "endif()\n",
    )
    _replace(
        tree / "drivers/rng/CMakeLists.txt",
        "pbl_library_sources_ifdef(CONFIG_RNG_POSIX posix.c)\n",
        "pbl_library_sources_ifdef(CONFIG_RNG_POSIX posix.c)\n"
        "pbl_library_sources_ifdef(CONFIG_RNG_NANO nano.c)\n",
    )
    rng_k = tree / "drivers/rng/Kconfig"
    text = rng_k.read_text()
    if 'rsource "Kconfig.nano"' not in text:
        # append a rsource next to posix if present, else at end of sourced files
        if 'rsource "Kconfig.posix"' in text:
            rng_k.write_text(text.replace(
                'rsource "Kconfig.posix"\n',
                'rsource "Kconfig.posix"\nrsource "Kconfig.nano"\n',
                1,
            ))
        else:
            rng_k.write_text(text + '\nrsource "Kconfig.nano"\n')


    # First assignment of these symbols is in prj.conf / prj_normal.conf.
    # Setting them again in the board file is a fatal Kconfig warning.
    prj = tree / "pebble/prj.conf"
    prj_text = prj.read_text()
    for sym in (
        "CONFIG_BT=y",
        "CONFIG_MIC=y",
        "CONFIG_CRC_HW=y",
        "CONFIG_LOG_BACKEND_FLASH=y",
        "CONFIG_SERVICE_BLUETOOTH=y",
        "CONFIG_SERVICE_LOG_DUMP_ENDPOINT=y",
        "CONFIG_BT_PRF_STORE=y",
    ):
        if sym not in prj_text:
            raise SystemExit(f"{prj}: missing {sym}")
        prj_text = prj_text.replace(sym, sym[:-1] + "n", 1)
    prj.write_text(prj_text)

    prjn = tree / "pebble/prj_normal.conf"
    prjn_text = prjn.read_text()
    for sym in (
        "CONFIG_SERVICE_BLUETOOTH_ADV_RECONNECT=y",
        "CONFIG_NIMBLE_STORE_SETTINGS_PRF_MIRROR=y",
    ):
        if sym not in prjn_text:
            raise SystemExit(f"{prjn}: missing {sym}")
        prjn_text = prjn_text.replace(sym, sym[:-1] + "n", 1)
    prjn.write_text(prjn_text)


    main = tree / "pebble/main.c"
    _replace(main,
        "#include <pbl/logging/backend_flash.h>\n",
        "#ifdef CONFIG_LOG_BACKEND_FLASH\n#include <pbl/logging/backend_flash.h>\n#endif\n",
    )
    _replace(main,
        "#include <nimble/nimble_port_pebble.h>\n",
        "#ifdef CONFIG_BT\n#include <nimble/nimble_port_pebble.h>\n#endif\n",
    )
    _replace(main,
        """  pbl_bt_init();
  pebble_task_register(PebbleTask_BTHost, nimble_port_pebble_host_thread());
#if NIMBLE_CFG_CONTROLLER
  pebble_task_register(PebbleTask_BTController, nimble_port_pebble_ll_thread());
#endif
#if defined(CONFIG_NIMBLE_HCI_SF32LB) || defined(CONFIG_NIMBLE_HCI_UART)
  pebble_task_register(PebbleTask_BTHCI, nimble_port_pebble_hci_thread());
#endif
""",
        """#ifdef CONFIG_BT
  pbl_bt_init();
  pebble_task_register(PebbleTask_BTHost, nimble_port_pebble_host_thread());
#if NIMBLE_CFG_CONTROLLER
  pebble_task_register(PebbleTask_BTController, nimble_port_pebble_ll_thread());
#endif
#if defined(CONFIG_NIMBLE_HCI_SF32LB) || defined(CONFIG_NIMBLE_HCI_UART)
  pebble_task_register(PebbleTask_BTHCI, nimble_port_pebble_hci_thread());
#endif
#endif
""",
    )


    svc_bt = tree / "pebble/services/bluetooth/Kconfig"
    svc_text = svc_bt.read_text()
    svc_text = svc_text.replace(
        "    default y if !RECOVERY_FW\n",
        "    default y if SERVICE_BLUETOOTH && !RECOVERY_FW\n",
    )
    svc_text = svc_text.replace(
        "config BT_SERVICE_PPS\n    default y\n",
        "config BT_SERVICE_PPS\n    default y if SERVICE_BLUETOOTH\n",
    )
    svc_text = svc_text.replace(
        "config PP_TRANSPORT_GATT_REVERSED\n    default y\n",
        "config PP_TRANSPORT_GATT_REVERSED\n    default y if SERVICE_BLUETOOTH\n",
    )
    svc_bt.write_text(svc_text)


    _replace(
        tree / "include/pbl/mcu/interrupts.h",
        """#ifdef __arm__
#include <pbl/mcu/interrupts_arm.inl.h>
#else
#include <pbl/mcu/interrupts_stubs.inl.h>
#endif
""",
        """#if defined(__arm__) && !defined(CONFIG_ARCH_POSIX)
#include <pbl/mcu/interrupts_arm.inl.h>
#else
#include <pbl/mcu/interrupts_stubs.inl.h>
#endif
""",
    )

    faithful = tree / "pebble/services/blob_db/faithful.c"
    if faithful.exists():
        ft = faithful.read_text()
        if "#ifdef CONFIG_BT" not in ft and "#include <pbl/bluetooth/bond.h>" in ft:
            ft = ft.replace(
                "#include <pbl/bluetooth/bond.h>\n",
                "#ifdef CONFIG_BT\n#include <pbl/bluetooth/bond.h>\n#endif\n",
            )
            ft = ft.replace(
                "PBL_BT_BOND_LISTENER_DEFINE(blob_db_faithful, .added = prv_bond_added);",
                "#ifdef CONFIG_BT\nPBL_BT_BOND_LISTENER_DEFINE(blob_db_faithful, .added = prv_bond_added);\n#endif",
            )
            # the bond-added helper also uses pbl_bt types; wrap it too if still present
            if "static void prv_bond_added" in ft and "#ifdef CONFIG_BT\n// Only one bond" not in ft and "#ifdef CONFIG_BT\nstatic void prv_bond_added" not in ft:
                ft = ft.replace(
                    "static void prv_bond_added(const struct pbl_bt_bond_info *info) {",
                    "#ifdef CONFIG_BT\nstatic void prv_bond_added(const struct pbl_bt_bond_info *info) {",
                )
                # if we added a second ifdef around the function, close it before the listener ifdef
            faithful.write_text(ft)

    _replace(
        tree / "pebble/syscall/syscall.c",
        """#ifdef __arm__
  __asm volatile("mrs %0, ipsr" : "=r"(ipsr));
#endif
""",
        """#if defined(__arm__) && !defined(CONFIG_ARCH_POSIX)
  __asm volatile("mrs %0, ipsr" : "=r"(ipsr));
#endif
""",
    )

    print(f"Applied nano7_emery overlay onto {tree}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: apply_overlay.py PEBBLEOS_TREE")
    apply(Path(sys.argv[1]))
