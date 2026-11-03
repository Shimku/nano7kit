# nano7 PvZ device build (iPod nano 7G): the same sources as the x86 dry run (build_dev.py) with the iPod platform
# (nano7dev/hb) instead of the posix one and the software rasterizer, in ARM state (Thumb VFP traps on this firmware),
# into libpvz_nano.a for nano7kit's games/pvz/nano/app. The app links it with the SDK into PvZ.hbapp.
import subprocess, sys, os, glob, concurrent.futures as cf
R = os.path.dirname(os.path.abspath(__file__))
# paths: the toolchain's bin/ (with the trailing slash, or empty for PATH), nano7kit, zlib 1.3.1 sources, and the
# SDL 2.30.9 include folder with SDL_config_minimal.h as SDL_config.h (headers only: no SDL code is built)
TC = os.environ.get('NANO7_TC', '')
NK = os.environ.get('NANO7KIT', os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
ZLIB = os.environ.get('NANO7_ZLIB', R + '/../pvzp-measure/ext/zlib-1.3.1')
SDLINC = os.environ.get('NANO7_SDLINC', R + '/../pvzp-measure/shim/SDL2')
APP = os.environ.get('NANO7_APP', NK + '/games/pvz_portable/nano')
pats = ['src/*.cpp','src/SexyAppFramework/*.cpp','src/SexyAppFramework/graphics/*.cpp','src/SexyAppFramework/imagelib/*.cpp',
        'src/SexyAppFramework/misc/*.cpp','src/SexyAppFramework/paklib/*.cpp','src/SexyAppFramework/widget/*.cpp',
        'src/PvzpLib/*.cpp','src/Lawn/*.cpp','src/Lawn/Widget/*.cpp','src/Lawn/System/*.cpp',
        'src/SexyAppFramework/platform/default/Input.cpp','nano7/*.cpp','nano7/*.c','nano7dev/*.cpp','nano7dev/*.c',
        'nano7dev/hb/*.c','nano7dev/hb/*.cpp']
skip = {'src/main.cpp', 'nano7/Window_nano7.cpp', 'nano7/NanoSoundManager.cpp', 'nano7/fakegl.c', 'nano7/memtrack.c',
        'nano7/texlog.cpp', 'nano7/bot.cpp', 'nano7dev/n7plat_posix.c', 'nano7dev/n7gl1_soft.c', 'nano7dev/n7host_files.c'}
ZL = ['adler32','compress','crc32','deflate','inffast','inflate','inftrees','trees','uncompr','zutil']
srcs = sorted(s for p in pats for s in glob.glob(os.path.join(R, p)) if os.path.relpath(s, R) not in skip)
srcs += [os.path.join(ZLIB, z + '.c') for z in ZL]
srcs += [APP + '/pvz_nano.c', APP + '/notice.c', APP + '/gl_left.c', APP + '/pvz_jmp.S']
inc = ['-I'+R+'/nano7dev/hb', '-I'+SDLINC, '-I'+R+'/nano7', '-I'+R+'/nano7dev', '-I'+R+'/src', '-I'+R+'/src/SexyAppFramework', '-I'+R+'/src/SexyAppFramework/platform/default',
       '-I'+R+'/src/SexyAppFramework/sound/SDL-Mixer-X/include', '-I'+ZLIB]
sdk = ['-I'+NK+'/nanoapps/sdk', '-I'+APP]
arch = ['-marm', '-mcpu=cortex-a5', '-mfloat-abi=softfp', '-mfpu=vfpv3-d16', '-mword-relocations']
defs = ['-DLOW_MEMORY', '-DNDEBUG', '-DNANO7_NO_SWTRI', '-DNANO7_SMALL_POOLS', '-DNANO7_FLAT_PAK', '-DNANO7_DEVICE', '-D__NANO7__']
# no -Dgetenv=n7_getenv here (libstdc++'s <cstdlib> undefines it); n7cfg.c defines getenv itself instead
common = arch + ['-Os', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections', '-fno-common', '-fno-strict-aliasing',
                 '-fwrapv', '-fno-short-enums', '-w', '-U__INT32_TYPE__', '-D__INT32_TYPE__=int', '-U__UINT32_TYPE__',
                 '-D__UINT32_TYPE__=unsigned int', '-U__INT_LEAST32_TYPE__', '-D__INT_LEAST32_TYPE__=int', '-U__UINT_LEAST32_TYPE__',
                 '-D__UINT_LEAST32_TYPE__=unsigned int'] + defs
# the iPod glue (nano7dev/hb, the app's C files) sees SDK headers, built with the toolchain's default (short) enums
glue = [f for f in common if f != '-fno-short-enums']
cxx = ['-std=gnu++20', '-frtti', '-include', R + '/nano7dev/hb/nano_shim.h']
# the files with try/catch get -fexceptions, the rest not
EXC = set(open(R + '/nano7dev/hb/exceptions.txt').read().split()) | {'src/SexyAppFramework/imagelib/ImageLib.cpp', 'src/Lawn/System/Music.cpp'}
# ARM state with VFP for the per-frame float code; everything else (start-up, menus, dialogs, saves, parsers, zlib,
# the resource tables) in Thumb with software float, about a third smaller. Both are the base AAPCS, so they link
# together; Thumb code never touches VFP registers, which this firmware traps.
HOT = {'src/Lawn/Board.cpp', 'src/Lawn/Zombie.cpp', 'src/Lawn/Plant.cpp', 'src/Lawn/Projectile.cpp', 'src/Lawn/Coin.cpp',
       'src/Lawn/GridItem.cpp', 'src/Lawn/LawnCommon.cpp', 'src/Lawn/Challenge.cpp', 'src/Lawn/CutScene.cpp',
       'src/Lawn/CursorObject.cpp', 'src/Lawn/SeedPacket.cpp', 'src/Lawn/LawnMower.cpp', 'src/Lawn/MessageWidget.cpp',
       'src/Lawn/ToolTipWidget.cpp', 'src/LawnApp.cpp', 'src/Lawn/Widget/GameButton.cpp',
       'src/PvzpLib/Reanimator.cpp', 'src/PvzpLib/PvzpParticle.cpp', 'src/PvzpLib/Trail.cpp', 'src/PvzpLib/Attachment.cpp',
       'src/PvzpLib/EffectSystem.cpp', 'src/PvzpLib/FilterEffect.cpp', 'src/PvzpLib/PvzpCommon.cpp',
       'src/SexyAppFramework/graphics/Graphics.cpp', 'src/SexyAppFramework/graphics/GLInterface.cpp',
       'src/SexyAppFramework/graphics/GLImage.cpp', 'src/SexyAppFramework/graphics/MemoryImage.cpp',
       'src/SexyAppFramework/graphics/ImageFont.cpp', 'src/SexyAppFramework/graphics/Font.cpp',
       'src/SexyAppFramework/graphics/Image.cpp', 'src/SexyAppFramework/misc/SexyMatrix.cpp',
       'src/SexyAppFramework/misc/SexyVector.cpp', 'src/SexyAppFramework/SexyAppBase.cpp',
       'src/SexyAppFramework/widget/WidgetManager.cpp', 'src/SexyAppFramework/widget/Widget.cpp',
       'src/SexyAppFramework/widget/WidgetContainer.cpp', 'nano7dev/n7glcommon.c', 'nano7dev/n7engine.cpp',
       'nano7dev/n7main.cpp', 'nano7dev/n7snd.cpp', 'nano7dev/n7alloc.c',
       'src/Lawn/GameObject.cpp', 'src/SexyAppFramework/graphics/Color.cpp', 'nano7/n7r.cpp', 'nano7/n7bake.cpp', 'nano7/n7fx.cpp'}
if os.environ.get('ALL_ARM'): HOT = None
def isa(rel):
    if HOT is None or rel in HOT or '/hb/' in rel: return arch
    return ['-mthumb', '-mcpu=cortex-a5', '-mfloat-abi=soft', '-mword-relocations']
out = os.path.join(R, 'obj_arm'); os.makedirs(out, exist_ok=True)
dep = max(os.path.getmtime(f) for f in glob.glob(R + '/nano7dev/*.h') + glob.glob(R + '/nano7dev/*.inc') + glob.glob(R + '/nano7/*.h')
          + [__file__])
def swap(fl, rel):
    return isa(rel) + [f for f in fl if f not in arch]
def comp(s):
    rel = os.path.relpath(s, R)
    o = os.path.join(out, rel.replace('/', '_').replace('..', 'up').lstrip('_') + '.o')
    if os.path.exists(o) and os.path.getmtime(o) > max(os.path.getmtime(s), dep) and not os.environ.get('FORCE'):
        return s, 0, '', o
    if s.startswith(APP):
        if s.endswith('.S'): cmd = [TC + 'arm-none-eabi-gcc'] + arch + ['-c', s, '-o', o]
        else: cmd = [TC + 'arm-none-eabi-gcc'] + glue + ['-std=gnu99', '-Wall', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables'] + sdk + ['-c', s, '-o', o]
    elif s.endswith('.c'):
        cmd = [TC + 'arm-none-eabi-gcc'] + swap(glue if '/hb/' in s else common, rel) + ['-std=gnu11'] + inc + (sdk if '/hb/' in s else []) + ['-c', s, '-o', o]
    else:
        ex = ['-fexceptions'] if rel in EXC else ['-fno-exceptions']
        cmd = [TC + 'arm-none-eabi-g++'] + swap(common, rel) + cxx + ex + inc + (sdk if '/hb/' in s else []) + ['-c', s, '-o', o]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return s, r.returncode, r.stderr, o
objs = []; bad = 0
with cf.ThreadPoolExecutor(os.cpu_count()) as ex:
    for s, rc, err, o in ex.map(comp, srcs):
        if rc: bad += 1; print('FAIL', os.path.relpath(s, R)); print('\n'.join([l for l in err.splitlines() if 'error' in l or 'warning' in l][:10]))
        else: objs.append(o)
print('sources', len(srcs), 'failed', bad)
if bad: sys.exit(1)
lib = os.path.join(out, 'libpvz_nano.a')
if os.path.exists(lib): os.remove(lib)
r = subprocess.run([TC + 'arm-none-eabi-ar', 'crs', lib] + objs, capture_output=True, text=True)
print(r.stderr[-2000:]); print('lib', lib, os.path.getsize(lib) if os.path.exists(lib) else 0)
sys.exit(r.returncode)
