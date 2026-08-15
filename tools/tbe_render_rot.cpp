// Rotated-listener variant of the study's headless TBE renderer.
//
// Adapted from the author's own immersive-formats-evaluation repo
// (pipeline/tbe/tbe_render.cpp, published there under CC BY 4.0;
// relicensed MIT here by the same author). Identical to that helper
// (same engine setup, same synchronous SpatDecoderQueue drive through
// getAudioMix with the audio device disabled) except that the listener
// rotation is taken from the command line instead of being fixed at
// (0, 0, 0). This exists for phase 4: capturing the oracle's decode at
// arbitrary listener orientations, so the native rotation math can be
// verified against the real SDK per orientation.
//
//   tbe_render_rot <in.raw> <out.raw> <inChannels> <sampleRate> <blockFrames>
//                  <yawDeg> <pitchDeg> <rollDeg>
//
// Angles follow the SDK's own documented convention (TBE_AudioEngine.h):
// degrees, yaw negative = left, pitch positive = up, roll negative = left.
//
// Build (needs the proprietary SDK, see docs/PROTOCOL.md; OPENTBE_ORACLE_DIR
// is the study's pipeline/tbe directory holding include/ and lib/):
//
//   clang++ -std=c++14 -arch x86_64 -O2 -I "$OPENTBE_ORACLE_DIR/include" \
//     tools/tbe_render_rot.cpp -L "$OPENTBE_ORACLE_DIR/lib" -lAudio360 \
//     -Wl,-rpath,"$OPENTBE_ORACLE_DIR/lib" -o bin/tbe_render_rot

#include "TBE_AudioEngine.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace TBE;

int main(int argc, char** argv) {
  if (argc < 9) {
    std::fprintf(stderr,
                 "usage: tbe_render_rot <in.raw> <out.raw> <inChannels> "
                 "<sampleRate> <blockFrames> <yawDeg> <pitchDeg> <rollDeg>\n");
    return 2;
  }
  const char* inPath = argv[1];
  const char* outPath = argv[2];
  const int inCh = std::atoi(argv[3]);
  const float sampleRate = (float)std::atof(argv[4]);
  const int block = std::atoi(argv[5]);
  const float yaw = (float)std::atof(argv[6]);
  const float pitch = (float)std::atof(argv[7]);
  const float roll = (float)std::atof(argv[8]);

  ChannelMap map;
  if (inCh == 10) {
    map = ChannelMap::TBE_8_2;
  } else if (inCh == 8) {
    map = ChannelMap::TBE_8;
  } else {
    std::fprintf(stderr, "unsupported channel count %d (expected 8 or 10)\n", inCh);
    return 2;
  }

  std::FILE* fi = std::fopen(inPath, "rb");
  if (!fi) { std::perror("open input"); return 1; }
  std::fseek(fi, 0, SEEK_END);
  const long bytes = std::ftell(fi);
  std::fseek(fi, 0, SEEK_SET);
  const size_t totalSamples = (size_t)(bytes / (long)sizeof(float));
  std::vector<float> in(totalSamples);
  if (std::fread(in.data(), sizeof(float), totalSamples, fi) != totalSamples) {
    std::fprintf(stderr, "short read on input\n"); std::fclose(fi); return 1;
  }
  std::fclose(fi);
  const long frames = (long)(totalSamples / (size_t)inCh);

  EngineInitSettings settings;
  settings.audioSettings.sampleRate = sampleRate;
  settings.audioSettings.bufferSize = block;
  settings.audioSettings.deviceType = AudioDeviceType::DISABLED;

  AudioEngine* engine = nullptr;
  if (TBE_CreateAudioEngine(engine, settings) != EngineError::OK || !engine) {
    std::fprintf(stderr, "failed to create audio engine\n"); return 1;
  }
  engine->setListenerRotation(yaw, pitch, roll);

  SpatDecoderQueue* queue = nullptr;
  if (engine->createSpatDecoderQueue(queue) != EngineError::OK || !queue) {
    std::fprintf(stderr, "failed to create spat decoder queue\n"); return 1;
  }

  engine->start();
  queue->play();

  std::FILE* fo = std::fopen(outPath, "wb");
  if (!fo) { std::perror("open output"); return 1; }

  std::vector<float> out((size_t)block * 2);
  long inPos = 0;
  long outFrames = 0;
  const long tail = (long)(sampleRate * 1.0f);
  const long target = frames + tail;

  while (outFrames < target) {
    const long freeFrames = (long)queue->getFreeSpaceInQueue(map) / inCh;
    if (freeFrames > 0) {
      long n = frames - inPos;
      if (n > freeFrames) n = freeFrames;
      if (n > 0) {
        queue->enqueueData(in.data() + (size_t)inPos * inCh, (int)(n * inCh), map);
        inPos += n;
      } else {
        long pad = freeFrames > block ? block : freeFrames;
        if (pad > 0) queue->enqueueSilence((int)(pad * inCh), map);
      }
    }

    if (engine->getAudioMix(out.data(), block * 2, 2) != EngineError::OK) {
      std::fprintf(stderr, "getAudioMix failed at frame %ld\n", outFrames);
      break;
    }
    long w = block;
    if (outFrames + w > target) w = target - outFrames;
    std::fwrite(out.data(), sizeof(float), (size_t)w * 2, fo);
    outFrames += w;
  }

  std::fclose(fo);
  TBE_DestroyAudioEngine(engine);
  std::fprintf(stderr, "rendered %ld frames (input %ld + %ld tail), ypr %.1f %.1f %.1f\n",
               outFrames, frames, tail, yaw, pitch, roll);
  return 0;
}
