@ fn_jmp.S: setjmp/longjmp for the nano frame loop (nano7kit, MIT). ARM mode;
@ newlib's setjmp pulls in the C++ unwinder, which has Thumb VFP code the loader refuses.
@ buffer: r4-r11, sp, lr (10 words), then d8-d15 (16 words)
    .syntax unified
    .arm
    .fpu vfpv3-d16
    .text
    .global fn_setjmp
    .type fn_setjmp, %function
fn_setjmp:
    stmia r0!, {r4-r11, sp, lr}
    vstmia r0, {d8-d15}
    mov r0, #0
    bx lr
    .size fn_setjmp, .-fn_setjmp
    .global fn_longjmp
    .type fn_longjmp, %function
fn_longjmp:
    ldmia r0!, {r4-r11, sp, lr}
    vldmia r0, {d8-d15}
    movs r0, r1
    moveq r0, #1
    bx lr
    .size fn_longjmp, .-fn_longjmp
