# People (faces)

Lunelis finds the faces in your photos, groups the ones that look alike, and
learns who is who from the names you give. Name someone once and Lunelis
suggests them in your other photos; every photo with a **named** face gets a
`People > <name>` tag, which works in the Tag filter, search, smart albums
and XMP sidecars (darktable and Lightroom see it).

Everything runs on this PC. Two small free models from OpenCV (YuNet finds
faces, SFace recognises them; about 39 MB) are downloaded only when you turn
faces on, and each is checked against its known fingerprint. No photo or face
ever leaves the PC.

## Turning it on

**Settings > Library > Faces > Download and turn on.** Then either:

- **Find faces in the library...** - a background job (Jobs, `Ctrl+J`) that
  can pause, run only while the PC is idle or at night, like every job; or
- leave **Look for faces in new photos after each scan** ticked, and new
  photos are looked at as they arrive.

Faces are found in a 1600-pixel copy of each photo (a RAW's own embedded
preview), so it's quick: about 15 photos a second.

## The People page

Sidebar > Photos > **People**.

| Tab | What it's for |
|---|---|
| **People** | Everyone you've named, with how many photos they're in and how many faces wait for a yes. Open a person to see all their faces. |
| **To confirm** | Every "Ann?" suggestion, person by person. **Yes** names the face (and tags the photo); **No** takes the suggestion away and Lunelis never suggests that person for that face again. |
| **Unnamed** | Groups of faces that look alike but have no name yet. **Name this person...** names the whole group at once; take out faces that don't belong first (**Not in this group**). |
| **Strangers & not faces** | People you don't know (**Stranger** - their photos get People > Unknown) and faces marked **Not a face**. Neither is grouped or suggested. **Bring back** undoes either. |

### Correcting mistakes

Open a person (People tab, double-click), select faces (Ctrl / Shift +
click), then:

- **Not Ann** - the face isn't Ann: her tag comes off that photo, and Ann is
  never suggested for that face again.
- **Move to...** - it's someone else: pick a name or type a new one.
- **Stranger** - someone you don't know (see below).
- **Not a face** - it isn't a face at all.
- **Use as cover** - the face shown for the person on the People tab.
- **Rename...** - a typo, or a nickname. Renaming to a name that's already
  in use **merges** the two people.
- **Forget this person...** - their faces become unnamed again and the tag
  goes. The photos don't change.

Double-click any face to open its photo.

## Strangers

Shooting in a crowd or a public place, most faces belong to people you'll
never name. Mark them as **strangers**: the photo gets **People > Unknown**,
so you can find (or leave out) photos with strangers in them, and they're
never grouped or suggested as someone you know.

- One face: click it in the photo view > **Stranger**, or select it on the
  People page > **Stranger**.
- A whole unnamed group: Unnamed > **Strangers**.
- A whole shoot: name the people you know, select the photos in the library,
  then **Photo > Unnamed faces in these photos are strangers** - everyone
  left unnamed becomes a stranger. In the photo view the same is in a face's
  menu: **Everyone not named here is a stranger**.

Strangers show as a dotted grey box labelled "Stranger". Naming a stranger
later makes them a person again and takes People > Unknown off the photo if
nobody else there is a stranger. "Unknown" can't be used as a person's name.

## Faces in the photo view

Press **F** (or the **Faces** button) in the photo view to show a box around
every face, with the name under it:

- **solid** box, name in the accent colour - named;
- **dashed** amber box, "Ann?" - a suggestion waiting for a yes;
- **white** box, no name - nobody yet;
- **dotted** grey box, "Stranger" - someone you don't know.

**Click a box** for: *Yes, this is Ann* · *Name...* / *Rename...* · *Not Ann* ·
*Not a face* · *All photos of Ann*. **Ctrl + drag** draws a box around a
face Lunelis missed and asks who it is. A photo can have any number of faces.
The overlay stays on until you turn it off (it's remembered), and hides while
you edit.

## Naming by itself

By default a likely match is only suggested, so a wrong guess never reaches a
tag. **Settings > Library > Faces > Name a face by itself when it's at least
N % sure** names sure matches straight away (60 % by default once ticked);
fix any mistake on the People page. Scene tags have the same option.

## Good to know

- Only **named** faces tag photos. Suggestions, groups and ignored faces
  never write anything to a sidecar.
- Faces Lunelis found are kept with the catalog; the model files can be
  removed (Settings) without losing names or tags.
- Very small faces (under about 2.5 % of the photo's long side) are skipped:
  too few pixels to know who it is.
- Videos aren't looked at.
