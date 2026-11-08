@ cxa_hook.S: log __cxa_throw then resume the SO (nano7kit, MIT).
    .syntax unified
    .arm
    .section .note.GNU-stack,"",%progbits
    .text
    .global ab_cxa_throw_hook
    .type ab_cxa_throw_hook, %function
ab_cxa_throw_hook:
    push {r0, r1, r2, r3, lr}
    ldr r3, ra_ptr
    str lr, [r3]
    bl ab_cxa_throw_log
    pop {r0, r1, r2, r3, lr}
    ldr r3, lit_ptr
    ldr r3, [r3]
    push {r4, r5, fp, lr}
    ldr ip, cont_ptr
    ldr pc, [ip]
lit_ptr:
    .word cxa_throw_lit
cont_ptr:
    .word cxa_throw_cont
ra_ptr:
    .word ab_throw_lr
    .size ab_cxa_throw_hook, .-ab_cxa_throw_hook
