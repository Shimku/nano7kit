/* jni.c: fake JNIEnv for Angry Birds 1.0.0 Ka3D (nano7kit, MIT).
 * About 20 slots are used: FindClass / GetMethodID / Call* / strings / byte arrays. */
#include "jni.h"
#include "rt_plat.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define JNI_SLOTS 240
#define MAG_STR  0x4a535452u
#define MAG_ARR  0x4a415252u
#define MAG_OBJ  0x4a4f424au
#define MID_READFILE 1
#define MID_ISSILENT 2
#define MID_OPENURL 3
#define MID_AUDIO_INIT 4
#define MID_START_OUT 5
#define MID_STOP_OUT 6
#define MID_SET_VOL 7
#define MID_GETINSTANCE 8
#define MID_FLURRY_START 9
#define MID_FLURRY_END 10
#define MID_FLURRY_EVENT 11
#define MID_HASH_INIT 12
#define MID_HASH_PUT 13
#define MID_OTHER 100

typedef struct { uint32_t mag; char utf[1]; } jstr;
typedef struct { uint32_t mag; jsize len; jbyte data[1]; } jarr;
typedef struct { uint32_t mag; char name[64]; } jobj;

static void *jni_fn[JNI_SLOTS];
static JNIEnv env_obj;
static jobj renderer_obj, app_obj, dummy_class;
static char *last_utf;

static int is_str(jobject o) { return o && ((jstr *)o)->mag == MAG_STR; }
static int is_arr(jobject o) { return o && ((jarr *)o)->mag == MAG_ARR; }

static jint J_GetVersion(JNIEnv *e) { (void)e; return 0x00010006; }
static jclass J_FindClass(JNIEnv *e, const char *name) {
    (void)e;
    if (!name) return 0;
    /* Any class the engine names is a dummy; methods are dispatched by name. */
    return (jclass)&dummy_class;
}
static jthrowable J_ExceptionOccurred(JNIEnv *e) { (void)e; return 0; }
static void J_ExceptionDescribe(JNIEnv *e) { (void)e; }
static void J_ExceptionClear(JNIEnv *e) { (void)e; }
static void J_FatalError(JNIEnv *e, const char *m) { (void)e; plat_fatal(m ? m : "jni fatal"); }
static jint J_PushLocalFrame(JNIEnv *e, jint n) { (void)e; (void)n; return JNI_OK; }
static jobject J_PopLocalFrame(JNIEnv *e, jobject r) { (void)e; return r; }
static jobject J_NewGlobalRef(JNIEnv *e, jobject o) { (void)e; return o; }
static void jni_drop(jobject o) {
    uint32_t mag;
    if (!o) return;
    if (o == (jobject)&renderer_obj || o == (jobject)&app_obj || o == (jobject)&dummy_class)
        return;
    mag = *(uint32_t *)o;
    if (mag != MAG_ARR && mag != MAG_STR && mag != MAG_OBJ) return;
    *(uint32_t *)o = 0;
    free(o);
}
static void J_DeleteGlobalRef(JNIEnv *e, jobject o) { (void)e; jni_drop(o); }
static void J_DeleteLocalRef(JNIEnv *e, jobject o) { (void)e; jni_drop(o); }
static jboolean J_IsSameObject(JNIEnv *e, jobject a, jobject b) { (void)e; return a == b; }
static jobject J_NewLocalRef(JNIEnv *e, jobject o) { (void)e; return o; }
static jint J_EnsureLocalCapacity(JNIEnv *e, jint n) { (void)e; (void)n; return JNI_OK; }
static jobject dummy_obj(const char *n) {
    jobj *o = calloc(1, sizeof *o);
    if (o) {
        o->mag = MAG_OBJ;
        strncpy(o->name, n ? n : "obj", sizeof o->name - 1);
    }
    return o;
}
static jobject J_AllocObject(JNIEnv *e, jclass c) { (void)e; (void)c; return dummy_obj("alloc"); }
static jclass J_GetObjectClass(JNIEnv *e, jobject o) { (void)e; (void)o; return (jclass)&dummy_class; }
static jboolean J_IsInstanceOf(JNIEnv *e, jobject o, jclass c) { (void)e; (void)o; (void)c; return JNI_TRUE; }

static jmethodID J_GetMethodID(JNIEnv *e, jclass c, const char *name, const char *sig) {
    (void)e; (void)c; (void)sig;
    if (!name) return (jmethodID)MID_OTHER;
    if (strcmp(name, "readFile") == 0) return (jmethodID)MID_READFILE;
    if (strcmp(name, "isSilentProfile") == 0) return (jmethodID)MID_ISSILENT;
    if (strcmp(name, "openURL") == 0) return (jmethodID)MID_OPENURL;
    if (strcmp(name, "<init>") == 0) {
        if (sig && strstr(sig, "JIIII")) return (jmethodID)MID_AUDIO_INIT;
        if (sig && strstr(sig, "Object;Ljava/lang/Object")) return (jmethodID)MID_HASH_INIT;
        return (jmethodID)MID_OTHER;
    }
    if (strcmp(name, "startOutput") == 0) return (jmethodID)MID_START_OUT;
    if (strcmp(name, "stopOutput") == 0) return (jmethodID)MID_STOP_OUT;
    if (strcmp(name, "setMasterVolume") == 0) return (jmethodID)MID_SET_VOL;
    if (strcmp(name, "put") == 0) return (jmethodID)MID_HASH_PUT;
    return (jmethodID)MID_OTHER;
}
static jmethodID J_GetStaticMethodID(JNIEnv *e, jclass c, const char *name, const char *sig) {
    (void)e; (void)c; (void)sig;
    if (!name) return (jmethodID)MID_OTHER;
    if (strcmp(name, "getInstance") == 0) return (jmethodID)MID_GETINSTANCE;
    if (strcmp(name, "onStartSession") == 0) return (jmethodID)MID_FLURRY_START;
    if (strcmp(name, "onEndSession") == 0) return (jmethodID)MID_FLURRY_END;
    if (strcmp(name, "onEvent") == 0) return (jmethodID)MID_FLURRY_EVENT;
    return (jmethodID)MID_OTHER;
}

static jstring J_NewStringUTF(JNIEnv *e, const char *s) {
    jstr *o;
    size_t n;
    (void)e;
    if (!s) s = "";
    n = strlen(s);
    o = calloc(1, sizeof(jstr) + n);
    if (!o) return 0;
    o->mag = MAG_STR;
    memcpy(o->utf, s, n + 1);
    return (jstring)o;
}
static const char *J_GetStringUTFChars(JNIEnv *e, jstring s, jboolean *copy) {
    (void)e;
    if (copy) *copy = JNI_FALSE;
    if (!is_str(s)) return "";
    return ((jstr *)s)->utf;
}
static void J_ReleaseStringUTFChars(JNIEnv *e, jstring s, const char *c) { (void)e; (void)s; (void)c; }
static jsize J_GetStringUTFLength(JNIEnv *e, jstring s) {
    (void)e;
    return is_str(s) ? (jsize)strlen(((jstr *)s)->utf) : 0;
}
static jsize J_GetStringLength(JNIEnv *e, jstring s) { return J_GetStringUTFLength(e, s); }
static const jchar *J_GetStringChars(JNIEnv *e, jstring s, jboolean *copy) {
    (void)e; (void)s;
    if (copy) *copy = JNI_FALSE;
    return (const jchar *)"";
}
static void J_ReleaseStringChars(JNIEnv *e, jstring s, const jchar *c) { (void)e; (void)s; (void)c; }
static jstring J_NewString(JNIEnv *e, const jchar *u, jsize n) { (void)u; (void)n; return J_NewStringUTF(e, ""); }

static jbyteArray J_NewByteArray(JNIEnv *e, jsize n) {
    jarr *a;
    (void)e;
    if (n < 0) n = 0;
    a = calloc(1, sizeof(jarr) + (size_t)n);
    if (!a) return 0;
    a->mag = MAG_ARR;
    a->len = n;
    return (jbyteArray)a;
}
static jsize J_GetArrayLength(JNIEnv *e, jarray a) { (void)e; return is_arr(a) ? ((jarr *)a)->len : 0; }
static jbyte *J_GetByteArrayElements(JNIEnv *e, jbyteArray a, jboolean *copy) {
    (void)e;
    if (copy) *copy = JNI_FALSE;
    return is_arr(a) ? ((jarr *)a)->data : 0;
}
static void J_ReleaseByteArrayElements(JNIEnv *e, jbyteArray a, jbyte *p, jint mode) {
    (void)e; (void)a; (void)p; (void)mode;
}
static void J_GetByteArrayRegion(JNIEnv *e, jbyteArray a, jsize start, jsize len, jbyte *buf) {
    (void)e;
    if (!is_arr(a) || !buf || start < 0) return;
    if (start + len > ((jarr *)a)->len) len = ((jarr *)a)->len - start;
    if (len > 0) memcpy(buf, ((jarr *)a)->data + start, (size_t)len);
}
static void J_SetByteArrayRegion(JNIEnv *e, jbyteArray a, jsize start, jsize len, const jbyte *buf) {
    (void)e;
    if (!is_arr(a) || !buf || start < 0) return;
    if (start + len > ((jarr *)a)->len) len = ((jarr *)a)->len - start;
    if (len > 0) memcpy(((jarr *)a)->data + start, buf, (size_t)len);
}

static jobject read_file(jstring name) {
    const char *s = is_str(name) ? ((jstr *)name)->utf : "";
    void *buf = 0;
    uint32_t n = 0;
    jbyteArray a;
    char log[192];
    snprintf(log, sizeof log, "readFile begin '%s'", s);
    plat_log_line(log);
    plat_log_flush();
    if (plat_read_file(s, &buf, &n)) {
        snprintf(log, sizeof log, "readFile miss '%s'", s);
        plat_log_line(log);
        plat_log_flush();
        return 0;
    }
    {
        size_t sl = strlen(s);
        int wav = sl > 4 && (s[sl - 4] == '.' && (s[sl - 3] == 'w' || s[sl - 3] == 'W'));
        if (!wav) {
            snprintf(log, sizeof log, "readFile '%s' %u bytes %02x %02x %02x %02x",
                     s, n,
                     n > 0 ? ((unsigned char *)buf)[0] : 0,
                     n > 1 ? ((unsigned char *)buf)[1] : 0,
                     n > 2 ? ((unsigned char *)buf)[2] : 0,
                     n > 3 ? ((unsigned char *)buf)[3] : 0);
            plat_log_line(log);
            plat_log_flush();
        }
    }
    a = J_NewByteArray(0, (jsize)n);
    if (!a) {
        snprintf(log, sizeof log, "readFile '%s' %u bytes: NewByteArray NULL", s, n);
        plat_log_line(log);
        plat_log_flush();
        free(buf);
        return 0;
    }
    if (n) memcpy(((jarr *)a)->data, buf, n);
    free(buf);
    return a;
}

static jobject J_CallObjectMethod(JNIEnv *e, jobject obj, jmethodID mid, ...) {
    va_list ap;
    int id = (int)(uintptr_t)mid;
    (void)e; (void)obj;
    va_start(ap, mid);
    if (id == MID_READFILE) {
        jstring name = va_arg(ap, jstring);
        va_end(ap);
        return read_file(name);
    }
    if (id == MID_HASH_PUT) { va_end(ap); return obj; }
    va_end(ap);
    return 0;
}
static jobject J_CallObjectMethodV(JNIEnv *e, jobject obj, jmethodID mid, va_list ap) {
    int id = (int)(uintptr_t)mid;
    (void)e; (void)obj;
    if (id == MID_READFILE) return read_file(va_arg(ap, jstring));
    return 0;
}
/* FileInputStream uses CallObjectMethodV. Slot 36 is CallObjectMethodA (jvalue*). */
static jobject J_CallObjectMethodA(JNIEnv *e, jobject obj, jmethodID mid, const void *args) {
    int id = (int)(uintptr_t)mid;
    jobject name = args ? *(jobject *)args : 0;
    (void)e; (void)obj;
    if (id == MID_READFILE) return read_file(name);
    return 0;
}
static jboolean J_CallBooleanMethod(JNIEnv *e, jobject obj, jmethodID mid, ...) {
    (void)e; (void)obj; (void)mid;
    return JNI_TRUE;   /* isSilentProfile */
}
static jboolean J_CallBooleanMethodV(JNIEnv *e, jobject obj, jmethodID mid, va_list ap) {
    (void)e; (void)obj; (void)mid; (void)ap;
    return JNI_TRUE;
}
static void J_CallVoidMethod(JNIEnv *e, jobject obj, jmethodID mid, ...) { (void)e; (void)obj; (void)mid; }
static void J_CallVoidMethodV(JNIEnv *e, jobject obj, jmethodID mid, va_list ap) { (void)e; (void)obj; (void)mid; (void)ap; }
static jobject J_NewObject(JNIEnv *e, jclass c, jmethodID mid, ...) {
    (void)e; (void)c;
    if ((int)(uintptr_t)mid == MID_HASH_INIT) return dummy_obj("HashMap");
    if ((int)(uintptr_t)mid == MID_AUDIO_INIT) return dummy_obj("AudioOutput");
    return dummy_obj("obj");
}
static jobject J_NewObjectV(JNIEnv *e, jclass c, jmethodID mid, va_list ap) {
    (void)ap;
    return J_NewObject(e, c, mid);
}
static jobject J_CallStaticObjectMethod(JNIEnv *e, jclass c, jmethodID mid, ...) {
    (void)e; (void)c;
    if ((int)(uintptr_t)mid == MID_GETINSTANCE) return (jobject)&app_obj;
    return 0;
}
static jobject J_CallStaticObjectMethodV(JNIEnv *e, jclass c, jmethodID mid, va_list ap) {
    (void)ap;
    return J_CallStaticObjectMethod(e, c, mid);
}
static void J_CallStaticVoidMethod(JNIEnv *e, jclass c, jmethodID mid, ...) { (void)e; (void)c; (void)mid; }
static void J_CallStaticVoidMethodV(JNIEnv *e, jclass c, jmethodID mid, va_list ap) { (void)e; (void)c; (void)mid; (void)ap; }
static jboolean J_ExceptionCheck(JNIEnv *e) { (void)e; return JNI_FALSE; }
static jint J_GetJavaVM(JNIEnv *e, void **vm) { (void)e; if (vm) *vm = 0; return 0; }
static jfieldID J_GetFieldID(JNIEnv *e, jclass c, const char *n, const char *s) {
    (void)e; (void)c; (void)n; (void)s; return (jfieldID)1;
}
static jfieldID J_GetStaticFieldID(JNIEnv *e, jclass c, const char *n, const char *s) {
    (void)e; (void)c; (void)n; (void)s; return (jfieldID)1;
}
static jobject J_GetObjectField(JNIEnv *e, jobject o, jfieldID f) { (void)e; (void)f; return o; }
static jint J_GetIntField(JNIEnv *e, jobject o, jfieldID f) { (void)e; (void)o; (void)f; return 0; }
static jboolean J_GetBooleanField(JNIEnv *e, jobject o, jfieldID f) { (void)e; (void)o; (void)f; return JNI_FALSE; }
static void J_SetObjectField(JNIEnv *e, jobject o, jfieldID f, jobject v) { (void)e; (void)o; (void)f; (void)v; }
static void J_SetIntField(JNIEnv *e, jobject o, jfieldID f, jint v) { (void)e; (void)o; (void)f; (void)v; }
static jint J_CallIntMethod(JNIEnv *e, jobject o, jmethodID m, ...) { (void)e; (void)o; (void)m; return 0; }
static jint J_CallIntMethodV(JNIEnv *e, jobject o, jmethodID m, va_list ap) { (void)e; (void)o; (void)m; (void)ap; return 0; }
static void *stub(void) { return 0; }

static void fill_table(void) {
    int i;
    for (i = 0; i < JNI_SLOTS; i++) jni_fn[i] = (void *)stub;
    jni_fn[4] = (void *)J_GetVersion;
    jni_fn[6] = (void *)J_FindClass;
    jni_fn[15] = (void *)J_ExceptionOccurred;
    jni_fn[16] = (void *)J_ExceptionDescribe;
    jni_fn[17] = (void *)J_ExceptionClear;
    jni_fn[18] = (void *)J_FatalError;
    jni_fn[19] = (void *)J_PushLocalFrame;
    jni_fn[20] = (void *)J_PopLocalFrame;
    jni_fn[21] = (void *)J_NewGlobalRef;
    jni_fn[22] = (void *)J_DeleteGlobalRef;
    jni_fn[23] = (void *)J_DeleteLocalRef;
    jni_fn[24] = (void *)J_IsSameObject;
    jni_fn[25] = (void *)J_NewLocalRef;
    jni_fn[26] = (void *)J_EnsureLocalCapacity;
    jni_fn[27] = (void *)J_AllocObject;
    jni_fn[28] = (void *)J_NewObject;
    jni_fn[29] = (void *)J_NewObjectV;
    jni_fn[30] = (void *)J_NewObjectV;
    jni_fn[31] = (void *)J_GetObjectClass;
    jni_fn[32] = (void *)J_IsInstanceOf;
    jni_fn[33] = (void *)J_GetMethodID;
    jni_fn[34] = (void *)J_CallObjectMethod;
    jni_fn[35] = (void *)J_CallObjectMethodV;
    jni_fn[36] = (void *)J_CallObjectMethodA;
    jni_fn[37] = (void *)J_CallBooleanMethod;
    jni_fn[38] = (void *)J_CallBooleanMethodV;
    jni_fn[49] = (void *)J_CallIntMethod;
    jni_fn[50] = (void *)J_CallIntMethodV;
    jni_fn[61] = (void *)J_CallVoidMethod;     /* CallVoidMethod */
    jni_fn[62] = (void *)J_CallVoidMethodV;
    jni_fn[94] = (void *)J_GetFieldID;
    jni_fn[95] = (void *)J_GetObjectField;
    jni_fn[96] = (void *)J_GetBooleanField;
    jni_fn[100] = (void *)J_GetIntField;
    jni_fn[104] = (void *)J_SetObjectField;
    jni_fn[109] = (void *)J_SetIntField;
    jni_fn[113] = (void *)J_GetStaticMethodID;
    jni_fn[114] = (void *)J_CallStaticObjectMethod;
    jni_fn[115] = (void *)J_CallStaticObjectMethodV;
    jni_fn[141] = (void *)J_CallStaticVoidMethod;  /* CallStaticVoidMethod */
    jni_fn[142] = (void *)J_CallStaticVoidMethodV;
    jni_fn[144] = (void *)J_GetStaticFieldID;
    jni_fn[163] = (void *)J_NewString;
    jni_fn[164] = (void *)J_GetStringLength;
    jni_fn[165] = (void *)J_GetStringChars;
    jni_fn[166] = (void *)J_ReleaseStringChars;
    jni_fn[167] = (void *)J_NewStringUTF;
    jni_fn[168] = (void *)J_GetStringUTFLength;
    jni_fn[169] = (void *)J_GetStringUTFChars;
    jni_fn[170] = (void *)J_ReleaseStringUTFChars;
    jni_fn[171] = (void *)J_GetArrayLength;
    /* Standard JNINativeInterface indices (verified against libangrybirds.so:
     * GetStringUTFChars is ldr pc,[r3,#676] = slot 169; GetByteArrayRegion is
     * ldr pc,[ip,#800] = slot 200 in FileInputStream). */
    jni_fn[176] = (void *)J_NewByteArray;
    jni_fn[184] = (void *)J_GetByteArrayElements;
    jni_fn[192] = (void *)J_ReleaseByteArrayElements;
    jni_fn[200] = (void *)J_GetByteArrayRegion;
    jni_fn[208] = (void *)J_SetByteArrayRegion;
    jni_fn[219] = (void *)J_GetJavaVM;
    jni_fn[228] = (void *)J_ExceptionCheck;
}

JNIEnv *ab_jni_env(void) {
    if (!env_obj.functions) {
        fill_table();
        env_obj.functions = (const struct JNINativeInterface *)jni_fn;
        dummy_class.mag = MAG_OBJ;
        strncpy(dummy_class.name, "Class", sizeof dummy_class.name - 1);
        renderer_obj.mag = MAG_OBJ;
        strncpy(renderer_obj.name, "MyRenderer", sizeof renderer_obj.name - 1);
        app_obj.mag = MAG_OBJ;
        strncpy(app_obj.name, "App", sizeof app_obj.name - 1);
    }
    return &env_obj;
}
jobject ab_jni_renderer(void) { ab_jni_env(); return (jobject)&renderer_obj; }
jstring ab_jni_new_utf(const char *s) { return J_NewStringUTF(ab_jni_env(), s); }
void ab_jni_reset(void) { last_utf = 0; }
