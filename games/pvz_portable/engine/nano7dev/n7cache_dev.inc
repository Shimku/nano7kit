// nano7 PvZ device: the reanimation cache draws straight to the screen. On the desktop a cached plant, mower or zombie is
// drawn once into a CPU pixel buffer and then blitted; on the nano the source images exist only as GL textures, so the
// same reanimation is drawn live at the same place and scale (more triangles, no pixel buffers). Included by
// ReanimationLawn.cpp when NANO7_DEVICE is set, in place of DrawCachedPlant / DrawCachedMower / DrawCachedZombie.
// Positions: a frame drawn at (px, py) inside the cached image, which itself lands at (x + ox * sx, y + oy * sy) scaled
// by (sx, sy), ends up at (x + (ox + px) * sx, y + (oy + py) * sy) with scale k * sx.
namespace {
struct N7At {
	Graphics G; float x, y, sx, sy, ox, oy;
	N7At(Graphics* g, float theX, float theY, float theOX, float theOY) : G(*g), x(theX), y(theY), sx(g->mScaleX), sy(g->mScaleY), ox(theOX), oy(theOY) {}
	float X(float px) const { return x + (ox + px) * sx; }
	float Y(float py) const { return y + (oy + py) * sy; }
	Graphics* K(float k) { G.mScaleX = sx * k; G.mScaleY = sy * k; return &G; }
};
}

void ReanimatorCache::DrawCachedPlant(Graphics* g, float thePosX, float thePosY, SeedType theSeedType, DrawVariation theDrawVariation)
{
	PVZP_ASSERT(theSeedType >= 0 && theSeedType < SeedType::NUM_SEED_TYPES);
	int aOffsetX, aOffsetY, aWidth, aHeight;
	GetPlantImageSize(theSeedType, aOffsetX, aOffsetY, aWidth, aHeight);
	N7At a(g, thePosX, thePosY, (float)aOffsetX, (float)aOffsetY);
	const PlantDefinition& aPlantDef = GetPlantDefinition(theSeedType);
	ReanimationType t = aPlantDef.mReanimationType;
	float px = (float)-aOffsetX, py = (float)-aOffsetY;
	if (theSeedType == SeedType::SEED_POTATOMINE)
	{
		px = (float)-static_cast<int>(aOffsetX - 12.0f); py = (float)-static_cast<int>(aOffsetY - 12.0f);
		DrawReanimatorFrame(a.K(0.85f), a.X(px), a.Y(py), t, "anim_armed", theDrawVariation);
		return;
	}
	if (theSeedType == SeedType::SEED_INSTANT_COFFEE)
	{
		px = (float)-static_cast<int>(aOffsetX - 12.0f); py = (float)-static_cast<int>(aOffsetY - 12.0f);
		DrawReanimatorFrame(a.K(0.8f), a.X(px), a.Y(py), t, "anim_idle", theDrawVariation);
		return;
	}
	Graphics* G = a.K(1.0f);
	if (theSeedType == SeedType::SEED_EXPLODE_O_NUT)
	{
		Color c(255, 64, 64);
		if (g->GetColorizeImages()) { Color o = g->GetColor(); c = Color(255 * o.mRed / 255, 64 * o.mGreen / 255, 64 * o.mBlue / 255, o.mAlpha); }
		G->SetColorizeImages(true); G->SetColor(c);
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_idle", theDrawVariation);
		return;
	}
	DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_idle", theDrawVariation);
	if (theSeedType == SeedType::SEED_PEASHOOTER || theSeedType == SeedType::SEED_SNOWPEA || theSeedType == SeedType::SEED_REPEATER ||
		theSeedType == SeedType::SEED_LEFTPEATER || theSeedType == SeedType::SEED_GATLINGPEA)
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_head_idle", theDrawVariation);
	else if (theSeedType == SeedType::SEED_SPLITPEA)
	{
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_head_idle", theDrawVariation);
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_splitpea_idle", theDrawVariation);
	}
	else if (theSeedType == SeedType::SEED_THREEPEATER)
	{
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_head_idle1", theDrawVariation);
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_head_idle3", theDrawVariation);
		DrawReanimatorFrame(G, a.X(px), a.Y(py), t, "anim_head_idle2", theDrawVariation);
	}
}

void ReanimatorCache::DrawCachedMower(Graphics* g, float thePosX, float thePosY, LawnMowerType theMowerType)
{
	PVZP_ASSERT(theMowerType >= 0 && theMowerType < LawnMowerType::NUM_MOWER_TYPES);
	N7At a(g, thePosX - 20.0f, thePosY, 0, 0);
	switch (theMowerType)
	{
	case LawnMowerType::LAWNMOWER_LAWN:  DrawReanimatorFrame(a.K(0.85f), a.X(10), a.Y(0), ReanimationType::REANIM_LAWNMOWER, "anim_normal", DrawVariation::VARIATION_NORMAL); break;
	case LawnMowerType::LAWNMOWER_POOL:  DrawReanimatorFrame(a.K(0.8f), a.X(10), a.Y(25), ReanimationType::REANIM_POOL_CLEANER, nullptr, DrawVariation::VARIATION_NORMAL); break;
	case LawnMowerType::LAWNMOWER_ROOF:  DrawReanimatorFrame(a.K(0.85f), a.X(10), a.Y(0), ReanimationType::REANIM_ROOF_CLEANER, nullptr, DrawVariation::VARIATION_NORMAL); break;
	case LawnMowerType::LAWNMOWER_SUPER_MOWER: DrawReanimatorFrame(a.K(0.85f), a.X(10), a.Y(0), ReanimationType::REANIM_LAWNMOWER, "anim_tricked", DrawVariation::VARIATION_NORMAL); break;
	default: break;
	}
}

void ReanimatorCache::DrawCachedZombie(Graphics* g, float thePosX, float thePosY, ZombieType theZombieType)
{
	PVZP_ASSERT(theZombieType >= 0 && theZombieType < ZombieType::NUM_CACHED_ZOMBIE_TYPES);
	N7At a(g, thePosX, thePosY, 0, 0);
	Graphics* G = a.K(1.0f);
	ZombieType aUseZombieType = theZombieType == ZombieType::ZOMBIE_CACHED_POLEVAULTER_WITH_POLE ? ZombieType::ZOMBIE_POLEVAULTER : theZombieType;
	const ZombieDefinition& aZombieDef = GetZombieDefinition(aUseZombieType);
	float aPosX = 40.0f, aPosY = 40.0f;
	if (aZombieDef.mReanimationType == ReanimationType::REANIM_ZOMBIE)
	{
		Reanimation aReanim;
		aReanim.ReanimationInitializeType(a.X(aPosX), a.Y(aPosY), aZombieDef.mReanimationType);
		aReanim.OverrideScale(a.sx, a.sy);
		aReanim.SetFramesForLayer("anim_idle");
		Zombie::SetupReanimLayers(&aReanim, aUseZombieType);
		if (theZombieType == ZombieType::ZOMBIE_DOOR)
			aReanim.AssignRenderGroupToTrack("anim_screendoor", RENDER_GROUP_NORMAL);
		else if (theZombieType == ZombieType::ZOMBIE_FLAG)
		{
			Reanimation aReanimFlag;
			aReanimFlag.ReanimationInitializeType(a.X(aPosX), a.Y(aPosY), ReanimationType::REANIM_FLAG);
			aReanimFlag.OverrideScale(a.sx, a.sy);
			aReanimFlag.SetFramesForLayer("Zombie_flag");
			aReanimFlag.Draw(G);
		}
		aReanim.Draw(G);
	}
	else if (aZombieDef.mReanimationType == ReanimationType::REANIM_BOSS)
	{
		Reanimation aReanim;
		aReanim.ReanimationInitializeType(a.X(-524.0f), a.Y(-88.0f), aZombieDef.mReanimationType);
		aReanim.OverrideScale(a.sx, a.sy);
		aReanim.SetFramesForLayer("anim_head_idle");
		Reanimation aReanimDriver;
		aReanimDriver.ReanimationInitializeType(a.X(46.0f), a.Y(22.0f), ReanimationType::REANIM_BOSS_DRIVER);
		aReanimDriver.OverrideScale(a.sx, a.sy);
		aReanimDriver.SetFramesForLayer("anim_idle");
		aReanim.Draw(G);
		aReanimDriver.Draw(G);
		aReanim.AssignRenderGroupToTrack("boss_body1", RENDER_GROUP_HIDDEN);
		aReanim.AssignRenderGroupToTrack("boss_neck", RENDER_GROUP_HIDDEN);
		aReanim.AssignRenderGroupToTrack("boss_head2", RENDER_GROUP_HIDDEN);
		aReanim.Draw(G);
	}
	else
	{
		const char* aTrackName = "anim_idle";
		if (theZombieType == ZombieType::ZOMBIE_POGO) aTrackName = "anim_pogo";
		else if (theZombieType == ZombieType::ZOMBIE_POLEVAULTER) aTrackName = "anim_walk";
		else if (theZombieType == ZombieType::ZOMBIE_GARGANTUAR) aPosY = 60.0f;
		DrawReanimatorFrame(G, a.X(aPosX), a.Y(aPosY), aZombieDef.mReanimationType, aTrackName, DrawVariation::VARIATION_NORMAL);
	}
}

// the frame-end half of a cache draw (n7bake.h): the same reanimation DrawReanimatorFrame sets up, baked, not drawn
void Nano7CacheBakeBuild(int theType, const char* theTrack, int theVariation)
{
	Reanimation aReanim;
	aReanim.ReanimationInitializeType(0.0f, 0.0f, (ReanimationType)theType);
	if (theTrack != nullptr && aReanim.TrackExists(theTrack))
		aReanim.SetFramesForLayer(theTrack);
	if ((ReanimationType)theType == ReanimationType::REANIM_KERNELPULT)
		aReanim.AssignRenderGroupToTrack("Cornpult_butter", RENDER_GROUP_HIDDEN);
	else if ((ReanimationType)theType == ReanimationType::REANIM_SUNFLOWER)
		aReanim.mAnimTime = 0.15f;
	aReanim.AssignRenderGroupToTrack("anim_waterline", RENDER_GROUP_HIDDEN);
	if ((DrawVariation)theVariation != DrawVariation::VARIATION_NORMAL)
		((LawnApp*)gSexyAppBase)->mReanimatorCache->UpdateReanimationForVariation(&aReanim, (DrawVariation)theVariation);
	Nano7BakeOnly(&aReanim);
}
