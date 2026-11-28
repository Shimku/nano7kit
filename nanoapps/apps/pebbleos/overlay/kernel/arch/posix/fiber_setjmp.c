/* SPDX-FileCopyrightText: 2026 Nikita Shimkus */
/* SPDX-License-Identifier: Apache-2.0 */

/* Single-thread fiber backend for a hosted NanoApps build. Kernel threads
 * share the NanoApps thread; pbl_posix_run() runs until idle and returns
 * to the frame loop. Stacks are the ones the kernel already allocated
 * (Pebble sizes, not the 128 KiB Emscripten fibers).
 *
 * Integer setjmp only (soft-float ABI): no VFP registers, no libc jmp_buf.
 */

#include <stdint.h>
#include <string.h>

#include <kernel.h>
#include <pbl/kernel/time.h>
#include <pbl_arch_posix.h>
#include <pbl/nano_hal.h>

#define NANO_JMP_WORDS 10
#define BOOT_STACK_SIZE 16384
#define HOST_TURN_US 20000

struct nano_jmp {
  uint32_t r[NANO_JMP_WORDS];
};

struct posix_thread {
  struct nano_jmp env;
  void (*entry)(void *);
  void *arg;
  bool live;
};

struct pbl_thread *pbl_cur;

void arch_cur_set(struct pbl_thread *t) {
  pbl_cur = t;
}

static bool s_switch_pending;
static bool s_in_isr;
static unsigned int s_irq_locked;
static int (*s_entry)(void);
static struct nano_jmp s_host_env;
static struct nano_jmp *s_resume;
static uint64_t s_host_turn_us;
static bool s_started;
static bool s_allow_host_yield;

static struct posix_thread s_boot_pt;
static uint8_t s_boot_stack[BOOT_STACK_SIZE] __attribute__((aligned(8)));

/* r0=jmp_buf, returns 0. Saves r4-r11, sp, lr. */
__attribute__((naked, returns_twice)) static int nano_setjmp(struct nano_jmp *env) {
  (void)env;
  __asm__ volatile(
      ".syntax unified\n"
      "stmia   r0!, {r4-r11,lr}\n"
      "str     sp, [r0]\n"
      "movs    r0, #0\n"
      "bx      lr\n");
}

/* r0=jmp_buf, r1=val. Restores and returns val (1 if val is 0). */
__attribute__((naked, noreturn)) static void nano_longjmp(struct nano_jmp *env, int val) {
  (void)env;
  (void)val;
  __asm__ volatile(
      ".syntax unified\n"
      "movs    r2, r1\n"
      "bne     1f\n"
      "movs    r2, #1\n"
      "1:\n"
      "ldmia   r0!, {r4-r11,lr}\n"
      "ldr     sp, [r0]\n"
      "mov     r0, r2\n"
      "bx      lr\n");
}

/* r0=stack_top, r1=entry. Sets SP and jumps to entry (no return). */
__attribute__((naked, noreturn)) static void nano_boot_sp(void *stack_top, void (*entry)(void)) {
  (void)stack_top;
  (void)entry;
  __asm__ volatile(
      ".syntax unified\n"
      "mov     r2, r0\n"
      "bic     r2, r2, #7\n"
      "mov     sp, r2\n"
      "bx      r1\n");
}

static struct posix_thread *prv_pt(struct pbl_thread *t) {
  return t->backend.arch.pt;
}

static void prv_switch(void) {
  s_switch_pending = false;
  struct pbl_thread *prev = pbl_cur;
  struct pbl_thread *next = sched_next;
  if (next == prev) {
    return;
  }
  arch_cur_set(next);
  struct posix_thread *from = prv_pt(prev);
  struct posix_thread *to = prv_pt(next);
  if (prev->backend.state == PBL_THREAD_DEAD) {
    prev->backend.arch.pt = nullptr;
    from->live = false;
  }
  if (nano_setjmp(&from->env) == 0) {
    nano_longjmp(&to->env, 1);
  }
}

void arch_early_init(void) {
  pbl_cycles_per_sec_set(1000000U);
}

void arch_init(void) {
}

static struct nano_jmp s_init_parent;
static struct posix_thread *s_initing;

static void prv_init_thunk(void) {
  /* The pointer must live on this fiber's stack so it survives the
   * longjmp. s_initing and pbl_cur are not stable: the boot fiber is
   * not a pbl_thread, and later thread creates overwrite s_initing. */
  volatile struct posix_thread *pt = s_initing;
  if (nano_setjmp(&pt->env) == 0) {
    nano_longjmp(&s_init_parent, 1);
  }
  pt->entry((void *)pt->arg);
  pbl_thread_abort(nullptr);
  for (;;) {
  }
}

static void prv_fiber_on_stack(struct posix_thread *pt, void *stack, size_t size) {
  uintptr_t top = ((uintptr_t)stack + size) & ~7U;
  s_initing = pt;
  if (nano_setjmp(&s_init_parent) == 0) {
    nano_boot_sp((void *)top, prv_init_thunk);
  }
}

void arch_thread_init(struct pbl_thread *t, void (*entry)(void *), void *arg) {
  /* Fiber state sits at the low address of the kernel stack. */
  uintptr_t base = (uintptr_t)t->stack;
  base = (base + 7U) & ~7U;
  struct posix_thread *pt = (struct posix_thread *)base;
  memset(pt, 0, sizeof(*pt));
  pt->entry = entry;
  pt->arg = arg;
  pt->live = true;
  t->backend.arch.pt = pt;

  /* Keep the posix_thread at the low address, out of the way of the
   * downward-growing stack. */
  uintptr_t usable = (base + sizeof(*pt) + 16U + 7U) & ~7U;
  uintptr_t top = ((uintptr_t)t->stack + t->stack_size) & ~7U;
  size_t usable_size = (top > usable) ? (size_t)(top - usable) : 0U;
  prv_fiber_on_stack(pt, (void *)usable, usable_size);
}

void arch_thread_regions_set(struct pbl_thread *t, const struct pbl_mpu_region *const *regions) {
  (void)t;
  (void)regions;
}

void arch_start(void) {
  s_irq_locked = 0;
  /* The boot fiber is done; it is never resumed. */
  if (nano_setjmp(&s_boot_pt.env) == 0) {
    nano_longjmp(&pbl_cur->backend.arch.pt->env, 1);
  }
  for (;;) {
  }
}

void arch_switch_request(void) {
  s_switch_pending = true;
}

void arch_thread_exit(void) {
  for (;;) {
  }
}

void arch_thread_aborted(struct pbl_thread *t) {
  if (t->backend.arch.pt != nullptr) {
    t->backend.arch.pt->live = false;
    t->backend.arch.pt = nullptr;
  }
}

bool arch_in_isr(void) {
  return s_in_isr;
}

void arch_heap_lock(void) {
}

void arch_heap_unlock(void) {
}

unsigned int arch_irq_lock(void) {
  unsigned int key = s_irq_locked;
  s_irq_locked = 1;
  return key;
}

bool arch_irq_is_locked(void) {
  return s_irq_locked != 0;
}

void arch_irq_unlock(unsigned int key) {
  s_irq_locked = key;
  if (key != 0 || s_in_isr || !pbl_kernel_is_started()) {
    return;
  }
  extern uint64_t posix_host_monotonic_us(void);
  /* Let the first boot reach idle (a watchface) before yielding
   * back to the host frame loop. Later turns yield every 20 ms. */
  if (s_allow_host_yield && pbl_nano_display_painted() &&
      posix_host_monotonic_us() - s_host_turn_us > HOST_TURN_US) {
    pbl_posix_cpu_wait(0);
  }
  if (s_switch_pending) {
    prv_switch();
  }
}

void arch_thread_saved_regs(const struct pbl_thread *t, struct pbl_thread_saved_regs *regs) {
  (void)t;
  *regs = (struct pbl_thread_saved_regs){};
}

void arch_thread_info_regs(const struct pbl_thread *t, uint32_t regs[PBL_THREAD_REG_COUNT]) {
  (void)t;
  memset(regs, 0, sizeof(uint32_t) * PBL_THREAD_REG_COUNT);
}

void arch_idle(pbl_tick_t max_ticks) {
  s_allow_host_yield = true;
  if (sched_idle_confirm()) {
    idle_enter(max_ticks);
  }
}

static void prv_boot_main(void *arg) {
  (void)arg;
  kernel_init(s_entry);
}

void pbl_posix_start(int (*entry)(void)) {
  s_entry = entry;
}

void pbl_posix_run(void) {
  if (s_entry == nullptr) {
    return;
  }
  if (!s_started) {
    /* Create the boot fiber on the static boot stack. */
    memset(&s_boot_pt, 0, sizeof(s_boot_pt));
    s_boot_pt.entry = prv_boot_main;
    s_boot_pt.arg = nullptr;
    s_boot_pt.live = true;

    prv_fiber_on_stack(&s_boot_pt, s_boot_stack, BOOT_STACK_SIZE);
    s_resume = &s_boot_pt.env;
    s_started = true;
  }
  if (s_resume == nullptr) {
    return;
  }
  extern uint64_t posix_host_monotonic_us(void);
  if (nano_setjmp(&s_host_env) == 0) {
    struct nano_jmp *to = s_resume;
    s_resume = nullptr;
    s_host_turn_us = posix_host_monotonic_us();
    nano_longjmp(to, 1);
  }
}

void pbl_posix_irq_run(void (*isr)(void *), void *arg) {
  s_in_isr = true;
  isr(arg);
  s_in_isr = false;
}

void pbl_posix_cpu_wait(uint64_t timeout_us) {
  (void)timeout_us;
  if (pbl_cur == nullptr || pbl_cur->backend.arch.pt == nullptr) {
    return;
  }
  /* During boot the idle thread can run before the compositor has
   * painted. Stay in the kernel and let timers advance until the
   * display has something to show, then yield to the host. */
  if (!pbl_nano_display_painted()) {
    extern void posix_host_boost_us(uint64_t us);
    posix_host_boost_us(10000);
    pbl_nano_timer_poll();
    if (s_switch_pending) {
      prv_switch();
    }
    return;
  }
  s_resume = &pbl_cur->backend.arch.pt->env;
  if (nano_setjmp(&pbl_cur->backend.arch.pt->env) == 0) {
    nano_longjmp(&s_host_env, 1);
  }
  extern uint64_t posix_host_monotonic_us(void);
  s_host_turn_us = posix_host_monotonic_us();
}
