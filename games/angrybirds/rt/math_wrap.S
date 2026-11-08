@ math_wrap.S: softfp (GPR) wrappers around hard-float libm (nano7kit, MIT).
@ The Android .so is -mfloat-abi=softfp; host libc/libm is gnueabihf (VFP args).
    .syntax unified
    .arm
    .fpu vfpv3-d16
    .text

    .macro W1 name
    .global wrap_\name
    .type wrap_\name, %function
wrap_\name:
    vmov d0, r0, r1
    push {r4, lr}
    bl \name
    pop {r4, lr}
    vmov r0, r1, d0
    bx lr
    .size wrap_\name, .-wrap_\name
    .endm

    .macro W2 name
    .global wrap_\name
    .type wrap_\name, %function
wrap_\name:
    vmov d0, r0, r1
    vmov d1, r2, r3
    push {r4, lr}
    bl \name
    pop {r4, lr}
    vmov r0, r1, d0
    bx lr
    .size wrap_\name, .-wrap_\name
    .endm

    W1 acos
    W1 asin
    W1 atan
    W2 atan2
    W1 ceil
    W1 cos
    W1 cosh
    W1 exp
    W1 floor
    W2 fmod
    W1 log
    W1 log10
    W2 pow
    W1 sin
    W1 sinh
    W1 sqrt
    W1 tan
    W1 tanh

    .global wrap_ldexp
    .type wrap_ldexp, %function
wrap_ldexp:
    vmov d0, r0, r1
    mov r0, r2
    push {r4, lr}
    bl ldexp
    pop {r4, lr}
    vmov r0, r1, d0
    bx lr
    .size wrap_ldexp, .-wrap_ldexp

    .global wrap_frexp
    .type wrap_frexp, %function
wrap_frexp:
    vmov d0, r0, r1
    mov r0, r2
    push {r4, lr}
    bl frexp
    pop {r4, lr}
    vmov r0, r1, d0
    bx lr
    .size wrap_frexp, .-wrap_frexp

    .global wrap_modf
    .type wrap_modf, %function
wrap_modf:
    vmov d0, r0, r1
    mov r0, r2
    push {r4, lr}
    bl modf
    pop {r4, lr}
    vmov r0, r1, d0
    bx lr
    .size wrap_modf, .-wrap_modf
