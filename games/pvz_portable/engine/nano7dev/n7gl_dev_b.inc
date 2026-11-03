// nano7 PvZ device renderer, part 2 (texture bands, start-up, frame end): included at the end of GLInterface.cpp.
// the one texture upload buffer: 64 KB plus 8 KB because the iPod's whole-file read may fill a sector past the end
#ifdef N7_HOST_ENV
extern "C" void n7_texname_note(N7GLuint, const char*);   // dry run: which image each texture is (n7glcommon.c)
#endif
static uint8_t* sN7Upload;
static const uint32_t N7_UPLOAD = 65536, N7_SLACK = 8192;
uint8_t* Nano7UploadBuffer()
{
	if (!sN7Upload) { sN7Upload = (uint8_t*)malloc(N7_UPLOAD + N7_SLACK); n7_stats.upload_buf = N7_UPLOAD + N7_SLACK; }
	return sN7Upload;
}
// trim class of an image for the triangle budget: 0 particles, 1 shadows, 2 small animation parts, 3 keep
static int Nano7TrimClass(const std::string& p, int W, int H)
{
	if (p.compare(0, 10, "particles/") == 0) return 0;
	if (p.find("shadow") != std::string::npos) return 1;
	if (p.compare(0, 11, "nano7bakez/") == 0) return 3;   // a baked zombie (after particles, shadows, small parts)
	if (p.compare(0, 7, "reanim/") == 0) {   // the sun and coins are protected; plants are baked (class 4)
		if (p.compare(0, 10, "reanim/sun") == 0 && p.size() > 10 && p[10] >= '0' && p[10] <= '9') return 4;
		if (p.compare(0, 11, "reanim/coin") == 0 || p.compare(0, 14, "reanim/diamond") == 0) return 4;
		return W * H <= 24 * 24 ? 2 : 3;   // logical 800x600 pixels: leaf tips, hands, hair
	}
	return 4;   // images/ (seed bank, projectiles, cursor, UI), fonts, the lawn, nano7bake/ (baked plants and mowers)
}
static int N7Pot(int v) { int p = 1; while (p < v) p <<= 1; return p; }
std::string Nano7TexPath(const std::string& theName);   // n7engine.cpp: data-dir path of an image's .n7t, lower case

// .n7t image -> GL textures in full-width horizontal bands of at most 64 KB each. The repacker stores band i>0 in
// "<name>.n7t.<i>" with the same rule, so every band is one whole-file read into the upload buffer.
void Nano7DevCreateTextures(TextureData* td, MemoryImage* img)
{
	GfxFlush();
	gAppliedTextureValid = false;
	td->ReleaseTextures();
	int W = img->mWidth, H = img->mHeight, TW = img->mN7TW, TH = img->mN7TH, fmt = img->mN7Fmt;
	int TWp = N7Pot(TW);
	int R = 1; while (R * 2 * TWp <= 32768) R *= 2;   // rows per band: a power of two, so POT padding stays in 64 KB
	if (R > TH) R = TH;
	int nb = (TH + R - 1) / R;
	int LH = nb == 1 ? H : (int)((double)R * H / TH);
	if (LH < 1) LH = 1;
	uint8_t* buf = Nano7UploadBuffer();
	if (!buf) { n7_fatal("no memory for the texture upload buffer"); return; }
	std::string path = Nano7TexPath(img->mFilePath);
	N7GLenum glfmt = fmt == 1 ? N7_GL_RGB : N7_GL_RGBA;
	N7GLenum gltype = fmt == 1 ? N7_GL_UNSIGNED_SHORT_565 : fmt == 2 ? N7_GL_UNSIGNED_SHORT_5551 : N7_GL_UNSIGNED_SHORT_4444;
	int cls = Nano7TrimClass(path, W, H);
	td->mTextures.resize(nb);
	td->mTexMemSize = 0;
	for (int i = 0; i < nb; i++)
	{
		int rows = std::min(R, TH - i * R);
		int head = i == 0 ? 16 : 0;
		uint32_t want = (uint32_t)TW * rows * 2 + head;
		std::string p = i == 0 ? path : path + "." + std::to_string(i);
		int got;
		if (img->mN7Pixels == (const uint16_t*)buf && nb == 1) { got = (int)want; head = 0; }   // baked, already here
		else if (img->mN7Pixels) { memcpy(buf + head, (const uint8_t*)img->mN7Pixels + (size_t)i * R * TW * 2, want - head); got = (int)want; }
		else got = n7p_read_into(p.c_str(), buf, N7_UPLOAD + head);
		if (got < (int)want) { char m[160]; snprintf(m, sizeof m, "nano7 texture %s: %d of %u bytes", p.c_str(), got, (unsigned)want); n7p_log(m); n7_stats.tex_fail++; memset(buf + (got > 0 ? got : 0), 0, want - (got > 0 ? got : 0)); }
		if (head) memmove(buf, buf + head, want - head);
		int aw = gNano7Npot ? (TW + 1) & ~1 : TWp, ah = gNano7Npot ? rows : N7Pot(rows);
		if ((uint32_t)aw * ah * 2 > N7_UPLOAD) { aw = TWp; ah = N7Pot(rows); }
		uint16_t* px = (uint16_t*)buf;
		if (aw != TW)
			for (int r = rows - 1; r >= 0; r--)
			{
				memmove(px + (size_t)r * aw, px + (size_t)r * TW, (size_t)TW * 2);
				for (int c = TW; c < aw; c++) px[(size_t)r * aw + c] = px[(size_t)r * aw + TW - 1];   // repeat the edge texel
			}
		for (int r = rows; r < ah; r++) memcpy(px + (size_t)r * aw, px + (size_t)(rows - 1) * aw, (size_t)aw * 2);
		TextureDataPiece& piece = td->mTextures[i];
		n7gl1_GenTextures(1, &piece.mTexture);
		n7gl1_BindTexture(N7_GL_TEXTURE_2D, piece.mTexture);
		n7gl1_TexParameteri(N7_GL_TEXTURE_2D, N7_GL_TEXTURE_WRAP_S, N7_GL_CLAMP_TO_EDGE);
		n7gl1_TexParameteri(N7_GL_TEXTURE_2D, N7_GL_TEXTURE_WRAP_T, N7_GL_CLAMP_TO_EDGE);
		n7gl1_TexImage2D(N7_GL_TEXTURE_2D, 0, glfmt, aw, ah, 0, glfmt, gltype, buf);
		n7_texscale_set(piece.mTexture, (float)TW / aw, (float)rows / ah);
		n7_texclass_set(piece.mTexture, cls);
#ifdef N7_HOST_ENV
		n7_texname_note(piece.mTexture, img->mFilePath.c_str());
#endif
		piece.mWidth = W;
		piece.mHeight = i < nb - 1 ? LH : H - (nb - 1) * LH;
		td->mTexMemSize += aw * ah * 2;
		n7_stats.tex_pieces++;
	}
	if (img->mN7Pixels && img->mN7PixelsOwned) { free((void*)img->mN7Pixels); img->mN7Pixels = nullptr; img->mN7PixelsOwned = false; }   // now a texture
	td->mTexPieceWidth = W; td->mTexPieceHeight = LH;
	td->mTexVecWidth = 1; td->mTexVecHeight = nb;
	td->mMaxTotalU = 1.0f; td->mMaxTotalV = (float)H / LH;
	td->mWidth = W; td->mHeight = H;
	td->mBitsChangedCount = img->mBitsChangedCount;
	td->mPixelFormat = fmt == 1 ? PixelFormat_R5G6B5 : PixelFormat_A4R4G4B4;
	td->mImageFlags = img->mRenderFlags & RenderImageFlag_TextureMask;
	gAppliedTextureValid = false;
}

static void N7ProbeNpot()
{
	const char* ext = (const char*)n7gl1_GetString(GL_EXTENSIONS);
	bool extNpot = ext && (strstr(ext, "npot") || strstr(ext, "NPOT"));
	while (n7gl1_GetError()) {}
	N7GLuint t = 0; uint16_t px[12 * 6] = { 0 };
	n7gl1_GenTextures(1, &t); n7gl1_BindTexture(N7_GL_TEXTURE_2D, t);
	n7gl1_TexImage2D(N7_GL_TEXTURE_2D, 0, N7_GL_RGBA, 12, 6, 0, N7_GL_RGBA, N7_GL_UNSIGNED_SHORT_4444, px);
	N7GLenum err = n7gl1_GetError();
	n7gl1_DeleteTextures(1, &t);
	gNano7Npot = (extNpot || err == 0) ? 1 : 0;
	if (n7p_exists_rel("pot.txt")) gNano7Npot = 0;     // override files in the data folder
	if (n7p_exists_rel("npot.txt")) gNano7Npot = 1;
	char m[200]; snprintf(m, sizeof m, "nano7 GL: %s / %s, npot ext %d, 12x6 upload error 0x%x -> %s textures",
		(const char*)n7gl1_GetString(GL_RENDERER), (const char*)n7gl1_GetString(GL_VERSION), (int)extNpot, (unsigned)err, gNano7Npot ? "NPOT" : "POT");
	n7p_log(m);
}

void GLInterface::UpdateViewport()
{
	GfxFlush();
	N7Layout(mWidth, mHeight);
	n7gl1_Viewport(0, 0, n7_panel_w, n7_panel_h);
	N7Projection(mWidth, mHeight);
	N7ScissorGame(true);
	mPresentationRect = Rect(0, 0, mWidth, mHeight);   // touches arrive already in game coordinates
}

int GLInterface::Init(bool IsWindowed)
{
	GfxFlush();
	static bool inited = false;
	if (!inited)
	{
		inited = true;
		PlatformGLInit();   // the engine's loader: GLES2 names map onto n7gl1 (nano7_gl_proc)
		N7ProbeNpot();
	}
	int aMaxSize = 0;
	n7gl1_GetIntegerv(GL_MAX_TEXTURE_SIZE, &aMaxSize);
	if (aMaxSize < 256) aMaxSize = 1024;
	MAX_TEXTURE_SIZE = aMaxSize;
	gTextureSizeMustBePow2 = !gNano7Npot;
	gMinTextureWidth = 8; gMinTextureHeight = 8;
	gMaxTextureWidth = aMaxSize; gMaxTextureHeight = aMaxSize;
	gSupportedPixelFormats = PixelFormat_A4R4G4B4 | PixelFormat_R5G6B5;   // 16-bit only
	gLinearFilter = false;
	gDrawState = {};
	gAppliedStateValid = false;
	gAppliedTextureValid = false;
	n7gl1_Disable(GL_DEPTH_TEST);
	n7gl1_Disable(GL_CULL_FACE);
	n7gl1_Disable(GL_DITHER);
	n7gl1_Enable(GL_BLEND);
	n7gl1_TexEnvi(N7_GL_TEXTURE_ENV, N7_GL_TEXTURE_ENV_MODE, N7_GL_MODULATE);
	n7gl1_EnableClientState(N7_GL_VERTEX_ARRAY);
	n7gl1_EnableClientState(N7_GL_COLOR_ARRAY);
	n7gl1_MatrixMode(N7_GL_TEXTURE); n7gl1_LoadIdentity();
	n7gl1_ClearColor(0, 0, 0, 1);
	UpdateViewport();
	N7ScissorGame(false); n7gl1_Clear(GL_COLOR_BUFFER_BIT); N7ScissorGame(true);
	mRGBBits = 32;
	mRedBits = 8; mGreenBits = 8; mBlueBits = 8;
	mRedShift = 0; mGreenShift = 8; mBlueShift = 16;
	mRedMask = 0xFFu << mRedShift;
	mGreenMask = 0xFFu << mGreenShift;
	mBlueMask = 0xFFu << mBlueShift;
	SetVideoOnlyDraw(false);
	return 1;
}

void Nano7HudDraw(Graphics* g);   // n7engine.cpp
void Nano7CalibrateDraw(Graphics* g);   // n7main.cpp
void Nano7BakeFrameEnd();               // nano7/n7bake.cpp
void GfxFlushForBake() { GfxFlush(); gAppliedTextureValid = false; }
bool GLInterface::PreDraw()
{
	SetMixedBlendMode(false);
	SetBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
	return true;
}
void GLInterface::Flush()
{
	GfxFlush();
	N7ScissorGame(false);
	{ Graphics g(mScreenImage.get()); g.mClipRect = Rect(-400, -200, 1600, 1000); Nano7HudDraw(&g); Nano7CalibrateDraw(&g); }
	Nano7BakeFrameEnd();
	GfxFlush();
	N7ScissorGame(true);
	n7p_present();   // the iPod shows the frame when the frame callback returns; the dry run takes its picture after it
}
// one OS frame callback = one full picture: clear the whole panel first (the strip beside the game too), and the
// triangle and draw counters cover the whole callback (n7main.cpp calls these around Nano7Step)
void Nano7FrameBegin()
{
	GfxFlush();
	N7ScissorGame(false); n7gl1_Clear(GL_COLOR_BUFFER_BIT); N7ScissorGame(true);
}
void Nano7FrameEnd()
{
	GfxFlush();
	n7_gl_frame_end();
}

bool GLInterface::RecoverBits(MemoryImage*) { return false; }   // no framebuffer objects on the nano
