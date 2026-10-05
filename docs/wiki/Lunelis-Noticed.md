# Lunelis noticed

After each scan, Lunelis looks for shots that seem to have been taken to be
combined, and offers to build them. It never builds anything by itself.

## What it looks for

| It noticed | How it tells |
|---|---|
| **HDR bracket** | 3, 5, 7 or 9 shots within 2 seconds of each other, same lens and aperture, exposures spread over at least 1.5 stops - and the pictures line up |
| **Panorama** | 3 or more shots within 10 seconds, same focal length and exposure - and each one overlaps the next, moved along by 10-85 % of the frame |
| **Focus stack** | 3 or more shots within 3 seconds, same exposure, lined up - and the sharpest part of the picture moves from frame to frame |
| **Timelapse** | 20 or more shots at a steady interval (a second or more), same settings, lined up |
| **Star trails** | 10 or more night exposures of 4 seconds or longer at a steady interval, lined up |

The camera settings narrow it down; then the pictures themselves are compared
(on their thumbnails), so a burst of a moving subject or four unrelated shots
taken quickly aren't offered. A shot only counts towards one thing.

## Where the suggestions are

**Sidebar > Keep safe > Library status**, under **Lunelis noticed**. Each one
shows a few of its frames and, for example, *"14 frames look like a
panorama. Build it?"*:

- **Build the HDR... / Build the panorama...** opens the merge with those
  frames selected, as **Photo > Merge** would. Once the merge is saved the
  suggestion is done; cancel the merge and it stays on offer.
- **Make the timelapse...** opens Create's Timelapse tool with those frames.
- **Show photos** shows just those frames in the library, all selected.
- **Dismiss** takes it away. The same frames are never offered again.

- **Make the focus stack... / Make the star trails...** opens that Create tool
  with those frames.

## Turning it off

**Settings > Appearance:** untick *After each scan, look for brackets,
panoramas, focus stacks and timelapses*. Only photos added since the last
look are checked, so it costs little after the first time.
