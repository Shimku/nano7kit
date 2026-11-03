/* nano7 PvZ device: there is no window, only the panel the OS hands the app each frame. */
#include "SexyAppBase.h"
#include "graphics/GLInterface.h"
#include "graphics/GLImage.h"
#include "graphics/GLPlatform.h"
#include "widget/WidgetManager.h"
#include <memory>
using namespace Sexy;
void SexyAppBase::MakeWindow()
{
	if (!mWindow) { mWindow = (void*)1; mContext = (void*)1; PlatformGLInit(); }
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
