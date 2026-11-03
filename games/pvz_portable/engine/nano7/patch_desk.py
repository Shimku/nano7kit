# nano7kit measuring build: source edits on a fresh PvZ-Portable checkout (desktop, headless, instrumented).
import sys, os, re
S = sys.argv[1]
import hashlib, json
_APPLIED_F = os.path.join(S, '.nano7_applied.json')
_APPLIED = set(json.load(open(_APPLIED_F))) if os.path.exists(_APPLIED_F) else set()
def _save_applied():
    json.dump(sorted(_APPLIED), open(_APPLIED_F, 'w'))
def edit(path, old, new, count=1):
    key = hashlib.sha1((path + '\0' + old + '\0' + new).encode()).hexdigest()
    if key in _APPLIED: return
    if os.environ.get('NANO7_MARK_ONLY'): _APPLIED.add(key); _save_applied(); return
    _edit(path, old, new, count); _APPLIED.add(key); _save_applied()
def _edit(path, old, new, count=1):
    p = os.path.join(S, path); s = open(p).read()
    if new in s: return
    n = s.count(old)
    if n < 1: raise SystemExit(f'anchor not found in {path}: {old[:70]!r}')
    s = s.replace(old, new, count if count else n)
    open(p, 'w').write(s)
INC = '#include "../../nano7/memtrack.h"\n'
# GL: fake loader
edit('SexyAppFramework/graphics/GLPlatform.h', '\tgladLoadGLES2((GLADloadfunc)SDL_GL_GetProcAddress);',
     '\tgladLoadGLES2((GLADloadfunc)nano7_gl_proc);')
edit('SexyAppFramework/graphics/GLPlatform.h', 'inline void PlatformGLInit()', 'extern "C" void* nano7_gl_proc(const char*);\ninline void PlatformGLInit()')
edit('SexyAppFramework/graphics/GLInterface.cpp', 'SDL_GL_GetDrawableSize((SDL_Window*)mApp->mWindow, &width, &height);',
     'SDL_GetWindowSize((SDL_Window*)mApp->mWindow, &width, &height);')
edit('SexyAppFramework/graphics/GLInterface.cpp', '#define GLAD_GLES2_IMPLEMENTATION', '#include "../../../nano7/texlog.h"\n#define GLAD_GLES2_IMPLEMENTATION')
edit('SexyAppFramework/graphics/GLInterface.cpp', '''	mWidth  = theImage->mWidth;
	mHeight = theImage->mHeight;
	mBitsChangedCount = theImage->mBitsChangedCount;''', '''	if (createTextures) nano7_tex_note(this, theImage, (int)aFormat);
	mWidth  = theImage->mWidth;
	mHeight = theImage->mHeight;
	mBitsChangedCount = theImage->mBitsChangedCount;''')
edit('SexyAppFramework/graphics/GLInterface.cpp', '''	mTextures.clear();
	mTexMemSize = 0;''', '''	mTextures.clear();
	mTexMemSize = 0;
	nano7_tex_drop(this);''')
edit('SexyAppFramework/graphics/GLInterface.cpp', '''void TextureData::CheckCreateTextures(MemoryImage *theImage)
{''', '''void TextureData::CheckCreateTextures(MemoryImage *theImage)
{
	nano7_tex_touch(this);''')
# sound and music: silent, but sizes recorded
edit('SexyAppFramework/SexyAppBase.cpp', '#include "sound/SDLSoundManager.h"', '#include "../../nano7/NanoSoundManager.h"')
edit('SexyAppFramework/SexyAppBase.cpp', 'mSoundManager = std::make_unique<SDLSoundManager>();', 'mSoundManager = std::make_unique<NanoSoundManager>();')
edit('SexyAppFramework/SexyAppBase.cpp', '\telse\n\t\treturn new SDLMusicInterface();', '\telse\n\t\treturn new DummyMusicInterface();')
edit('Lawn/System/Music.cpp', '\tint aSize = p_ftell(pFile);', '\tint aSize = p_ftell(pFile);\n\t{ extern unsigned long long nano7_music_bytes; nano7_music_bytes += aSize; }')
# heap tags
edit('SexyAppFramework/misc/ResourceManager.cpp', '#include "ResourceManager.h"', '#include "ResourceManager.h"\n#include "../../../nano7/memtrack.h"')
edit('SexyAppFramework/misc/ResourceManager.cpp', 'bool ResourceManager::DoLoadFont(FontRes* theRes)\n{', 'bool ResourceManager::DoLoadFont(FontRes* theRes)\n{\n\tMT_TAG(MT_FONT);')
edit('SexyAppFramework/misc/ResourceManager.cpp', 'bool ResourceManager::DoLoadImage(ImageRes *theRes)\n{', 'bool ResourceManager::DoLoadImage(ImageRes *theRes)\n{\n\tMT_TAG(MT_PIXELS);')
edit('SexyAppFramework/misc/ResourceManager.cpp', 'bool ResourceManager::ParseResourcesFile(const std::string& theFilename)\n{', 'bool ResourceManager::ParseResourcesFile(const std::string& theFilename)\n{\n\tMT_TAG(MT_RESMGR);')
edit('SexyAppFramework/SexyAppBase.cpp', 'bool SexyAppBase::LoadProperties(const std::string& theFileName, bool required, bool checkSig)\n{',
     'bool SexyAppBase::LoadProperties(const std::string& theFileName, bool required, bool checkSig)\n{\n\tMT_TAG(MT_STRINGS);')
edit('SexyAppFramework/SexyAppBase.cpp', '#include "../../nano7/NanoSoundManager.h"', '#include "../../nano7/NanoSoundManager.h"\n#include "../../nano7/memtrack.h"')
edit('SexyAppFramework/SexyAppBase.cpp', '''Sexy::GLImage* SexyAppBase::GetImage(const std::string& theFileName, bool commitBits)
{''', '''Sexy::GLImage* SexyAppBase::GetImage(const std::string& theFileName, bool commitBits)
{
	MT_TAG(MT_PIXELS);''')
edit('PvzpLib/PvzpStringFile.cpp', 'void PvzpStringListLoad(const char* theFileName)\n{', '#include "../../nano7/memtrack.h"\nvoid PvzpStringListLoad(const char* theFileName)\n{\n\tMT_TAG(MT_STRINGS);')
edit('PvzpLib/Reanimator.cpp', 'bool ReanimationLoadDefinition(const std::string& theFileName, ReanimatorDefinition* theDefinition)\n{',
     '#include "../../nano7/memtrack.h"\n#include "../../nano7/reanimstat.h"\nbool ReanimationLoadDefinition(const std::string& theFileName, ReanimatorDefinition* theDefinition)\n{\n\tMT_TAG(MT_REANIM);')
edit('PvzpLib/Reanimator.cpp', '\tReanimAtlas* aAtlas = new ReanimAtlas();\n', '\tMT_TAG(MT_PIXELS);\n\tReanimAtlas* aAtlas = new ReanimAtlas();\n')
# nano: no startup preload of every compiled reanim
edit('PvzpLib/Reanimator.cpp', '\t\tif (DefinitionIsCompiled(aReanimationParams->mReanimFileName))\n\t\t\tReanimatorEnsureDefinitionLoaded',
     '\t\tif (!getenv("NANO7_LAZY_REANIM") && DefinitionIsCompiled(aReanimationParams->mReanimFileName))\n\t\t\tReanimatorEnsureDefinitionLoaded')
edit('PvzpLib/PvzpParticle.cpp', 'void PvzpParticleLoadDefinitions(const ParticleParams* theParticleParamArray, int theParticleParamArraySize)\n{',
     '#include "../../nano7/memtrack.h"\nvoid PvzpParticleLoadDefinitions(const ParticleParams* theParticleParamArray, int theParticleParamArraySize)\n{\n\tMT_TAG(MT_PARTICLE);')
edit('PvzpLib/Trail.cpp', 'void TrailLoadDefinitions(TrailParams* theTrailParamArray, int theTrailParamArraySize)\n{',
     '#include "../../nano7/memtrack.h"\nvoid TrailLoadDefinitions(TrailParams* theTrailParamArray, int theTrailParamArraySize)\n{\n\tMT_TAG(MT_PARTICLE);')
edit('Lawn/Board.cpp', 'Board::Board(LawnApp* theApp)', '#include "../../nano7/memtrack.h"\nBoard::Board(LawnApp* theApp)')
# pools: cap from NANO7_POOLS, record high water
edit('PvzpLib/DataArray.h', '#include "PvzpCommon.h"\n', '#include "PvzpCommon.h"\n#include "../../nano7/poolcap.h"\n')
edit('PvzpLib/DataArray.h', '''		PVZP_ASSERT(mItems == nullptr);
		mItems = std::make_unique<DataArrayItem[]>(theMaxSize);''', '''		PVZP_ASSERT(mItems == nullptr);
		theMaxSize = nano7_pool_cap(theName, theMaxSize, sizeof(DataArrayItem) + sizeof(unsigned int));
		MtScope aPoolTag(MT_POOL);
		mItems = std::make_unique<DataArrayItem[]>(theMaxSize);''')
edit('PvzpLib/DataArray.h', '''		PVZP_ASSERT(mSize < mMaxSize, "Data array full: {}", mName);''', '''		if (mSize >= mMaxSize) nano7_pool_full(mName, mMaxSize);''')
edit('PvzpLib/DataArray.h', '''		mSize++;

		return &aNewItem;''', '''		mSize++;
		nano7_pool_note(mName, mSize);

		return &aNewItem;''')
# bot hook
edit('LawnApp.cpp', '''		mMusic->MusicUpdate();

		CheckForGameEnd();''', '''		mMusic->MusicUpdate();

		CheckForGameEnd();
		{ extern void nano7_bot_update(LawnApp*); nano7_bot_update(this); }''')
print('patched')
# pass 2: pixel buffers made inside MemoryImage/GLImage count as pixels (they become textures on the nano)
import re as _re
def tag_funcs(path, names, inc):
    p = os.path.join(S, path); s = open(p).read()
    if 'nano7 pixel tag' in s: return
    s = s.replace('using namespace Sexy;', 'using namespace Sexy;\n' + inc + ' /* nano7 pixel tag */', 1)
    for n in names:
        m = _re.search(r'\n[^\n]*' + _re.escape(n) + r'\([^\n]*\)[^\n]*\n\{\n', s)
        if not m: raise SystemExit('fn not found ' + n)
        s = s[:m.end()] + '\tMT_TAG(MT_PIXELS);\n' + s[m.end():]
    open(p, 'w').write(s)
tag_funcs('SexyAppFramework/graphics/MemoryImage.cpp', ['MemoryImage::CommitBits', 'MemoryImage::GetNativeAlphaData', 'MemoryImage::GetRLAlphaData',
    'MemoryImage::GetRLAdditiveData', 'MemoryImage::SetBits', 'MemoryImage::Create', 'MemoryImage::GetBits', 'MemoryImage::Palletize'], '#include "../../../nano7/memtrack.h"')
tag_funcs('SexyAppFramework/graphics/GLImage.cpp', ['GLImage::Create'], '#include "../../../nano7/memtrack.h"')
edit('SexyAppFramework/graphics/GLInterface.cpp', '''void TextureData::CreateTextures(MemoryImage *theImage)
{''', '''#include "../../../nano7/memtrack.h"
void TextureData::CreateTextures(MemoryImage *theImage)
{
	MT_TAG(MT_PIXELS);''')
# music: the nano build will not keep tunes in RAM; record the size only
edit('Lawn/System/Music.cpp', '''	void* aData = operator new[](aSize);''', '''	if (getenv("NANO7_NO_MUSIC")) { p_fclose(pFile); return false; }
	void* aData = operator new[](aSize);''')
print('patched pass 2')
# LOW_MEMORY bug: the old board is deleted lazily and releases DelayLoad_Background1 after the award
# screen has loaded it, leaving IMAGE_BACKGROUND1 dangling (crash on the zombie-note award after 1-9).
edit('LawnApp.cpp', '''	if (IsAdventureMode())
	{
		int aLevel = mBoard->mLevel;
		KillBoard();
''', '''	if (IsAdventureMode())
	{
		int aLevel = mBoard->mLevel;
		KillBoard();
		ProcessSafeDeleteList();  // nano7kit: release the old board's resources before the next screen loads its own
''')
print('patched pass 3')
edit('SexyAppFramework/SexyAppBase.h', '\tvoid					ProcessSafeDeleteList();\n', '\tvoid					ProcessSafeDeleteList();\npublic:\n\tvoid					Nano7ProcessSafeDeleteList() { ProcessSafeDeleteList(); }\nprotected:\n')
print('patched pass 4')
# pass 5: compact animations (NANO7_COMPACT_REANIM): each track keeps one copy of each run of identical
# frames plus a 16-bit frame index. Reads go through ReanimTf().
edit('PvzpLib/Reanimator.h', 'struct ReanimatorTransformArray {', '''extern bool gNano7CompactReanim;
struct ReanimatorTransformArray;
inline class ReanimatorTransform& ReanimTf(const ReanimatorTransformArray& a, int i);
struct ReanimatorTransformArray {''')
edit('PvzpLib/Reanimator.h', '''public:
	ReanimatorTransform();
};''', '''public:
	ReanimatorTransform();
};
inline ReanimatorTransform& ReanimTf(const ReanimatorTransformArray& a, int i)
{
	if (!gNano7CompactReanim) return a.mTransforms[i];
	const uint16_t* idx = (const uint16_t*)a.mTransforms;
	return ((ReanimatorTransform*)((char*)a.mTransforms + ((a.count * 2 + 3) & ~3)))[idx[i]];
}''')
if 'Nano7CompactTrack' not in open(os.path.join(S,'PvzpLib/Reanimator.cpp')).read(): edit('PvzpLib/Reanimator.cpp', '''bool ReanimationLoadDefinition(const std::string& theFileName, ReanimatorDefinition* theDefinition)
{''', '''bool gNano7CompactReanim = getenv("NANO7_COMPACT_REANIM") != nullptr;
static void Nano7CompactTrack(ReanimatorTrack* aTrack)
{
	int n = aTrack->mTransforms.count;
	if (n <= 0) return;
	ReanimatorTransform* aOld = aTrack->mTransforms.mTransforms;
	int aRuns = 0;
	for (int i = 0; i < n; i++) if (i == 0 || memcmp(&aOld[i], &aOld[i - 1], sizeof(ReanimatorTransform))) aRuns++;
	int aPad = (n * 2 + 3) & ~3;
	char* aBlock = (char*)operator new[](aPad + aRuns * sizeof(ReanimatorTransform));
	uint16_t* aIdx = (uint16_t*)aBlock;
	ReanimatorTransform* aUnique = (ReanimatorTransform*)(aBlock + aPad);
	int u = -1;
	for (int i = 0; i < n; i++) {
		if (i == 0 || memcmp(&aOld[i], &aOld[i - 1], sizeof(ReanimatorTransform))) { u++; memcpy(&aUnique[u], &aOld[i], sizeof(ReanimatorTransform)); }
		aIdx[i] = (uint16_t)u;
	}
	delete[] (char*)aOld;
	aTrack->mTransforms.mTransforms = (ReanimatorTransform*)aBlock;
}
bool ReanimationLoadDefinition(const std::string& theFileName, ReanimatorDefinition* theDefinition)
{''')
edit('PvzpLib/Reanimator.cpp', '''			if (*aTransform.mText == '\\0')
				aTransform.mText = aPrevText;
			else
				aPrevText = aTransform.mText;
		}
''', '''			if (*aTransform.mText == '\\0')
				aTransform.mText = aPrevText;
			else
				aPrevText = aTransform.mText;
		}
		if (gNano7CompactReanim) Nano7CompactTrack(aTrack);
''')
edit('PvzpLib/Reanimator.cpp', '''void ReanimationFreeDefinition(ReanimatorDefinition* theDefinition)
{''', '''void ReanimationFreeDefinition(ReanimatorDefinition* theDefinition)
{
	if (gNano7CompactReanim) return;  // measuring build: compacted definitions live until exit''')
def rewrite_tf(path, skip_funcs=()):
    p = os.path.join(S, path); s = open(p).read()
    if 'ReanimTf(' in s and 'mTransforms.mTransforms[' not in s: return
    # protect the load and free functions
    keep = []
    for fn in skip_funcs:
        a = s.find(fn); b = s.find('\n}\n', a) + 3; keep.append((a, b))
    out = []; i = 0; key = 'mTransforms.mTransforms['
    while True:
        j = s.find(key, i)
        if j < 0: out.append(s[i:]); break
        if any(a <= j < b for a, b in keep): out.append(s[i:j + len(key)]); i = j + len(key); continue
        # walk back over the object expression
        k = j
        while k > 0:
            c = s[k - 1]
            if c.isalnum() or c in '_.': k -= 1
            elif s[k - 2:k] == '->': k -= 2
            elif c == ']':
                d = 1; k -= 1
                while d: k -= 1; d += {']': 1, '[': -1}.get(s[k], 0)
            else: break
        obj = s[k:j]
        e = j + len(key); d = 1; m = e
        while d: d += {'[': 1, ']': -1}.get(s[m], 0); m += 1
        expr = s[e:m - 1]
        out.append(s[i:k]); out.append('ReanimTf(' + obj + 'mTransforms, ' + expr + ')'); i = m
    open(p, 'w').write(''.join(out))
rewrite_tf('PvzpLib/Reanimator.cpp', ('bool ReanimationLoadDefinition(', 'void ReanimationFreeDefinition('))
rewrite_tf('PvzpLib/ReanimAtlas.cpp'); rewrite_tf('Lawn/Zombie.cpp'); rewrite_tf('Lawn/Widget/CreditScreen.cpp')
print('patched pass 5')
edit('SexyAppFramework/graphics/GLInterface.cpp', 'constexpr const int MAX_VERTICES = 16384;', 'static const int MAX_VERTICES = getenv("NANO7_SMALL_VERTS") ? 2048 : 16384;  // nano cut: 48 KB batch buffer')
edit('PvzpLib/ReanimAtlas.cpp', '''			Image*& aImage = ReanimTf(aTrack->mTransforms, aKeyIndex).mImage;
			if (aImage''', '''			Image*& aImage = ReanimTf(aTrack->mTransforms, aKeyIndex).mImage;
			if (aKeyIndex > 0 && &aImage == &ReanimTf(aTrack->mTransforms, aKeyIndex - 1).mImage)
				continue;  // compacted: this frame shares the previous frame's record, already rewritten
			if (aImage''')
# pass 6 (M1): lazy image bits, no pool effect, font parser data dropped after load
edit('SexyAppFramework/misc/ResourceManager.cpp', '''	if (aGLImage->mPurgeBits)
		aGLImage->PurgeBits();

	ResourceLoadedHook(theRes);''', '''	if (aGLImage->mPurgeBits)
		aGLImage->PurgeBits();
	// nano7: keep only the size; decode again from the pak when the texture is first made
	if (getenv("NANO7_LAZY_IMAGES") && aGLImage->mRenderData == nullptr && !aGLImage->mFilePath.empty()
		&& theRes->mAlphaImage.empty() && theRes->mAlphaGridImage.empty() && aGLImage->mColorTable == nullptr)
	{
		aGLImage->mPurgeBits = true;
		aGLImage->mBits.reset();
	}

	ResourceLoadedHook(theRes);''')
edit('LawnApp.cpp', '''	mPoolEffect = std::make_unique<PoolEffect>();
	mPoolEffect->PoolEffectInitialize();''', '''	mPoolEffect = std::make_unique<PoolEffect>();
	if (!getenv("NANO7_NO_POOL")) mPoolEffect->PoolEffectInitialize();  // nano7: day levels never draw the pool''')
edit('SexyAppFramework/graphics/ImageFont.cpp', '''	mInitialized = LoadDescriptor(theFontDescFileName); ;''', '''	mInitialized = LoadDescriptor(theFontDescFileName); ;
	if (getenv("NANO7_FONT_TRIM")) { mDefineMap.clear(); mCurrentLine.clear(); mCurrentLine.shrink_to_fit(); }  // nano7: parser data is not used after load''')
edit('SexyAppFramework/SexyAppBase.cpp', '''	anImage->mFilePath = theFileName;
#ifdef LOW_MEMORY
	anImage->mPurgeBits = true;
#endif''', '''	anImage->mFilePath = theFileName;
#ifdef LOW_MEMORY
	anImage->mPurgeBits = true;
	// nano7: keep only the size; GetBits() decodes it again from the pak when a texture is made
	static bool sLazy = getenv("NANO7_LAZY_IMAGES") != nullptr;
	if (sLazy && ImageLib::gAlphaComposeColor == 0xFFFFFF)
		anImage->mBits.reset();
#endif''')
edit('SexyAppFramework/misc/ResourceManager.cpp', '''	if (isNew)
	{
		if (!theRes->mAlphaImage.empty())''', '''	if (isNew && aGLImage->mBits == nullptr && (!theRes->mAlphaImage.empty() || !theRes->mAlphaGridImage.empty()))
		aGLImage->GetBits();  // nano7 lazy images: the alpha merge needs the colour bits now
	if (isNew)
	{
		if (!theRes->mAlphaImage.empty())''')
edit('SexyAppFramework/graphics/ImageFont.cpp', '''					if (isNew)
						anImage->Palletize();''', '''					if (isNew && !getenv("NANO7_LAZY_IMAGES"))
						anImage->Palletize();  // nano7 lazy images: no palette copy, bits decoded when the texture is made''')
edit('SexyAppFramework/SexyAppBase.cpp', '''	std::unique_ptr<ImageLib::Image> aLoadedImage(ImageLib::GetImage(theFileName, true));''', '''	std::unique_ptr<ImageLib::Image> aLoadedImage([&] { MT_TAG(MT_DECODE); return ImageLib::GetImage(theFileName, true); }());''')
edit('SexyAppFramework/graphics/MemoryImage.cpp', '''				aLoadedImage.reset(ImageLib::GetImage(mFilePath, true));''', '''				{ MT_TAG(MT_DECODE); aLoadedImage.reset(ImageLib::GetImage(mFilePath, true)); }''')
# pass 7 (M1): flat pak index. One 20-24 byte record per file, sorted by a 64-bit name hash; no name strings kept.
edit('SexyAppFramework/paklib/PakInterface.h', '''class PakRecord
{
public:
	PakCollection*			mCollection;			//+0x0''', '''#ifdef NANO7_FLAT_PAK
class PakRecord
{
public:
	PakCollection*			mCollection;
	uint64_t				mHash;					// FNV-1a 64 of the normalised path
	int						mStartPos;
	int						mSize;
};
typedef std::vector<PakRecord> PakRecordMap;
#else
class PakRecord
{
public:
	PakCollection*			mCollection;			//+0x0''')
edit('SexyAppFramework/paklib/PakInterface.h', '''typedef std::map<std::string, PakRecord> PakRecordMap;''', '''typedef std::map<std::string, PakRecord> PakRecordMap;
#endif''')
edit('SexyAppFramework/paklib/PakInterface.h', '''	static std::string		NormalizePakPath(std::string_view theFileName);''', '''	static std::string		NormalizePakPath(std::string_view theFileName);
#ifdef NANO7_FLAT_PAK
	static uint64_t			Nano7Hash(const std::string& theKey) { uint64_t h = 1469598103934665603ULL; for (unsigned char c : theKey) { h ^= c; h *= 1099511628211ULL; } return h; }
	PakRecord*				Nano7Find(const std::string& theKey);
#endif''')
src_add = r'''
#ifdef NANO7_FLAT_PAK
PakRecord* PakInterface::Nano7Find(const std::string& theKey)
{
	uint64_t h = Nano7Hash(theKey);
	auto it = std::lower_bound(mPakRecordMap.begin(), mPakRecordMap.end(), h, [](const PakRecord& r, uint64_t v) { return r.mHash < v; });
	return (it != mPakRecordMap.end() && it->mHash == h) ? &*it : nullptr;
}

bool PakInterface::AddPakFile(const std::string& theFileName)
{
	FILE *aFileHandle = fcaseopen(theFileName.c_str(), "rb");
	if (!aFileHandle)
		return false;
	fseek(aFileHandle, 0, SEEK_END);
	size_t aFileSize = ftell(aFileHandle);
	fseek(aFileHandle, 0, SEEK_SET);
	mPakCollectionList.emplace_back(aFileHandle);
	PakCollection* aPakCollection = &mPakCollectionList.back();

	PakRecord aSelf{aPakCollection, 0, 0, (int)aFileSize};
	PFILE* aFP = new PFILE;
	aFP->mRecord = &aSelf; aFP->mPos = 0; aFP->mFP = nullptr; aFP->mBufferStart = 0; aFP->mBufferLen = 0;

	uint32_t aMagic = 0, aVersion = 0;
	FRead(&aMagic, sizeof(uint32_t), 1, aFP);
	FRead(&aVersion, sizeof(uint32_t), 1, aFP);
	if (Sexy::FromLE32(aMagic) != 0xBAC04AC0 || Sexy::FromLE32(aVersion) > 0)
	{
		FClose(aFP);
		mPakCollectionList.pop_back();
		return false;
	}

	size_t aFirst = mPakRecordMap.size();
	int aPos = 0;
	for (;;)
	{
		uint8_t aFlags = 0;
		int aCount = FRead(&aFlags, 1, 1, aFP);
		if ((aFlags & FILEFLAGS_END) || (aCount == 0))
			break;
		uint8_t aNameWidth = 0;
		char aName[256];
		FRead(&aNameWidth, 1, 1, aFP);
		FRead(aName, 1, aNameWidth, aFP);
		aName[aNameWidth] = 0;
		int aSrcSize = 0;
		FRead(&aSrcSize, sizeof(int), 1, aFP);
		aSrcSize = static_cast<int>(Sexy::FromLE32(static_cast<uint32_t>(aSrcSize)));
		int64_t aFileTime;
		FRead(&aFileTime, sizeof(int64_t), 1, aFP);
		for (int i = 0; i < aNameWidth; i++)
			if (aName[i] == '\\') aName[i] = '/';
		mPakRecordMap.push_back(PakRecord{aPakCollection, Nano7Hash(NormalizePakPath(aName)), aPos, aSrcSize});
		aPos += aSrcSize;
	}
	int anOffset = FTell(aFP);
	FClose(aFP);
	for (size_t i = aFirst; i < mPakRecordMap.size(); i++)
		mPakRecordMap[i].mStartPos += anOffset;
	std::stable_sort(mPakRecordMap.begin(), mPakRecordMap.end(), [](const PakRecord& a, const PakRecord& b) { return a.mHash < b.mHash; });
	// later paks override earlier ones with the same name: keep the last record for each hash
	std::vector<PakRecord> aOut;
	aOut.reserve(mPakRecordMap.size());
	for (size_t i = 0; i < mPakRecordMap.size(); i++)
	{
		if (!aOut.empty() && aOut.back().mHash == mPakRecordMap[i].mHash)
			aOut.back() = mPakRecordMap[i];
		else
			aOut.push_back(mPakRecordMap[i]);
	}
	mPakRecordMap.swap(aOut);
	return true;
}
#else
bool PakInterface::AddPakFile(const std::string& theFileName)
{'''
p = os.path.join(S, 'SexyAppFramework/paklib/PakInterface.cpp'); s = open(p).read()
if 'Nano7Find' not in s:
    a = s.find('bool PakInterface::AddPakFile(const std::string& theFileName)\n{')
    s = s[:a] + src_add.lstrip('\n') + s[a + len('bool PakInterface::AddPakFile(const std::string& theFileName)\n{'):]
    b = s.find('PFILE* PakInterface::FOpen(')
    s = s[:b] + '#endif\n\n' + s[b:]
    s = s.replace('''		std::string aKey = NormalizePakPath(theFileName);
		auto anItr = mPakRecordMap.find(aKey);
		if (anItr != mPakRecordMap.end())
		{
			PFILE* aPFP = new PFILE;
			aPFP->mRecord = &anItr->second;''', '''		std::string aKey = NormalizePakPath(theFileName);
#ifdef NANO7_FLAT_PAK
		if (PakRecord* aRec = Nano7Find(aKey))
		{
			PFILE* aPFP = new PFILE;
			aPFP->mRecord = aRec;
#else
		auto anItr = mPakRecordMap.find(aKey);
		if (anItr != mPakRecordMap.end())
		{
			PFILE* aPFP = new PFILE;
			aPFP->mRecord = &anItr->second;
#endif''')
    open(p, 'w').write(s)
edit('SexyAppFramework/imagelib/ImageLib.cpp', '''		if (gPakInterface->mPakRecordMap.contains(PakInterface::NormalizePakPath(thePath)))''', '''#ifdef NANO7_FLAT_PAK
		if (gPakInterface->Nano7Find(PakInterface::NormalizePakPath(thePath)))
#else
		if (gPakInterface->mPakRecordMap.contains(PakInterface::NormalizePakPath(thePath)))
#endif''')
print('patched pass 7')
# pass 8 (M1): pre-compacted .n7r animations, export step for the repacker, per-level freeing
edit('PvzpLib/Reanimator.h', '''extern bool gNano7CompactReanim;''', '''extern bool gNano7CompactReanim;
#include "../../nano7/n7r.h"''')
edit('PvzpLib/Reanimator.h', '''	if (!gNano7CompactReanim) return a.mTransforms[i];''', '''	if (gNano7N7R)
	{
		const char* b = (const char*)a.mTransforms;
		const uint16_t* idx = (const uint16_t*)(b + sizeof(void*));
		return Nano7Decode(*(Nano7Def* const*)b, ((const Nano7Tf*)(b + sizeof(void*) + a.count * 2))[idx[i]]);
	}
	if (!gNano7CompactReanim) return a.mTransforms[i];''')
edit('PvzpLib/Reanimator.cpp', '''{
	MT_TAG(MT_REANIM);
	if (!DefinitionLoadXML(theFileName, &gReanimatorDefMap, theDefinition))''', '''{
	MT_TAG(MT_REANIM);
	if (gNano7N7R)
		return Nano7LoadN7R(theFileName, theDefinition);
	if (!DefinitionLoadXML(theFileName, &gReanimatorDefMap, theDefinition))''')
edit('PvzpLib/Reanimator.cpp', '''	if (gNano7CompactReanim) return;  // measuring build''', '''	if (gNano7N7R) { Nano7FreeN7R(theDefinition); return; }
	if (gNano7CompactReanim) return;  // measuring build''')
edit('PvzpLib/ReanimAtlas.cpp', '''	int aAtlasWidth, aAtlasHeight;
	ArrangeImages(aAtlasWidth, aAtlasHeight);
''', '''	int aAtlasWidth, aAtlasHeight;
	ArrangeImages(aAtlasWidth, aAtlasHeight);

	if (gNano7N7R)
	{
		// .n7r: frames refer to the definition's image table, so encode the table entries once
		Nano7Def* aDef = Nano7DefOf(theReanimDef);
		for (int i = 0; aDef && i < aDef->nimg; i++)
		{
			Image*& aImage = aDef->images[i];
			if (aImage != nullptr && aImage->mWidth <= 254 && aImage->mHeight <= 254)
			{
				intptr_t aImageIndex = FindImage(aImage);
				if (aImageIndex >= 0)
					aImage = (Image*)(aImageIndex + 1);
			}
		}
	}
	else
''')
edit('PvzpLib/Definition.cpp', '''bool DefinitionLoadImage(Image** theImage, const std::string& theName)
{''', '''static bool DefinitionLoadImage_n7(Image** theImage, const std::string& theName);
bool DefinitionLoadImage(Image** theImage, const std::string& theName)
{
	bool aOk = DefinitionLoadImage_n7(theImage, theName);
	if (aOk) Nano7NoteImageName(*theImage, theName);
	return aOk;
}
static bool DefinitionLoadImage_n7(Image** theImage, const std::string& theName)
{''')
edit('PvzpLib/Definition.cpp', '''	_Font* aFont = gSexyAppBase->mResourceManager->LoadFont(theName);
	*theFont = aFont;''', '''	_Font* aFont = gSexyAppBase->mResourceManager->LoadFont(theName);
	*theFont = aFont;
	Nano7NoteFontName(aFont, theName);''')
edit('LawnApp.cpp', '''void LawnApp::MakeNewBoard()
{
	KillBoard();''', '''void LawnApp::MakeNewBoard()
{
	KillBoard();
	Nano7LevelFree(this);  // nano7: drop animations and textures the last screen used''')
edit('PvzpLib/PvzpCommon.cpp', '''	aImage->mFilePath.clear();
}''', '''	if (getenv("NANO7_LAZY_IMAGES"))
		Nano7NoteSanded(aImage);  // nano7: keep the path so the texture can be rebuilt (and sanded again)
	else
		aImage->mFilePath.clear();
}''')
print('patched pass 8')
edit('PvzpLib/PvzpCommon.cpp', '''void FixPixelsOnAlphaEdgeForBlending(Image* theImage)
{''', '''#include "../../nano7/n7r.h"
void FixPixelsOnAlphaEdgeForBlending(Image* theImage)
{''')
# pass 9 (M1): resource groups pruned by the repacker resolve to nullptr instead of a fatal error
for _kind, _map in (('Image', 'mImageMap'), ('Sound', 'mSoundMap'), ('Font', 'mFontMap')):
    edit('SexyAppFramework/misc/ResourceManager.cpp', '''	Fail(std::format("%s resource not found: {}", theId));''' % _kind, '''	if (getenv("NANO7_PRUNED_OK") && %s.find(theId) == %s.end())
		return {};  // nano7: this id belongs to a resource group the repacker removed (stripped mode)
	Fail(std::format("%s resource not found: {}", theId));''' % (_map, _map, _kind))
# pass 10 (M1): animation track instances come from the normal heap instead of grow-only size-class pools
edit('PvzpLib/Reanimator.cpp', '''		int aItemSize = mDefinition->mTracks.count * sizeof(ReanimatorTrackInstance);
		FindGlobalAllocator(aItemSize)->Free(mTrackInstances, aItemSize);''', '''		int aItemSize = mDefinition->mTracks.count * sizeof(ReanimatorTrackInstance);
		if (Nano7PlainAlloc()) free(mTrackInstances); else  // nano7: pools never give memory back
		FindGlobalAllocator(aItemSize)->Free(mTrackInstances, aItemSize);''')
edit('PvzpLib/Reanimator.cpp', '''		mTrackInstances = (ReanimatorTrackInstance*)FindGlobalAllocator(aItemSize)->Calloc(aItemSize);''', '''		mTrackInstances = (ReanimatorTrackInstance*)(Nano7PlainAlloc() ? calloc(1, aItemSize) : FindGlobalAllocator(aItemSize)->Calloc(aItemSize));''')
edit('Lawn/System/SaveGame.cpp', '''			theReanimation->mTrackInstances = reinterpret_cast<ReanimatorTrackInstance*>(
				FindGlobalAllocator(aCount * sizeof(ReanimatorTrackInstance))->Calloc(aCount * sizeof(ReanimatorTrackInstance)));''', '''			theReanimation->mTrackInstances = reinterpret_cast<ReanimatorTrackInstance*>(Nano7PlainAlloc() ? calloc(aCount, sizeof(ReanimatorTrackInstance)) :
				FindGlobalAllocator(aCount * sizeof(ReanimatorTrackInstance))->Calloc(aCount * sizeof(ReanimatorTrackInstance)));''')
edit('Lawn/System/SaveGame.cpp', '''			if (aAllocator == nullptr || !aAllocator->IsPointerFromAllocator(theReanimation->mTrackInstances) || aAllocator->IsPointerOnFreeList(theReanimation->mTrackInstances))''', '''			if (Nano7PlainAlloc() ? theReanimation->mTrackInstances == nullptr : (aAllocator == nullptr || !aAllocator->IsPointerFromAllocator(theReanimation->mTrackInstances) || aAllocator->IsPointerOnFreeList(theReanimation->mTrackInstances)))''')
edit('Lawn/System/SaveGame.cpp', '''			theReanimation->mTrackInstances = (ReanimatorTrackInstance*)FindGlobalAllocator(aSize)->Calloc(aSize);''', '''			theReanimation->mTrackInstances = (ReanimatorTrackInstance*)(Nano7PlainAlloc() ? calloc(1, aSize) : FindGlobalAllocator(aSize)->Calloc(aSize));''')
edit('Lawn/System/ReanimationLawn.cpp', '''std::unique_ptr<MemoryImage> ReanimatorCache::MakeBlankMemoryImage(int theWidth, int theHeight)
{''', '''#include "../../../nano7/memtrack.h"
std::unique_ptr<MemoryImage> ReanimatorCache::MakeBlankMemoryImage(int theWidth, int theHeight)
{
	MT_TAG(MT_PIXELS);  // nano7: cached plant/mower frames are pixel buffers''')
# pass 11 (M1): per-level freeing also when the award screen opens; lazy texture reloads are upload buffers
edit('LawnApp.cpp', '''		ProcessSafeDeleteList();  // nano7kit: release the old board's resources before the next screen loads its own''', '''		ProcessSafeDeleteList();  // nano7kit: release the old board's resources before the next screen loads its own
		Nano7LevelFree(this);''')
edit('SexyAppFramework/graphics/MemoryImage.cpp', '''uint32_t* MemoryImage::GetBits()
{
	MT_TAG(MT_PIXELS);''', '''uint32_t* MemoryImage::GetBits()
{
	// nano7: a lazy image decoded again only to build its texture is an upload buffer; the device streams
	// the 16-bit .n7t in strips instead (counted as a fixed strip buffer). Bits that outlive the upload are
	// added back by the measuring bot.
	static const bool sLazy = getenv("NANO7_LAZY_IMAGES") != nullptr;
	MT_TAG(sLazy && mBits == nullptr && mPurgeBits && !mFilePath.empty() && mColorTable == nullptr ? MT_DECODE : MT_PIXELS);''')
edit('SexyAppFramework/SexyAppBase.cpp', '''	anImage->SetBits(aLoadedImage->GetBits(), aLoadedImage->GetWidth(), aLoadedImage->GetHeight(), commitBits);''', '''	{
		// nano7: with lazy images these bits are dropped a few lines below; the device only reads the .n7t header here
		static const bool sLazyTag = getenv("NANO7_LAZY_IMAGES") != nullptr;
		MT_TAG(sLazyTag && ImageLib::gAlphaComposeColor == 0xFFFFFF ? MT_DECODE : MT_PIXELS);
		anImage->SetBits(aLoadedImage->GetBits(), aLoadedImage->GetWidth(), aLoadedImage->GetHeight(), commitBits);
	}''')
# pass 12 (measurement): pixel scopes keep an outer "decode" tag (load-time copies that lazy images drop at once);
# desktop texture conversion buffers are decode buffers too (the device uploads .n7t data as is)
for _f in ('SexyAppFramework/graphics/MemoryImage.cpp', 'SexyAppFramework/graphics/GLImage.cpp'):
    _p = os.path.join(S, _f); _s = open(_p).read()
    _s2 = _s.replace('\tMT_TAG(MT_PIXELS);\n', '\tMT_TAG(mt_tag == MT_DECODE ? MT_DECODE : MT_PIXELS);\n')
    if _s2 != _s: open(_p, 'w').write(_s2)
edit('SexyAppFramework/graphics/GLInterface.cpp', '''void TextureData::CreateTextures(MemoryImage *theImage)
{
	MT_TAG(MT_PIXELS);''', '''void TextureData::CreateTextures(MemoryImage *theImage)
{
	static const bool sLazyTex = getenv("NANO7_LAZY_IMAGES") != nullptr;
	MT_TAG(sLazyTex ? MT_DECODE : MT_PIXELS);  // nano7: conversion buffers; the device uploads .n7t data directly''')
edit('SexyAppFramework/graphics/MemoryImage.cpp', '''	MT_TAG(sLazy && mBits == nullptr && mPurgeBits && !mFilePath.empty() && mColorTable == nullptr ? MT_DECODE : MT_PIXELS);''', '''	(void)sLazy;  // only texture creation (which sets the decode tag itself) counts as an upload buffer
	MT_TAG(mt_tag == MT_DECODE ? MT_DECODE : MT_PIXELS);''')
edit('SexyAppFramework/misc/ResourceManager.cpp', '''	if (aGLImage->mPurgeBits)
		aGLImage->PurgeBits();
	// nano7: keep only the size''', '''	// nano7: under lazy images the bits may already be gone; PurgeBits would decode them again just to drop them
	if (aGLImage->mPurgeBits && !(getenv("NANO7_LAZY_IMAGES") && aGLImage->mBits == nullptr && !aGLImage->mFilePath.empty()))
		aGLImage->PurgeBits();
	// nano7: keep only the size''')
edit('Lawn/Board.cpp', '''void Board::StartLevel()
{
	mCoinBankFadeCount = 0;''', '''void Board::StartLevel()
{
	Nano7FreeUnusedDefs(mApp);  // nano7: the intro animations are done with
	mCoinBankFadeCount = 0;''')
edit('PvzpLib/Reanimator.cpp', '''	if (gNano7CompactReanim) return;  // measuring build: compacted definitions live until exit
	if (gNano7N7R) { Nano7FreeN7R(theDefinition); return; }''', '''	if (gNano7N7R) { Nano7FreeN7R(theDefinition); return; }  // nano7: .n7r definitions are freed for real
	if (gNano7CompactReanim) return;  // measuring build: compacted definitions live until exit''')
