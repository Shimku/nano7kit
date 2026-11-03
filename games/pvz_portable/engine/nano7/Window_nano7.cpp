/* nano7kit measurement: headless window, the GL context is the fake one in fakegl.c. */
#include <SDL.h>
#include <memory>
#include "SexyAppBase.h"
#include "graphics/GLInterface.h"
#include "graphics/GLImage.h"
#include "graphics/GLPlatform.h"
#include "widget/WidgetManager.h"
using namespace Sexy;
void SexyAppBase::MakeWindow()
{
	if (!mWindow)
	{
		SDL_Init(SDL_INIT_VIDEO);
		mWindow = (void*)SDL_CreateWindow(mTitle.c_str(), 0, 0, mWidth, mHeight, 0);
		mContext = (void*)1;
	}
	if (mGLInterface == nullptr)
	{
		mGLInterface = std::make_unique<GLInterface>(this);
		if (!InitGLInterface()) { mGLInterface = nullptr; return; }
	}
	mActive = true;
	mPhysMinimized = false;
	mMinimized = false;
	ReInitImages();
	mWidgetManager->mImage = mGLInterface->GetScreenImage();
	mWidgetManager->MarkAllDirty();
	mGLInterface->UpdateViewport();
	mWidgetManager->Resize(mScreenBounds, mGLInterface->mPresentationRect);
}
