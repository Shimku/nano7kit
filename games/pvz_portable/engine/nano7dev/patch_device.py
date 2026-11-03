#!/usr/bin/env python3
"""nano7 PvZ device patch: adds the NANO7_DEVICE code paths to a PvZ-Portable tree that already has the nano7
measuring edits (patch_desk.py). Every change is inside #ifdef NANO7_DEVICE (or #ifndef around a replaced function),
so the desktop measuring build is unchanged. Idempotent: run it again and nothing happens."""
import os, re, sys
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')
MARK = 'NANO7_DEVICE'
def rd(p): return open(os.path.join(SRC, p), encoding='utf-8').read()
def wr(p, s): open(os.path.join(SRC, p), 'w', encoding='utf-8').write(s)
def once(p, anchor, new, before=False, count=1):
    s = rd(p)
    if new in s: return
    assert s.count(anchor) >= 1, (p, anchor[:60])
    s = s.replace(anchor, (new + anchor) if before else (anchor + new), count)
    wr(p, s)
def func_span(s, start):
    """[start, end) of a function definition starting at `start` (through its closing brace)."""
    i = s.index('{', start); d = 0
    for j in range(i, len(s)):
        if s[j] == '{': d += 1
        elif s[j] == '}':
            d -= 1
            if d == 0: return start, j + 1
    raise ValueError
def wrap_out(p, header):
    """#ifndef NANO7_DEVICE around one function definition (by its first line)."""
    s = rd(p)
    k = s.index(header)
    if s[max(0, k - 30):k].endswith('#ifndef NANO7_DEVICE\n'): return
    a, b = func_span(s, k)
    s = s[:a] + '#ifndef NANO7_DEVICE\n' + s[a:b] + '\n#endif\n' + s[b:]
    wr(p, s)

G = 'SexyAppFramework/graphics/'
# 1 image fields: a texture-only image made from a .n7t header
once(G + 'MemoryImage.h', '\tbool\t\t\t\t\tmPurgeBits;\n',
     '#ifdef NANO7_DEVICE\n\tbool\t\t\t\t\tmN7T = false;\t\t// pixels exist only as a GL texture (.n7t)\n\tuint16_t\t\t\t\tmN7TW = 0, mN7TH = 0;\n\tuint8_t\t\t\t\t\tmN7Fmt = 0;\n#endif\n')
# 2 GetBits on such an image: counted, blank pixels, never a decode
s = rd(G + 'MemoryImage.cpp')
if 'Nano7DevBitsWanted' not in s:
    s = s.replace('uint32_t* MemoryImage::GetBits()\n{', '#ifdef NANO7_DEVICE\nvoid Nano7DevBitsWanted(Sexy::MemoryImage*);\n#define N7_NOT_N7T && !mN7T\n#else\n#define N7_NOT_N7T\n#endif\nuint32_t* MemoryImage::GetBits()\n{', 1)
    k = s.index('uint32_t* MemoryImage::GetBits()')
    a = s.index('\tif (mBits == nullptr)\n\t{\n', k) + len('\tif (mBits == nullptr)\n\t{\n')
    s = s[:a] + '#ifdef NANO7_DEVICE\n\t\tif (mN7T) Nano7DevBitsWanted(this);\n#endif\n' + s[a:]
    old = '\t\t\tif (!mFilePath.empty())\n\t\t\t\t{ MT_TAG(MT_DECODE); aLoadedImage.reset('
    assert old in s
    s = s.replace(old, '\t\t\tif (!mFilePath.empty() N7_NOT_N7T)\n\t\t\t\t{ MT_TAG(MT_DECODE); aLoadedImage.reset(', 1)
    wr(G + 'MemoryImage.cpp', s)
# 3 PurgeBits must not fetch pixels it is about to drop
once(G + 'GLImage.cpp', 'void GLImage::PurgeBits()\n{\n', '#ifdef NANO7_DEVICE\n\tif (mN7T && mBits == nullptr) { mPurgeBits = true; return; }\n#endif\n')
# 4 SexyAppBase: images from .n7t, CPU decodes counted, no sleeping in the frame callback, the inverted main loop
A = 'SexyAppFramework/SexyAppBase.cpp'
once(A, 'Sexy::GLImage* SexyAppBase::GetImage(const std::string& theFileName, bool commitBits)\n{\n\tMT_TAG(MT_PIXELS);\n',
     '#ifdef NANO7_DEVICE\n\tif (GLImage* n = Nano7DevGetImage(this, theFileName)) return n;\n#endif\n')
once(A, '\tif (aLoadedImage == nullptr)\n\t\treturn nullptr;\n\n\tGLImage* anImage = new GLImage(mGLInterface.get());\n',
     '#ifdef NANO7_DEVICE\n\tNano7DevDecoded(aLoadedImage->GetWidth(), aLoadedImage->GetHeight(), theFileName);\n#endif\n')
once(A, 'using namespace Sexy;\n', '#ifdef NANO7_DEVICE\nSexy::GLImage* Nano7DevGetImage(Sexy::SexyAppBase*, const std::string&);\nvoid Nano7DevDecoded(int, int, const std::string&);\n#endif\n')
s = rd(A)
s2 = re.sub(r'#ifndef __EMSCRIPTEN__\n(\s*timespec ts;)', r'#if !defined(__EMSCRIPTEN__) && !defined(NANO7_DEVICE)\n\1', s)
if 'Nano7StartBegin' not in s2:
    s2 += '''
#ifdef NANO7_DEVICE
// nano7 PvZ device: the OS calls the app once per frame, so Start() and DoMainLoop() are split the way the
// emscripten build does it: begin once, then one step per frame callback (no sleeping inside it).
void SexyAppBase::Nano7StartBegin()
{
	if (mShutdown) return;
	StartCursorThread();
	if (mAutoStartLoadingThread) StartLoadingThread();   // single-threaded: loads the start-up resources right here
	uint32_t aStartTime = SDL_GetTicks();
	mRunning = true;
	mStartTick = aStartTime;
	mLastTime = aStartTime;
	mLastUserInputTick = aStartTime;
	mLastTimerTime = aStartTime;
}
// One call per OS frame callback, and exactly one full picture per call: the GL back buffer is not kept between
// callbacks, so every widget is marked dirty, the panel is cleared first, the updates run until the first draw (or
// until the step is done), and if no draw happened one is forced. Never two draws in a callback (each triangle
// costs GL driver memory).
void Nano7FrameBegin();
void Nano7FrameEnd();
bool SexyAppBase::Nano7Step()
{
	if (mShutdown) return false;
	if (mExitToTop) mExitToTop = false;
	if (mWidgetManager) mWidgetManager->MarkAllDirty();
	Nano7FrameBegin();
	uint aDraws = mDrawCount;
	bool updated = false;
	for (int i = 0; i < 64 && mDrawCount == aDraws && !mShutdown; i++)
	{
		if (!UpdateAppStep(&updated)) break;
		if (mUpdateAppState == UPDATESTATE_PROCESS_DONE && !mHasPendingDraw) break;
	}
	if (mDrawCount == aDraws && !mShutdown && mWidgetManager)
	{
		mWidgetManager->MarkAllDirty();
		mNextDrawTick = 0;
		DrawDirtyStuff();
	}
	Nano7FrameEnd();
	return !mShutdown;
}
#endif
'''
wr(A, s2)
once('SexyAppFramework/SexyAppBase.h', '\tvirtual void\t\t\tStart();\n', '#ifdef NANO7_DEVICE\n\tvoid\t\t\t\t\tNano7StartBegin();\n\tbool\t\t\t\t\tNano7Step();\n#endif\n')
# 5 GL: the GLES1 renderer and the .n7t band loader
I = G + 'GLInterface.cpp'
s = rd(I)
if 'n7gl_dev_a.inc' not in s:
    a = s.index('static void GfxApplyState(const GfxDrawState& state)')
    b = s.index('static void GfxEnd()')
    s = s[:a] + '#ifdef NANO7_DEVICE\n#include "../../../nano7dev/n7gl_dev_a.inc"\n#else\n' + s[a:b] + '#endif\n\n' + s[b:]
    s = s.replace('void TextureData::CreateTextures(MemoryImage *theImage)\n{\n',
                  'void TextureData::CreateTextures(MemoryImage *theImage)\n{\n#ifdef NANO7_DEVICE\n\tif (theImage->mN7T && theImage->mBits == nullptr) { Nano7DevCreateTextures(this, theImage); return; }\n#endif\n', 1)
    s += '\n#ifdef NANO7_DEVICE\n#include "../../../nano7dev/n7gl_dev_b.inc"\n#endif\n'
    wr(I, s)
for h in ('void GLInterface::UpdateViewport()', 'int GLInterface::Init(bool IsWindowed)', 'bool GLInterface::PreDraw()',
          'void GLInterface::Flush()', 'bool GLInterface::RecoverBits(MemoryImage* theImage)'):
    wrap_out(I, h)
# 6 reanimation cache drawn live
R = 'Lawn/System/ReanimationLawn.cpp'
for h in ('void ReanimatorCache::DrawCachedPlant(', 'void ReanimatorCache::DrawCachedMower(', 'void ReanimatorCache::DrawCachedZombie('):
    wrap_out(R, h)
s = rd(R)
if 'n7cache_dev.inc' not in s:
    wr(R, s + '\n#ifdef NANO7_DEVICE\n#include "../../../nano7dev/n7cache_dev.inc"\n#endif\n')
# 7 no atlases, no sanding, no filter-effect copies (all need CPU pixels of the source images)
once('PvzpLib/Reanimator.cpp', 'void ReanimationCreateAtlas(ReanimatorDefinition* theDefinition, ReanimationType theReanimationType)\n{\n',
     '#ifdef NANO7_DEVICE\n\t(void)theDefinition; (void)theReanimationType; return;   // atlases are built from CPU pixels\n#endif\n')
once('PvzpLib/PvzpCommon.cpp', 'void PvzpSandImageIfNeeded(Image* theImage)\n{\n',
     '#ifdef NANO7_DEVICE\n\t((MemoryImage*)theImage)->mRenderFlags &= ~RENDERIMAGEFLAG_SANDING; return;   // edge fix-up needs CPU pixels\n#endif\n')
once('PvzpLib/FilterEffect.cpp', 'Image* FilterEffectGetImage(Image* theImage, FilterEffect theFilterEffect)\n{\n',
     '#ifdef NANO7_DEVICE\n\tif (((MemoryImage*)theImage)->mN7T) { (void)theFilterEffect; return theImage; }   // washed-out copies need CPU pixels\n#endif\n')
# 8 resources: a .n7t image already has its alpha (the repacker merged it)
M = 'SexyAppFramework/misc/ResourceManager.cpp'
s = rd(M)
if 'N7_ALPHA_DONE' not in s:
    s = s.replace('bool ResourceManager::DoLoadImage(ImageRes *theRes)\n{', '#ifdef NANO7_DEVICE\n#define N7_ALPHA_DONE(i) ((i)->mN7T)\n#else\n#define N7_ALPHA_DONE(i) false\n#endif\nbool ResourceManager::DoLoadImage(ImageRes *theRes)\n{', 1)
    s = s.replace('\tif (isNew && aGLImage->mBits == nullptr && (', '\tif (isNew && !N7_ALPHA_DONE(aGLImage) && aGLImage->mBits == nullptr && (', 1)
    s = s.replace('\tif (isNew)\n\t{\n\t\tif (!theRes->mAlphaImage.empty())', '\tif (isNew && !N7_ALPHA_DONE(aGLImage))\n\t{\n\t\tif (!theRes->mAlphaImage.empty())', 1)
    assert s.count('N7_ALPHA_DONE(aGLImage)') == 2
    wr(M, s)
# 9 files: whole-file reads from the data folder
P = 'SexyAppFramework/paklib/'
once(P + 'PakInterface.h', '\tFILE*\t\t\t\t\tmFP;\n', '#ifdef NANO7_DEVICE\n\tbool\t\t\t\t\tmN7 = false;\t\t// a whole file read into memory (nano7 device)\n\tuint8_t*\t\t\t\tmN7Data = nullptr;\n\tint\t\t\t\t\t\tmN7Size = 0;\n#endif\n')
s = rd(P + 'PakInterface.cpp')
if 'Nano7DevOpen' not in s:
    hdr = '#ifdef NANO7_DEVICE\nPFILE* Nano7DevOpen(const char* theFileName, const char* theAccess);\n#define N7F(f) ((f)->mN7)\n#endif\n'
    k = s.index('PFILE* PakInterface::FOpen(const char* theFileName, const char* anAccess)')
    s = s[:k] + hdr + s[k:]
    def ins(head, code):
        global s
        k = s.index(head); b = s.index('{', k) + 1
        s = s[:b] + '\n#ifdef NANO7_DEVICE\n' + code + '\n#endif' + s[b:]
    ins('PFILE* PakInterface::FOpen(const char* theFileName, const char* anAccess)', '\tif (PFILE* aN7 = Nano7DevOpen(theFileName, anAccess)) return aN7;')
    ins('int PakInterface::FClose(PFILE* theFile)', '\tif (N7F(theFile)) { free(theFile->mN7Data); delete theFile; return 0; }')
    ins('int PakInterface::FSeek(PFILE* theFile, long theOffset, int theOrigin)',
        '\tif (N7F(theFile)) { long p = theOrigin == SEEK_SET ? theOffset : theOrigin == SEEK_END ? theFile->mN7Size + theOffset : theFile->mPos + theOffset;\n'
        '\t\ttheFile->mPos = (int)std::clamp(p, 0L, (long)theFile->mN7Size); return 0; }')
    ins('int PakInterface::FTell(PFILE* theFile)', '\tif (N7F(theFile)) return theFile->mPos;')
    ins('size_t PakInterface::FRead(void* thePtr, int theElemSize, int theCount, PFILE* theFile)',
        '\tif (N7F(theFile)) { if (theElemSize <= 0) return 0; int n = std::min(theElemSize * theCount, theFile->mN7Size - theFile->mPos) / theElemSize;\n'
        '\t\tmemcpy(thePtr, theFile->mN7Data + theFile->mPos, (size_t)n * theElemSize); theFile->mPos += n * theElemSize; return n; }')
    ins('int PakInterface::FGetC(PFILE* theFile)',
        '\tif (N7F(theFile)) { while (theFile->mPos < theFile->mN7Size) { char c = (char)theFile->mN7Data[theFile->mPos++]; if (c != \'\\r\') return (uint8_t)c; } return EOF; }')
    ins('int PakInterface::UnGetC(int theChar, PFILE* theFile)', '\tif (N7F(theFile)) { theFile->mPos = std::max(theFile->mPos - 1, 0); return theChar; }')
    ins('char* PakInterface::FGetS(char* thePtr, int theSize, PFILE* theFile)',
        '\tif (N7F(theFile)) { int i = 0; if (theFile->mPos >= theFile->mN7Size) return nullptr;\n'
        '\t\twhile (i < theSize - 1 && theFile->mPos < theFile->mN7Size) { char c = (char)theFile->mN7Data[theFile->mPos++]; if (c != \'\\r\') thePtr[i++] = c; if (c == \'\\n\') break; }\n'
        '\t\tthePtr[i] = 0; return thePtr; }')
    ins('int PakInterface::FEof(PFILE* theFile)', '\tif (N7F(theFile)) return theFile->mPos >= theFile->mN7Size;')
    wr(P + 'PakInterface.cpp', s)
print('device patch applied to', os.path.abspath(SRC))
