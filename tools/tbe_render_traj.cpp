// Trajectory variant of the headless TBE renderer (adapted from the
// author's own immersive-formats-evaluation pipeline/tbe/tbe_render.cpp,
// CC BY 4.0 there, relicensed MIT here by the same author): the listener rotation
// changes mid-stream, for characterising how the SDK transitions between
// orientations (docs/PROTOCOL.md, dynamic rotation).
//
// The trajectory file is text, one update per line:
//
//   <outputFrame> <yawDeg> <pitchDeg> <rollDeg>
//
// Each update is applied (setListenerRotation) immediately before rendering
// the block in which the output frame counter reaches <outputFrame>.
// Rotation acts at render time, not enqueue time, so updates take effect
// without the input transport delay; the exact transition behaviour is what
// the phase 5 harness measures.
//
//   tbe_render_traj <in.raw> <out.raw> <inChannels> <sampleRate>
//                   <blockFrames> <trajectory.txt>
//
// Build: as tools/tbe_render_rot.cpp, same SDK, same flags, output
// bin/tbe_render_traj.

#include "TBE_AudioEngine.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace TBE;

struct Update {
  long frame;
  float yaw, pitch, roll;
};

int main(int argc, char** argv) {
  if (argc < 7) {
    std::fprintf(stderr,
                 "usage: tbe_render_traj <in.raw> <out.raw> <inChannels> "
                 "<sampleRate> <blockFrames> <trajectory.txt>\n");
    return 2;
  }
  const char* inPath = argv[1];
  const char* outPath = argv[2];
  const int inCh = std::atoi(argv[3]);
  const float sampleRate = (float)std::atof(argv[4]);
  const int block = std::atoi(argv[5]);
  const char* trajPath = argv[6];

  ChannelMap map;
  if (inCh == 10) {
    map = ChannelMap::TBE_8_2;
  } else if (inCh == 8) {
    map = ChannelMap::TBE_8;
  } else {
    std::fprintf(stderr, "unsupported channel count %d (expected 8 or 10)\n", inCh);
    return 2;
  }

  std::vector<Update> traj;
  {
    std::FILE* ft = std::fopen(trajPath, "r");
    if (!ft) { std::perror("open trajectory"); return 1; }
    Update u;
    while (std::fscanf(ft, "%ld %f %f %f", &u.frame, &u.yaw, &u.pitch,
                       &u.roll) == 4) {
      traj.push_back(u);
    }
    std::fclose(ft);
  }
  if (traj.empty()) {
    std::fprintf(stderr, "empty trajectory\n");
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

  SpatDecoderQueue* queue = nullptr;
  if (engine->createSpatDecoderQueue(queue) != EngineError::OK || !queue) {
    std::fprintf(stderr, "failed to create spat decoder queue\n"); return 1;
  }

  size_t next = 0;
  // apply any updates scheduled at or before frame 0 before starting
  while (next < traj.size() && traj[next].frame <= 0) {
    engine->setListenerRotation(traj[next].yaw, traj[next].pitch,
                                traj[next].roll);
    next++;
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
    while (next < traj.size() && traj[next].frame <= outFrames) {
      engine->setListenerRotation(traj[next].yaw, traj[next].pitch,
                                  traj[next].roll);
      next++;
    }

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
  std::fprintf(stderr, "rendered %ld frames, %zu of %zu rotation updates applied\n",
               outFrames, next, traj.size());
  return 0;
}
