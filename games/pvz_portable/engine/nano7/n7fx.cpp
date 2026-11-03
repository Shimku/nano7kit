// a global trim of cosmetic effects, by whitelist (stop fixing level by level).
// - Particle systems: none are made except the pick-up arrows (awards, coins, seed packets in the tutorial). Their
//   definitions are not even loaded at start-up (no arena memory, no textures). Hit splats, limbs, heads, cones,
//   buckets, newspapers falling off, smoke, dirt, sparkles, explosion clouds: gone. Explosions leave a 6-frame flash
//   (one filled rectangle, two triangles).
// - Animations (reanimations): every plant, zombie, sun, coin, mower, Crazy Dave, the menus and game-mode props are
//   drawn; effect animations (charred zombies, mowed-down zombies, splashes, puffs, dirt, Ready-Set-Plant, Final Wave,
//   the huge-wave letters' animation, jalapeno fire, rain, surprise, credits) keep running for the game's timing but
//   are never drawn, so their pictures are never loaded. Burned and mowed zombies vanish at once (Zombie.cpp).
// fx.txt in the data folder brings everything back (for comparison only).
#include "ConstEnums.h"
#include "Lawn/Plant.h"
#include "Lawn/Zombie.h"
#include "graphics/Graphics.h"
#include "n7fx.h"
#include "n7dev.h"
#include <cstdio>
#include <cstring>
using namespace Sexy;

static int sOn = -1;
bool Nano7FxOn()
{
	if (sOn < 0) sOn = n7p_exists_rel("fx.txt") ? 0 : 1;
	return sOn == 1;
}
static unsigned sRefused, sRefusedLevel, sHidden, sFlashes;

bool Nano7FxParticleAllowed(int e)
{
	if (!Nano7FxOn()) return true;
	switch (e) {
	case ParticleEffect::PARTICLE_AWARD_PICKUP_ARROW:
	case ParticleEffect::PARTICLE_COIN_PICKUP_ARROW:
	case ParticleEffect::PARTICLE_SEED_PACKET_PICK:      // the tutorial's "plant this" glow
	case ParticleEffect::PARTICLE_PERSENT_PICK_UP_ARROW:
		return true;
	default:
		return false;
	}
}

struct Flash { float x, y, w, h; int kind, left; };
static Flash sFl[8]; static int sFlN;
void Nano7FxFlash(float x, float y, float w, float h, int kind)
{
	if (sFlN >= 8) return;
	sFl[sFlN++] = { x, y, w, h, kind, 6 };
	sFlashes++;
}
void Nano7FxParticleRefused(int e, float x, float y)
{
	sRefused++; sRefusedLevel++;
	switch (e) {
	case ParticleEffect::PARTICLE_POWIE: case ParticleEffect::PARTICLE_JACKEXPLODE: case ParticleEffect::PARTICLE_POTATO_MINE:
	case ParticleEffect::PARTICLE_ZAMBONI_EXPLOSION: case ParticleEffect::PARTICLE_CATAPULT_EXPLOSION:
	case ParticleEffect::PARTICLE_BLASTMARK: case ParticleEffect::PARTICLE_BOSS_EXPLOSION:
		Nano7FxFlash(x - 90, y - 70, 180, 140, 2); break;
	case ParticleEffect::PARTICLE_DOOM:
		Nano7FxFlash(x - 220, y - 180, 440, 360, 0); break;
	case ParticleEffect::PARTICLE_ICE_TRAP:
		Nano7FxFlash(0, 0, 800, 600, 1); break;
	default: break;
	}
}

static signed char sShow[1024];
static bool Shown(int t)
{
	if (!Nano7FxOn() || t < 0 || t >= 1024) return true;
	if (!sShow[t]) {
		bool s = false;
		for (int i = 0; i < (int)SeedType::NUM_SEED_TYPES && !s; i++) if ((int)gPlantDefs[i].mReanimationType == t) s = true;
		for (int i = 0; i < (int)NUM_ZOMBIE_TYPES && !s; i++) if ((int)gZombieDefs[i].mReanimationType == t) s = true;
		switch ((ReanimationType)t) {
		case ReanimationType::REANIM_ZOMBIE: case ReanimationType::REANIM_SUN: case ReanimationType::REANIM_COIN_SILVER:
		case ReanimationType::REANIM_COIN_GOLD: case ReanimationType::REANIM_DIAMOND: case ReanimationType::REANIM_LAWNMOWER:
		case ReanimationType::REANIM_POOL_CLEANER: case ReanimationType::REANIM_ROOF_CLEANER: case ReanimationType::REANIM_CRAZY_DAVE:
		case ReanimationType::REANIM_SELECTOR_SCREEN: case ReanimationType::REANIM_LOADBAR_SPROUT:
		case ReanimationType::REANIM_LOADBAR_ZOMBIEHEAD: case ReanimationType::REANIM_SODROLL: case ReanimationType::REANIM_ZOMBIES_WON:
		case ReanimationType::REANIM_FLAG: case ReanimationType::REANIM_FIRE_PEA: case ReanimationType::REANIM_BOSS:
		case ReanimationType::REANIM_BOSS_DRIVER: case ReanimationType::REANIM_BOSS_ICEBALL: case ReanimationType::REANIM_BOSS_FIREBALL:
		case ReanimationType::REANIM_HAMMER: case ReanimationType::REANIM_SLOT_MACHINE_HANDLE: case ReanimationType::REANIM_PORTAL_CIRCLE:
		case ReanimationType::REANIM_PORTAL_SQUARE: case ReanimationType::REANIM_RAKE: case ReanimationType::REANIM_SLEEPING:
		case ReanimationType::REANIM_ZENGARDEN_SPROUT: case ReanimationType::REANIM_ZENGARDEN_WATERINGCAN:
		case ReanimationType::REANIM_ZENGARDEN_FERTILIZER: case ReanimationType::REANIM_ZENGARDEN_BUGSPRAY:
		case ReanimationType::REANIM_ZENGARDEN_PHONOGRAPH: case ReanimationType::REANIM_STINKY: case ReanimationType::REANIM_TREEOFWISDOM:
		case ReanimationType::REANIM_ZOMBATAR_HEAD:
			s = true; break;
		default: break;
		}
		sShow[t] = s ? 1 : -1;
	}
	return sShow[t] > 0;
}
bool Nano7FxReanimIsEffect(int t) { return !Shown(t); }
bool Nano7FxReanimShown(int t)
{
	if (Shown(t)) return true;
	sHidden++;
	return false;
}

void Nano7FxDraw(Graphics* g)
{
	if (!sFlN) return;
	static const Color kC[3] = { Color(255, 255, 255), Color(170, 210, 255), Color(255, 200, 90) };
	g->PushState();
	g->SetColorizeImages(false);
	int n = 0;
	for (int i = 0; i < sFlN; i++) {
		Flash& f = sFl[i];
		Color c = kC[f.kind < 0 || f.kind > 2 ? 0 : f.kind];
		c.mAlpha = 25 * f.left;   // 150 down to 25
		g->SetColor(c);
		g->FillRect((int)f.x, (int)f.y, (int)f.w, (int)f.h);
		if (--f.left > 0) sFl[n++] = f;
	}
	sFlN = n;
	g->PopState();
}

static unsigned sMissing;
void Nano7FxMissingImage()
{
	if (sMissing++ == 0) n7p_log("nano7 picture missing (its group was left out of the pack): drawn as nothing");
}

void Nano7FxLevelLog()
{
	char m[240];
	snprintf(m, sizeof m, "nano7 effects left out: %u particle systems this level (%u in all), %u effect animation draws skipped, %u flashes, %u missing-picture draws",
		sRefusedLevel, sRefused, sHidden, sFlashes, sMissing);
	n7p_log(m);
	sRefusedLevel = 0; sHidden = 0; sFlashes = 0;
}
