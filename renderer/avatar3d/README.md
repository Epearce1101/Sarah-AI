# Sarah's 3D Avatar (VRM + Mixamo)

An anime-style 3D Sarah with a talking face and full-body animations, all free:

- **Model:** any VRM file (make your own in [VRoid Studio](https://vroid.com/en/studio)).
- **Animations:** from [Mixamo](https://www.mixamo.com) (free with an Adobe ID). They're
  fitted to Sarah's body automatically when the page loads, so there's no Blender step.
- **Face:** her expression follows the `emotion` from `/api/chat`, her mouth moves with her
  TTS voice, and she blinks and looks at you on her own. Hair and clothes swing with physics.

## Quick start

1. Install the libraries (once): `npm install`
2. Get the free sample model (until you make your own):
   `python renderer/avatar3d/download_sample_model.py`
3. Start Sarah's backend as usual (`server.py`), then open:
   **http://127.0.0.1:8907/avatar/?dev=1**

`?dev=1` shows a test panel (press **D** to toggle it) with buttons for every emotion and
animation, a mouth test and a chat box. With no animation files yet she stands in a
built-in breathing pose.

## Your own Sarah

In VRoid Studio: design her, then **Export → Export as VRM**. Save the file as
`renderer/avatar3d/models/sarah.vrm`. VRM 1.0 and 0.x both work. All her animations
and expressions carry over, since every VRM model has the same named bones.

## Animations from Mixamo

For each animation below:

1. On mixamo.com, search for the name in the **Search for** column and click the result.
2. Click **Download** and use these settings:
   - Format: **FBX Binary (.fbx)**
   - Skin: **Without Skin**
   - Frames per Second: **30**
   - Keyframe Reduction: **none**
3. Save it into `renderer/avatar3d/animations/` using the name in the **Save as** column.

Reload the page and the new animations appear in the test panel. Any file you haven't
downloaded yet is simply skipped.

| # | Search for | Save as | Group | Plays |
|---|---|---|---|---|
| 1 | Idle | `idle.fbx` | idle | loops |
| 2 | Breathing Idle | `breathing_idle.fbx` | idle | loops |
| 3 | Happy Idle | `happy_idle.fbx` | idle_happy | loops |
| 4 | Sad Idle | `sad_idle.fbx` | idle_sad | loops |
| 5 | Angry | `angry_idle.fbx` | idle_angry | loops |
| 6 | Talking | `talking_1.fbx` | talk | loops |
| 7 | Talking (a different one) | `talking_2.fbx` | talk | loops |
| 8 | Talking (a third one) | `talking_3.fbx` | talk | loops |
| 9 | Thinking | `thinking.fbx` | think | loops |
| 10 | Waving | `waving.fbx` | wave | once |
| 11 | Standing Greeting | `greeting.fbx` | wave | once |
| 12 | Head Nod Yes | `head_nod_yes.fbx` | nod | once |
| 13 | Agreeing | `agreeing.fbx` | nod | once |
| 14 | Shaking Head No | `shaking_head_no.fbx` | shake | once |
| 15 | Excited | `excited.fbx` | celebrate | once |
| 16 | Clapping | `clapping.fbx` | celebrate | once |
| 17 | Laughing | `laughing.fbx` | laugh | once |
| 18 | Shrugging | `shrugging.fbx` | shrug | once |
| 19 | Surprised | `surprised.fbx` | surprised | once |
| 20 | Disappointed | `disappointed.fbx` | sad | once |
| 21 | Bashful | `bashful.fbx` | shy | once |
| 22 | Blow A Kiss | `blow_kiss.fbx` | affection | once |
| 23 | Quick Formal Bow | `bow.fbx` | bow | once |
| 24 | Pointing | `pointing.fbx` | point | once |
| 25 | Look Around | `look_around.fbx` | look | once |
| 26 | Arm Stretching | `stretching.fbx` | stretch | once |
| 27 | Yawn | `yawn.fbx` | yawn | once |
| 28 | Hip Hop Dancing | `hip_hop_dancing.fbx` | dance | loops |
| 29 | Samba Dancing | `samba_dancing.fbx` | dance | loops |
| 30 | Silly Dancing | `silly_dancing.fbx` | dance | loops |
| 31 | Sitting Idle | `sitting_idle.fbx` | sit | loops |
| 32 | Sitting Talking | `sitting_talking.fbx` | sit | loops |

Mixamo has several animations with similar names. Pick whichever one you like, as long as it's saved
under the right file name. To add more, add a line to `animations/animations.json`.

**Groups** are what Sarah's code asks for. For example, `play('wave')` picks any animation in
the `wave` group, `talk` plays while she speaks, and `idle_happy` / `idle_sad` / `idle_angry`
replace the normal idle while she feels that way. `look`, `stretch` and `yawn` play now and
then while she's idle.

> Mixamo's license lets you use these animations in Sarah, but not share the raw files.
> `.gitignore` keeps them out of GitHub.

## Showing her in the Electron app

The page has a transparent background, so she can float over the desktop or Sarah's UI:

```js
const avatarWindow = new BrowserWindow({
  width: 500, height: 700, transparent: true, frame: false, alwaysOnTop: true,
});
avatarWindow.loadURL('http://127.0.0.1:8907/avatar/');
```

Or put it inside an existing page: `<iframe src="http://127.0.0.1:8907/avatar/">`.

### Controlling her from other code

On the avatar page (or `iframe.contentWindow`):

```js
sarahAvatar.setEmotion('happy', 0.8);      // happy, sad, angry, surprised, relaxed, concerned…
sarahAvatar.play('wave');                  // any group or animation name
sarahAvatar.speak(audioBlobOrUrl);         // plays audio with lip sync
sarahAvatar.setFraming('full');            // 'upper' (head & shoulders) or 'full' body
await sarahBridge.chat('Hi Sarah!');       // sends to /api/chat, reacts, speaks the reply
sarahBridge.onWake();                      // call when the wake word fires
```

She also reacts by herself when a V11 background task finishes (via `/api/notifications`).
Settings such as the model path, framing and wake polling are in `avatar.config.json`.

## Changing the code

Source is in `src/`. After editing, rebuild the bundle the page loads:
`npm run build:avatar`

## Credits

- [three.js](https://threejs.org) and [three-vrm](https://github.com/pixiv/three-vrm): MIT
  license. The Mixamo retargeting is adapted from three-vrm's example.
- Sample model "AvatarSample_F": CC0, from early VRoid Studio
  ([OpenGameArt](https://opengameart.org/content/vroid-studio-cc0-models)).
