// What kind of thing the window in front is, so she can react to it:
//   "video"  a film, a stream, a video site: she settles down and watches
//   "focus"  anything filling the whole monitor (a game, a presentation):
//            she keeps out of the way and stays quiet
//   "work"   code, documents, spreadsheets: fewer fidgets
//   null     anything else
// Input is /api/agency/foreground's answer: { app, title, fullscreen }.

const VIDEO_APPS = new Set([
  "vlc.exe", "mpv.exe", "mpc-hc.exe", "mpc-hc64.exe", "mpc-be.exe", "mpc-be64.exe", "potplayer.exe",
  "potplayermini.exe", "potplayermini64.exe", "kmplayer.exe", "wmplayer.exe", "video.ui.exe",
  "microsoft.media.player.exe", "plex.exe", "jellyfin media player.exe", "netflix.exe", "smplayer.exe",
]);
const VIDEO_TITLES = /(^|[^\w])(youtube|netflix|twitch|prime video|disney\+|hulu|crunchyroll|hbo max|plex|jellyfin|vimeo|dailymotion|funimation|paramount\+|peacock|apple tv|vlc media player|media player classic)(?!\w)/i;
const WORK_APPS = new Set([
  "code.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "webstorm64.exe",
  "rider64.exe", "clion64.exe", "goland64.exe", "sublime_text.exe", "notepad++.exe", "winword.exe",
  "excel.exe", "powerpnt.exe", "onenote.exe", "obsidian.exe", "notion.exe", "blender.exe", "photoshop.exe",
  "figma.exe", "unity.exe", "unrealeditor.exe", "windowsterminal.exe", "wt.exe", "powershell.exe", "cmd.exe",
]);
// Browsers and the desktop itself are never "focus" just for being large.
const SHELLS = new Set(["explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"]);

export function classifyApp(fg) {
  if (!fg || !fg.found) return null;
  const app = String(fg.app || "").toLowerCase();
  const title = String(fg.title || "");
  if (VIDEO_APPS.has(app) || VIDEO_TITLES.test(title)) return "video";
  if (fg.fullscreen && !SHELLS.has(app)) return "focus";
  if (WORK_APPS.has(app)) return "work";
  return null;
}
