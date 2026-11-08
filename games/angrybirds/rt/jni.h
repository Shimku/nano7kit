/* jni.h: tiny JNIEnv for Ka3D's Java callbacks (nano7kit, MIT). */
#ifndef AB_JNI_H
#define AB_JNI_H
#include <stdint.h>
typedef uint8_t jboolean;
typedef int8_t jbyte;
typedef uint16_t jchar;
typedef int16_t jshort;
typedef int32_t jint;
typedef int64_t jlong;
typedef float jfloat;
typedef double jdouble;
typedef jint jsize;
typedef void *jobject;
typedef jobject jclass;
typedef jobject jstring;
typedef jobject jarray;
typedef jarray jbyteArray;
typedef jobject jthrowable;
typedef void *jmethodID;
typedef void *jfieldID;
typedef jobject jweak;
#define JNI_FALSE 0
#define JNI_TRUE 1
#define JNI_OK 0
#define JNI_ABORT 2
struct JNINativeInterface;
typedef const struct JNINativeInterface *JNINativeInterfacePtr;
typedef struct JNIEnv_ {
    const struct JNINativeInterface *functions;
} JNIEnv;
JNIEnv *ab_jni_env(void);
jobject ab_jni_renderer(void);
jstring ab_jni_new_utf(const char *s);
void ab_jni_reset(void);
#endif
