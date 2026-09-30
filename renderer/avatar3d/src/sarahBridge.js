// Connects the avatar to Sarah's backend (server.py, port 8907):
//   - chat replies set her expression, then she speaks them with lip sync
//   - finished background tasks (V11 notifications) get a happy reaction
//   - optional: the wake word makes her wave
//
// When the page is opened from the backend (http://127.0.0.1:8907/avatar/),
// `api` can stay '' (same address, no CORS setup needed).

export class SarahBridge {
  /**
   * @param {import('./sarahAvatar.js').SarahAvatar} avatar
   * @param {{api?: string, pollNotifications?: boolean, pollWake?: boolean,
   *          notificationIntervalMs?: number, wakeIntervalMs?: number}} options
   */
  constructor(avatar, options = {}) {
    this.avatar = avatar;
    this.api = (options.api || '').replace(/\/$/, '');
    this.pollNotifications = options.pollNotifications !== false;
    // Off by default: /api/wake hands each wake event to only one caller, so
    // polling here would steal it from Sarah's main UI if that polls too.
    this.pollWake = !!options.pollWake;
    this.notificationIntervalMs = options.notificationIntervalMs || 5000;
    this.wakeIntervalMs = options.wakeIntervalMs || 1500;
    this._seenNotifications = null; // filled on the first poll, so old ones don't trigger
    this._timers = [];
  }

  start() {
    if (this.pollNotifications) {
      this._checkNotifications();
      this._timers.push(setInterval(() => this._checkNotifications(), this.notificationIntervalMs));
    }
    if (this.pollWake) {
      this._timers.push(setInterval(() => this._checkWake(), this.wakeIntervalMs));
    }
  }

  stop() {
    this._timers.forEach(clearInterval);
    this._timers = [];
  }

  async _json(path, init) {
    const res = await fetch(this.api + path, init);
    if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
    return res.json();
  }

  /**
   * Send a message to Sarah; she reacts, then speaks the reply.
   * @returns {Promise<object>} the /api/chat response
   */
  async chat(message, { speak = true, conversationId = null } = {}) {
    this.avatar.play('think');
    let data;
    try {
      data = await this._json('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, conversation_id: conversationId }),
      });
    } finally {
      this.avatar.returnToIdle(); // stop "thinking" whether or not the reply arrived
    }
    this.avatar.setEmotion(data.emotion || 'neutral', data.emotion_intensity ?? 0.6);
    if (speak && data.reply) await this.say(data.reply);
    return data;
  }

  /** Speak text with Sarah's TTS voice; mouth-only animation if TTS is unavailable. */
  async say(text) {
    try {
      const res = await fetch(this.api + '/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(`TTS HTTP ${res.status}`);
      await this.avatar.speak(await res.blob());
    } catch (err) {
      console.warn('[avatar] TTS unavailable, animating without audio:', err);
      const ms = Math.min(15000, 600 + text.length * 55); // rough speaking time
      this.avatar.fakeTalk(ms);
      await new Promise((r) => setTimeout(r, ms));
    }
  }

  /** Call this from Sarah's UI when the wake word fires (if you don't use pollWake). */
  onWake() {
    this.avatar.play('wave');
  }

  async _checkWake() {
    try {
      const data = await this._json('/api/wake');
      if (data.wake) this.onWake();
    } catch { /* backend not up yet */ }
  }

  async _checkNotifications() {
    try {
      const data = await this._json('/api/notifications?unread_only=true');
      const notes = data.notifications || [];
      if (this._seenNotifications === null) {
        this._seenNotifications = new Set(notes.map((n) => n.id));
        return;
      }
      const fresh = notes.filter((n) => !this._seenNotifications.has(n.id));
      fresh.forEach((n) => this._seenNotifications.add(n.id));
      if (fresh.length) {
        const failed = fresh.some((n) => /couldn't|didn't/i.test(n.message));
        this.avatar.setEmotion(failed ? 'concerned' : 'happy', 0.7);
        this.avatar.play(failed ? 'shake' : 'celebrate') || this.avatar.play('nod');
        window.dispatchEvent(new CustomEvent('sarah-notification', { detail: fresh }));
      }
    } catch { /* backend not up yet, or V11 features not installed */ }
  }
}
