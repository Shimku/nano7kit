@ pvz_jmp.S: setjmp/longjmp for the PvZ nano frame loop (copied from Fruit Ninja) (nano7kit, LGPL-3.0-or-later). ARM mode;
@ newlib's setjmp pulls in the C++ unwinder, which has Thumb VFP code the loader refuses.
@ buffer: r4-r11, sp, lr (10 words), then d8-d15 (16 words)
    .syntax unified
    .arm
    .fpu vfpv3-d16
    .text
    .global pvz_setjmp
    .type pvz_setjmp, %function
pvz_setjmp:
    stmia r0!, {r4-r11, sp, lr}
    vstmia r0, {d8-d15}
    mov r0, #0
    bx lr
    .size pvz_setjmp, .-pvz_setjmp
    .global pvz_longjmp
    .type pvz_longjmp, %function
pvz_longjmp:
    ldmia r0!, {r4-r11, sp, lr}
    vldmia r0, {d8-d15}
    movs r0, r1
    moveq r0, #1
    bx lr
    .size pvz_longjmp, .-pvz_longjmp
