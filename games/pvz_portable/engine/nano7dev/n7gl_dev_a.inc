// nano7 PvZ device renderer: included by GLInterface.cpp when NANO7_DEVICE is set. The nano's GPU is OpenGL ES 1.1
// (fixed function), so the engine's one GLSL shader becomes client arrays + texture combiner MODULATE; the shader's
// "mixed" mode (normal and additive triangles in one batch, told apart by vertex z) is drawn as runs with two blend
// functions. The 800x600 game is drawn turned a quarter turn into the 240x432 portrait panel. It fills the panel
// (432x240 when held sideways: 0.54 across, 0.40 down, so 1.35 times wider than drawn); letterbox.txt in the data
// folder gives the older letterboxed picture back (0.4 both ways, 320x240 with bars).
#include "n7gl1.h"
#include "n7dev.h"
#define N7_GL_TEXTURE_2D 0x0DE1
#define N7_GL_VERTEX_ARRAY 0x8074
#define N7_GL_COLOR_ARRAY 0x8076
#define N7_GL_TEXTURE_COORD_ARRAY 0x8078
#define N7_GL_PROJECTION 0x1701
#define N7_GL_MODELVIEW 0x1700
#define N7_GL_TEXTURE 0x1702
#define N7_GL_SCISSOR_TEST 0x0C11
#define N7_GL_TEXTURE_ENV 0x2300
#define N7_GL_TEXTURE_ENV_MODE 0x2200
#define N7_GL_MODULATE 0x2100
#define N7_GL_CLAMP_TO_EDGE 0x812F
#define N7_GL_TEXTURE_WRAP_S 0x2802
#define N7_GL_TEXTURE_WRAP_T 0x2803
#define N7_GL_LINEAR 0x2601
#define N7_GL_UNSIGNED_SHORT_565 0x8363
#define N7_GL_UNSIGNED_SHORT_5551 0x8034
#define N7_GL_UNSIGNED_SHORT_4444 0x8033
#define N7_GL_RGB 0x1907
#define N7_GL_RGBA 0x1908

int n7_panel_w = 240, n7_panel_h = 432;
static int sN7GameX, sN7GameY, sN7GameW, sN7GameH;   // the game's rectangle on the panel (panel pixels, y down)
static float sN7Scale = 0.4f, sN7ScaleX = 0.4f, sN7ScaleY = 0.4f;   // X: along the game's x (the panel's long side)
int gNano7Npot = 1;                                  // NPOT textures allowed (probed at start)
static bool sN7TexOn = false;

static void N7Layout(int gw, int gh)
{
	static int sLetterbox = -1;
	if (sLetterbox < 0) sLetterbox = n7p_exists_rel("letterbox.txt") ? 1 : 0;
	float kx = (float)n7_panel_w / gh, ky = (float)n7_panel_h / gw;
	sN7Scale = kx < ky ? kx : ky;
	if (sLetterbox) { sN7ScaleX = sN7ScaleY = sN7Scale; }
	else { sN7ScaleX = ky; sN7ScaleY = kx; }   // fill: game x along the panel's 432, game y across its 240
	sN7GameW = (int)(gh * sN7ScaleY + 0.5f); sN7GameH = (int)(gw * sN7ScaleX + 0.5f);
	sN7GameX = (n7_panel_w - sN7GameW) / 2; sN7GameY = (n7_panel_h - sN7GameH) / 2;
}
// logical (gx, gy) -> panel: px = x0 + (gh - gy) * ky, py = y0 + gx * kx  (the iPod is held with its Home button on the right)
static void N7Projection(int gw, int gh)
{
	float W = (float)n7_panel_w, H = (float)n7_panel_h;
	float m[16] = { 0 };
	m[1] = -2.0f * sN7ScaleX / H;            // gx -> ndc y
	m[4] = -2.0f * sN7ScaleY / W;            // gy -> ndc x
	m[10] = 1.0f;
	m[12] = 2.0f * (sN7GameX + gh * sN7ScaleY) / W - 1.0f;
	m[13] = 1.0f - 2.0f * sN7GameY / H;
	m[15] = 1.0f;
	n7gl1_MatrixMode(N7_GL_PROJECTION); n7gl1_LoadMatrixf(m);
	n7gl1_MatrixMode(N7_GL_MODELVIEW); n7gl1_LoadIdentity();
}
void Nano7PanelToGame(int px, int py, int* gx, int* gy)   // touch: panel pixels -> logical 800x600
{
	*gx = (int)((py - sN7GameY) / sN7ScaleX);
	*gy = (int)(600 - (px - sN7GameX) / sN7ScaleY);
}
static void N7ScissorGame(bool on)
{
	if (on) { n7gl1_Scissor(sN7GameX, n7_panel_h - sN7GameY - sN7GameH, sN7GameW, sN7GameH); n7gl1_Enable(N7_GL_SCISSOR_TEST); }
	else n7gl1_Disable(N7_GL_SCISSOR_TEST);
}

static void GfxApplyState(const GfxDrawState& state)
{
	if (!gAppliedStateValid || gAppliedState.blendSrc != state.blendSrc || gAppliedState.blendDst != state.blendDst)
		n7gl1_BlendFunc(state.blendSrc, state.blendDst);
	if (!gAppliedStateValid || gAppliedState.useTexture != state.useTexture)
	{
		if (state.useTexture) { n7gl1_Enable(N7_GL_TEXTURE_2D); n7gl1_EnableClientState(N7_GL_TEXTURE_COORD_ARRAY); }
		else { n7gl1_Disable(N7_GL_TEXTURE_2D); n7gl1_DisableClientState(N7_GL_TEXTURE_COORD_ARRAY); }
	}
	if (state.useTexture)
	{
		bool aTextureChanged = !gAppliedTextureValid || gAppliedState.texture != state.texture;
		if (aTextureChanged) { n7gl1_BindTexture(N7_GL_TEXTURE_2D, state.texture); n7_texscale_apply(state.texture); }
		if (aTextureChanged || gAppliedState.filter != state.filter)
		{
			n7gl1_TexParameteri(N7_GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, state.filter);
			n7gl1_TexParameteri(N7_GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, state.filter);
		}
		gAppliedState.texture = state.texture;
		gAppliedState.filter = state.filter;
		gAppliedTextureValid = true;
	}
	gAppliedState.blendSrc = state.blendSrc;
	gAppliedState.blendDst = state.blendDst;
	gAppliedState.useTexture = state.useTexture;
	gAppliedState.mixedBlend = state.mixedBlend;
	gAppliedStateValid = true;
}

static void GfxSubmit()
{
	if (gDrawCommands.empty()) return;
	const uint8_t* base = (const uint8_t*)gVertices.data();
	n7gl1_VertexPointer(2, GL_FLOAT, sizeof(GLVertex), base);
	n7gl1_ColorPointer(4, GL_UNSIGNED_BYTE, sizeof(GLVertex), base + sizeof(float) * 3);
	n7gl1_TexCoordPointer(2, GL_FLOAT, sizeof(GLVertex), base + sizeof(float) * 3 + sizeof(uint32_t));
	for (const auto& command : gDrawCommands)
	{
		GfxApplyState(command.state);
		uint32_t aTris = command.mode == GL_TRIANGLES ? command.count / 3 : (command.count >= 3 ? command.count - 2 : 0);
		if (!n7_tri_admit(command.state.useTexture ? command.state.texture : 0, aTris)) continue;   // over the frame's budget
		if (!command.state.mixedBlend)
		{
			n7gl1_DrawArrays(command.mode, command.first, command.count);
			continue;
		}
		// mixed: straight alpha for normal triangles, additive for the ones flagged with z = 1
		int step = command.mode == GL_TRIANGLES ? 3 : command.count;
		int i = command.first, end = command.first + command.count;
		while (i < end)
		{
			bool add = gVertices[i].sz != 0.0f;
			int j = i + step;
			while (step == 3 && j < end && (gVertices[j].sz != 0.0f) == add) j += step;
			n7gl1_BlendFunc(GL_SRC_ALPHA, add ? GL_ONE : GL_ONE_MINUS_SRC_ALPHA);
			n7gl1_DrawArrays(command.mode, i, j - i);
			i = j;
		}
		gAppliedStateValid = false;   // blend was changed behind the cache
	}
	gDrawCommands.clear();
	gNumVertices = 0;
}

void Nano7DevCreateTextures(TextureData* td, MemoryImage* img);
