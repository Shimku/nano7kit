@ fn_jmp.S: setjmp/longjmp for the nano (nano7kit, MIT). ARM mode;
@ newlib's setjmp is Thumb and saves d0-d31; the loader refuses Thumb VFP.
@ Ka3D SjLj (recomp C calling setjmp) and the frame-loop unwind share this.
@ buffer: r4-r11, sp, lr (10 words), then d8-d15 (16 words). jmp_buf is 160 bytes.
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
    .global setjmp
    .set setjmp, fn_setjmp
    .global longjmp
    .set longjmp, fn_longjmp
    .global _setjmp
    .set _setjmp, fn_setjmp
    .global _longjmp
    .set _longjmp, fn_longjmp
