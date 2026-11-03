/* nano7kit measurement: PNG/JPEG decode through stb_image from the pak. */
#pragma once
#include "memtrack.h"
#include "paklib/PakInterface.h"
#include <vector>
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
static ImageLib::Image* nano7_stb_load(const std::string& theFileName, bool isJpeg)
{
	PFILE* fp = p_fopen(theFileName.c_str(), "rb");
	if (!fp) return nullptr;
	std::vector<unsigned char> buf;
	{
		MT_TAG(MT_DECODE);
		p_fseek(fp, 0, SEEK_END); long n = p_ftell(fp); p_fseek(fp, 0, SEEK_SET);
		buf.resize(n > 0 ? n : 0);
		if (n > 0) p_fread(buf.data(), 1, n, fp);
	}
	p_fclose(fp);
	if (buf.size() < 4) return nullptr;
	bool isPng = buf[0] == 0x89 && buf[1] == 'P';
	bool isJpg = buf[0] == 0xFF && buf[1] == 0xD8;
	if ((isJpeg && !isJpg) || (!isJpeg && !isPng)) return nullptr;
	MT_TAG(MT_DECODE);  /* decoder temporaries: the nano reads pre-scaled 16-bit textures instead */
	int w, h, c;
	unsigned char* px = stbi_load_from_memory(buf.data(), (int)buf.size(), &w, &h, &c, 4);
	if (!px) return nullptr;
	uint32_t* bits = new uint32_t[(size_t)w * h];
	for (size_t i = 0, n = (size_t)w * h; i < n; i++) {
		unsigned char* p = px + i * 4;
		bits[i] = ((uint32_t)p[3] << 24) | ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2];
	}
	stbi_image_free(px);
	ImageLib::Image* img = new ImageLib::Image();
	img->mWidth = w; img->mHeight = h; img->mBits.reset(bits);
	return img;
}
