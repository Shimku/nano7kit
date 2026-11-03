// one switch for every cosmetic effect in the game (all worlds and modes), as a whitelist.
// Part of the nano7kit PvZ port; links against PvZ-Portable (LGPL-3.0-or-later, wszqkzqk).
#pragma once
namespace Sexy { class Graphics; }
bool Nano7FxParticleAllowed(int theEffect);        // false: the particle system is never made (callers get nullptr)
void Nano7FxParticleRefused(int theEffect, float theX, float theY);   // counts it; explosions leave a short flash
bool Nano7FxReanimShown(int theReanimType);
bool Nano7FxReanimIsEffect(int theReanimType);      // the same test, not counted (preloads skip these)        // false: that animation runs (game timing intact) but is never drawn
void Nano7FxFlash(float theX, float theY, float theW, float theH, int theKind);   // 0 white, 1 blue, 2 orange
void Nano7FxDraw(Sexy::Graphics* g);               // the flashes, last on the board
void Nano7FxLevelLog();                            // one line per level: what was left out
void Nano7FxMissingImage();                        // a draw of a pruned (null) picture; counted, logged once
bool Nano7FxOn();                                  // fx.txt in the data folder turns the trim off (all effects back)
