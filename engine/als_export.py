"""Ableton Live Set (.als) exporter.

Creates a minimal but valid Ableton Live 11+ project file containing
audio tracks with sample references. The .als format is gzipped XML.

Usage::

    from engine.als_export import export_als
    export_als(
        name="My Beat",
        tracks=[
            {"name": "Kick", "samples": ["/path/to/kick.wav"]},
            {"name": "Snare", "samples": ["/path/to/snare.wav"]},
        ],
        output_path="/output/My Beat.als",
        bpm=90.0,
    )

Note: Ableton will show "Samples Offline" until you use "Collect All
and Save" to embed the samples into the project folder. The .als file
references samples by relative path.
"""

import gzip
import logging
import os
import shutil
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

log = logging.getLogger(__name__)

# Ableton Live version identifiers
ALS_MAJOR = "5"
ALS_MINOR = "11.0.1"
ALS_SCHEMA = "19.5"
ALS_CREATOR = "Compa"


def export_als(name: str, tracks: list[dict], output_path: str,
               bpm: float = 120.0, time_sig: tuple[int, int] = (4, 4),
               copy_samples: bool = True) -> Optional[str]:
    """Export an Ableton Live Set (.als) with audio tracks.

    Args:
        name: Project/set name.
        tracks: List of track dicts, each with:
            - "name": Track display name
            - "samples": List of WAV file paths for clips on this track
            - "color" (optional): Track color index (0-69)
        output_path: Where to write the .als file.
        bpm: Project tempo.
        time_sig: (numerator, denominator) tuple.
        copy_samples: If True, copy WAV files next to the .als.

    Returns:
        Path to the .als file, or None on failure.
    """
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # Copy samples to project folder
        project_dir = os.path.splitext(output_path)[0] + " Project"
        samples_dir = os.path.join(project_dir, "Samples", "Imported")
        if copy_samples:
            os.makedirs(samples_dir, exist_ok=True)

        # Build XML
        xml = _build_als_xml(name, tracks, bpm, time_sig, samples_dir,
                             copy_samples)

        # Write gzipped
        with gzip.open(output_path, "wb") as f:
            f.write(xml.encode("utf-8"))

        log.info("Exported .als: %s (%d tracks)", output_path, len(tracks))
        return output_path

    except Exception as e:
        log.error("ALS export failed: %s", e)
        return None


def _build_als_xml(name: str, tracks: list[dict], bpm: float,
                   time_sig: tuple[int, int], samples_dir: str,
                   copy_samples: bool) -> str:
    """Build the Ableton Live Set XML string."""

    track_xml_parts = []
    for i, track in enumerate(tracks):
        track_name = track.get("name", f"Track {i + 1}")
        color = track.get("color", i % 70)
        samples = track.get("samples", [])
        track_xml = _build_audio_track(i, track_name, color, samples,
                                        samples_dir, copy_samples, bpm)
        track_xml_parts.append(track_xml)

    tracks_xml = "\n".join(track_xml_parts)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="{ALS_MAJOR}" MinorVersion="{ALS_MINOR}"
         SchemaChangeCount="{ALS_SCHEMA}" Creator="{ALS_CREATOR}"
         Revision="">
  <LiveSet>
    <NextPointeeId Value="1000" />
    <OverwriteProtectionNumber Value="2819" />
    <LomId Value="0" />
    <LomIdView Value="0" />
    <Tracks>
{tracks_xml}
    </Tracks>
    <MasterTrack>
      <LomId Value="0" />
      <LomIdView Value="0" />
      <Name>
        <EffectiveName Value="Master" />
        <UserName Value="" />
      </Name>
      <DeviceChain>
        <Mixer>
          <Volume>
            <Manual Value="0.0" />
          </Volume>
          <Tempo>
            <Manual Value="{bpm}" />
          </Tempo>
          <TimeSignature>
            <TimeSignatures>
              <RemoteableTimeSignature Id="0">
                <Numerator Value="{time_sig[0]}" />
                <Denominator Value="{time_sig[1]}" />
              </RemoteableTimeSignature>
            </TimeSignatures>
          </TimeSignature>
        </Mixer>
      </DeviceChain>
    </MasterTrack>
    <Transport>
      <PhaseNudgeTempo>
        <Manual Value="{bpm}" />
      </PhaseNudgeTempo>
    </Transport>
  </LiveSet>
</Ableton>"""

    return xml


def _build_audio_track(index: int, name: str, color: int,
                       samples: list[str], samples_dir: str,
                       copy_samples: bool, bpm: float) -> str:
    """Build XML for one audio track with clips."""

    clips_xml_parts = []
    for j, sample_path in enumerate(samples):
        if not os.path.isfile(sample_path):
            continue

        filename = os.path.basename(sample_path)
        stem = os.path.splitext(filename)[0]

        # Copy sample to project folder
        if copy_samples and samples_dir:
            dest = os.path.join(samples_dir, filename)
            if not os.path.exists(dest):
                try:
                    shutil.copy2(sample_path, dest)
                except Exception:
                    pass

        # Get sample info
        try:
            import soundfile as sf
            info = sf.info(sample_path)
            duration_secs = info.duration
            sample_rate = info.samplerate
            num_frames = info.frames
        except Exception:
            duration_secs = 1.0
            sample_rate = 44100
            num_frames = 44100

        # Duration in beats
        beats = duration_secs * (bpm / 60.0)
        end_time = max(1.0, beats)

        rel_path = f"Samples/Imported/{filename}"

        clip_xml = f"""
          <ClipSlot Id="{j}">
            <Value>
              <AudioClip Id="{index * 100 + j}" Time="0">
                <LomId Value="0" />
                <Name Value="{xml_escape(stem)}" />
                <ColorIndex Value="{color}" />
                <CurrentStart Value="0" />
                <CurrentEnd Value="{end_time}" />
                <Loop>
                  <LoopOn Value="false" />
                  <LoopStart Value="0" />
                  <LoopEnd Value="{end_time}" />
                </Loop>
                <SampleRef>
                  <FileRef>
                    <RelativePath Value="{xml_escape(rel_path)}" />
                    <Name Value="{xml_escape(filename)}" />
                    <Type Value="1" />
                  </FileRef>
                  <DefaultDuration Value="{num_frames}" />
                  <DefaultSampleRate Value="{sample_rate}" />
                </SampleRef>
              </AudioClip>
            </Value>
          </ClipSlot>"""
        clips_xml_parts.append(clip_xml)

    clips_xml = "\n".join(clips_xml_parts) if clips_xml_parts else ""

    return f"""      <AudioTrack Id="{index}">
        <LomId Value="0" />
        <LomIdView Value="0" />
        <Name>
          <EffectiveName Value="{xml_escape(name)}" />
          <UserName Value="{xml_escape(name)}" />
        </Name>
        <ColorIndex Value="{color}" />
        <DeviceChain>
          <MainSequencer>
            <ClipSlotList>
{clips_xml}
            </ClipSlotList>
          </MainSequencer>
          <Mixer>
            <Volume>
              <Manual Value="0.0" />
            </Volume>
            <Pan>
              <Manual Value="0" />
            </Pan>
          </Mixer>
        </DeviceChain>
      </AudioTrack>"""
