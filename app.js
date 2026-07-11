// ============================================================
// Language Learning Video Tracker — frontend
// Talks to Flask backend (server.py). State lives in SQLite.
// YouTube IFrame API is used to track playback time.
// ============================================================

const API = {
  authStatus:  ()                                  => fetchJSON('/api/auth/status'),
  register:    (body)                              => fetchJSON('/api/auth/register', { method: 'POST', body }),
  login:       (body)                              => fetchJSON('/api/auth/login', { method: 'POST', body }),
  logout:      ()                                  => fetchJSON('/api/auth/logout', { method: 'POST' }),
  listVideos:  ()                                  => fetchJSON('/api/videos'),
  addVideo:    (body)                              => fetchJSON('/api/videos', { method: 'POST', body }),
  removeVideo: (id)                                => fetch(`/api/videos/${id}`, { method: 'DELETE' }),
  watchTime:   (id, seconds, position, token)      => fetchJSON(`/api/videos/${id}/watch`, { method: 'POST', body: { seconds, position, session_token: token } }),
  rateVideo:   (id)                                => fetchJSON(`/api/videos/${id}/rate`, { method: 'POST' }),
  search:      (q, max)                            => fetchJSON(`/api/search?q=${encodeURIComponent(q)}&max=${max}`),
  stats:       ()                                  => fetchJSON('/api/stats'),
  setGoal:     (minutes)                           => fetchJSON('/api/goal', { method: 'POST', body: { minutes } }),
  reset:       ()                                  => fetchJSON('/api/reset', { method: 'POST' }),
  history:     (limit = 100)                       => fetchJSON(`/api/history?limit=${limit}`),
};

async function fetchJSON(url, { method = 'GET', body } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_) {
      data = {
        error: `Non-JSON response (${res.status}): ${text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()}`,
      };
    }
  }
  if (!res.ok) {
    const msg = (data && data.error) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    if (res.status === 401 && !url.startsWith('/api/auth/')) showAuth(true);
    throw err;
  }
  return data;
}

let authMode = 'login';

function configureAuth(status) {
  authMode = status.has_user ? 'login' : 'register';
  const isSetup = authMode === 'register';
  document.getElementById('authTitle').textContent = isSetup ? 'Create your account' : 'Sign in';
  document.getElementById('authHint').textContent = isSetup
    ? 'No user exists yet. Create the first account to protect this app.'
    : 'Use your JapApp account to continue.';
  document.getElementById('authSubmit').textContent = isSetup ? 'Create account' : 'Sign in';
  document.getElementById('authPassword').autocomplete = isSetup ? 'new-password' : 'current-password';
}

function showAuth(show, status = null) {
  if (status) configureAuth(status);
  document.body.classList.toggle('auth-pending', show);
  document.getElementById('authScreen').hidden = !show;
  if (show) setTimeout(() => document.getElementById('authUsername').focus(), 50);
}

function setSignedInUser(username) {
  document.getElementById('currentUser').textContent = username ? `Signed in as ${username}` : '';
}

// ---------- Utilities ----------
function fmt(seconds) {
  seconds = Math.floor(seconds || 0);
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

function fmtViews(n) {
  if (n == null) return '';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(n);
}

function fmtDuration(s) {
  if (s == null) return '';
  s = Math.floor(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function relativeTime(sqlTimestamp) {
  if (!sqlTimestamp) return '';
  // SQLite datetime('now') returns UTC like "2026-05-25 18:37:08" with no zone.
  // Treat it as UTC by inserting T and appending Z.
  const t = new Date(sqlTimestamp.includes('T') ? sqlTimestamp : sqlTimestamp.replace(' ', 'T') + 'Z');
  const sec = (Date.now() - t.getTime()) / 1000;
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 604800) return `${Math.floor(sec / 86400)}d ago`;
  return t.toLocaleDateString();
}

function genToken() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function extractYouTubeId(input) {
  if (!input) return null;
  const trimmed = input.trim();
  if (/^[A-Za-z0-9_-]{11}$/.test(trimmed)) return trimmed;
  try {
    const url = new URL(trimmed);
    if (url.hostname.includes('youtu.be')) return url.pathname.slice(1).split('/')[0] || null;
    if (url.hostname.includes('youtube.com')) {
      if (url.searchParams.get('v')) return url.searchParams.get('v');
      const m = url.pathname.match(/\/(embed|shorts|v)\/([A-Za-z0-9_-]{11})/);
      if (m) return m[2];
    }
  } catch (_) {}
  return null;
}

function bandSlug(label) {
  if (!label) return '';
  // "N3" → "n3", "N1+" → "n1plus"
  return label.toLowerCase().replace('+', 'plus');
}

const KANJI_BANDS = ['N5', 'N4', 'N3', 'N2', 'N1', 'above_n1'];

function bandLabelFromKey(key) {
  return key === 'above_n1' ? 'N1+' : key;
}

// Map raw difficulty score (~1.0 = all N5, ~6.0 = all N1+) to a 0–10 scale.
// Real-world content lives in roughly 1.5–3.5, so we clamp to that range.
function difficultyOnTen(rawScore) {
  if (typeof rawScore !== 'number' || !isFinite(rawScore)) return null;
  const normalized = (rawScore - 1.0) / 0.3;          // 1.0 → 0, 4.0 → 10
  return Math.max(0, Math.min(10, normalized));
}

// Continuous green→yellow→red gradient over 0..10.
function difficultyColor(score10) {
  const t = Math.max(0, Math.min(10, score10)) / 10;  // 0..1
  const hue = 130 - t * 130;                          // 130 (green) → 0 (red)
  return `hsl(${hue}, 60%, 40%)`;
}

function toast(msg, kind = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 2800);
}

// ---------- Card rendering ----------
function renderLevelCell(video) {
  const breakdown = video.kanji_breakdown;
  if (!breakdown) {
    return `<button class="rate-btn" data-action="rate" data-id="${video.id}">Rate level</button>`;
  }
  const rawScore = (breakdown.difficulty_score != null)
    ? breakdown.difficulty_score
    : video.level_score;
  const score10 = difficultyOnTen(rawScore);
  const score10Txt = (score10 != null) ? score10.toFixed(1) : '?';
  const color = (score10 != null) ? difficultyColor(score10) : 'var(--surface-2)';
  const occ = breakdown.occurrence_counts || {};
  const totalOcc = breakdown.total_kanji || 0;

  // Tooltip surfaces the underlying breakdown for those who want detail.
  const tipLines = KANJI_BANDS.map(k => {
    const o = occ[k] || 0;
    const u = (breakdown.unique_counts || {})[k] || 0;
    return `${bandLabelFromKey(k)}: ${o} (${u} unique)`;
  });
  const tip = `Difficulty: ${score10Txt}/10\n${breakdown.total_kanji} kanji, ${breakdown.unique_kanji} unique\n${tipLines.join(' · ')}\nRated ${video.level_rated_at || ''}. Click to re-rate.`;

  // Stacked bar segments — only show bands with > 0 occurrences
  const segs = KANJI_BANDS
    .filter(k => (occ[k] || 0) > 0)
    .map(k => `<div class="kanji-bar-seg" data-band="${bandSlug(bandLabelFromKey(k))}" style="flex-grow:${occ[k]}"></div>`)
    .join('');

  return `
    <div class="kanji-level-wrap" title="${tip.replace(/"/g, '&quot;')}">
      <button class="level-badge difficulty-badge" data-action="rate" data-id="${video.id}"
              style="background:${color};">
        <span class="score-10">${score10Txt}</span>
        <span class="score-suffix">/10</span>
      </button>
      ${totalOcc ? `<div class="kanji-bar">${segs}</div>` : ''}
      ${totalOcc ? `<div class="kanji-summary">${breakdown.total_kanji} kanji · ${breakdown.unique_kanji} unique</div>` : ''}
    </div>
  `;
}

function videoCard(v) {
  const card = document.createElement('div');
  card.className = 'video-card clickable';
  card.dataset.id = v.id;
  card.innerHTML = `
    <img class="video-thumb" loading="lazy" alt="" />
    <div class="video-card-actions">
      <button class="icon-btn" data-action="remove" data-id="${v.id}" title="Remove">&times;</button>
    </div>
    <div class="video-info">
      <h3 class="video-title"></h3>
      <span class="video-channel"></span>
      <div class="video-meta">
        <span class="video-lang"></span>
        <span class="video-watched">${fmt(v.watched_seconds)} watched</span>
      </div>
      <div class="level-row">${renderLevelCell(v)}</div>
    </div>
  `;
  card.querySelector('.video-thumb').src = v.thumbnail || `https://img.youtube.com/vi/${v.id}/mqdefault.jpg`;
  card.querySelector('.video-title').textContent = v.title;
  const chan = card.querySelector('.video-channel');
  if (v.channel) chan.textContent = v.channel; else chan.remove();
  const langEl = card.querySelector('.video-lang');
  if (v.language) langEl.textContent = v.language; else langEl.remove();

  card.addEventListener('click', (e) => {
    if (e.target.closest('[data-action]')) return;
    openPlayer(v.id);
  });
  return card;
}

function searchCard(r) {
  const card = document.createElement('div');
  card.className = 'video-card';
  card.dataset.id = r.id;
  const duration = fmtDuration(r.duration_seconds);
  const views = fmtViews(r.view_count);
  const meta = [duration, views ? `${views} views` : ''].filter(Boolean).join(' · ');
  card.innerHTML = `
    <img class="video-thumb" loading="lazy" alt="" />
    <div class="video-info">
      <h3 class="video-title"></h3>
      <span class="video-channel"></span>
      <div class="video-meta">
        <span>${meta}</span>
        <button class="add-btn" data-action="add" data-id="${r.id}" ${r.in_collection ? 'disabled' : ''}>
          ${r.in_collection ? 'In collection' : '+ Add'}
        </button>
      </div>
    </div>
  `;
  card.querySelector('.video-thumb').src = r.thumbnail || `https://img.youtube.com/vi/${r.id}/mqdefault.jpg`;
  card.querySelector('.video-title').textContent = r.title || r.id;
  const chan = card.querySelector('.video-channel');
  if (r.channel) chan.textContent = r.channel; else chan.remove();

  // Stash the full result so the click handler can POST a body
  card._result = r;
  return card;
}

// ---------- State ----------
let videos = [];        // local cache of /api/videos
let lastSearch = [];    // last search results

// ---------- Renderers ----------
const grid = document.getElementById('videoGrid');
const emptyHint = document.getElementById('emptyHint');
const searchGrid = document.getElementById('searchGrid');
const searchHint = document.getElementById('searchHint');

async function refreshVideos() {
  videos = await API.listVideos();
  grid.innerHTML = '';
  if (!videos.length) {
    emptyHint.hidden = false;
    return;
  }
  emptyHint.hidden = true;
  for (const v of videos) grid.appendChild(videoCard(v));
}

async function refreshStats() {
  const s = await API.stats();
  const goalSec = s.daily_goal_minutes * 60;
  const pct = goalSec ? Math.min(100, (s.today_seconds / goalSec) * 100) : 0;
  document.getElementById('todayTime').textContent = fmt(s.today_seconds);
  document.getElementById('dailyGoal').textContent = `${s.daily_goal_minutes}m`;
  document.getElementById('totalTime').textContent = fmt(s.total_seconds);
  const fill = document.getElementById('dailyBarFill');
  fill.style.width = `${pct}%`;
  fill.classList.toggle('complete', pct >= 100);
}

function renderSearch(results) {
  searchGrid.innerHTML = '';
  if (!results.length) {
    searchHint.textContent = 'No results.';
    searchHint.hidden = false;
    return;
  }
  searchHint.hidden = true;
  for (const r of results) searchGrid.appendChild(searchCard(r));
}

async function refreshHistory() {
  let sessions;
  try {
    sessions = await API.history();
  } catch (err) {
    toast(`History failed: ${err.message}`, 'error');
    return;
  }
  const list = document.getElementById('historyList');
  const empty = document.getElementById('historyEmpty');
  const count = document.getElementById('historyCount');
  list.innerHTML = '';
  count.textContent = sessions.length ? `${sessions.length} session${sessions.length === 1 ? '' : 's'}` : '';
  if (!sessions.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  for (const s of sessions) {
    const li = document.createElement('li');
    li.className = 'history-item';
    li.dataset.id = s.video_id;
    const inCollection = videos.some(v => v.id === s.video_id);
    const metaBits = [
      relativeTime(s.started_at),
      `watched ${fmt(s.seconds_watched)}`,
    ];
    if (s.last_position != null) metaBits.push(`stopped at ${fmtDuration(s.last_position)}`);
    li.innerHTML = `
      <img class="history-thumb" loading="lazy" alt="" />
      <div class="history-info">
        <div class="history-title"></div>
        <div class="history-channel"></div>
        <div class="history-meta"></div>
      </div>
      <button class="resume-btn" ${inCollection ? '' : 'disabled title="Video no longer in collection"'}>
        ${inCollection ? 'Resume' : 'Removed'}
      </button>
    `;
    li.querySelector('.history-thumb').src = s.thumbnail || `https://img.youtube.com/vi/${s.video_id}/mqdefault.jpg`;
    li.querySelector('.history-title').textContent = s.title || `Video ${s.video_id}`;
    li.querySelector('.history-channel').textContent = s.channel || '';
    const metaEl = li.querySelector('.history-meta');
    metaBits.forEach((b, i) => {
      const span = document.createElement('span');
      span.textContent = (i > 0 ? '· ' : '') + b;
      metaEl.appendChild(span);
    });
    if (inCollection) {
      li.addEventListener('click', () => openPlayer(s.video_id));
    }
    list.appendChild(li);
  }
}

// ---------- Event delegation for card actions ----------
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const id = btn.dataset.id;
  const action = btn.dataset.action;

  if (action === 'remove') {
    if (!confirm('Remove this video from your collection?')) return;
    await API.removeVideo(id);
    await refreshVideos();
    await refreshStats();
  } else if (action === 'rate') {
    btn.disabled = true;
    const prevHTML = btn.innerHTML;
    btn.textContent = 'Rating…';
    try {
      const r = await API.rateVideo(id);
      const b = r.kanji_breakdown;
      const s10 = difficultyOnTen(r.difficulty_score);
      const s10Txt = s10 != null ? s10.toFixed(1) : '?';
      toast(`Difficulty ${s10Txt}/10 · ${b.total_kanji} kanji, ${b.unique_kanji} unique (${r.subtitle_kind} subs)`, 'success');
      await refreshVideos();
    } catch (err) {
      toast(`Rate failed: ${err.message}`, 'error');
      btn.disabled = false;
      btn.innerHTML = prevHTML;
    }
  } else if (action === 'add') {
    const card = btn.closest('.video-card');
    const r = card && card._result;
    if (!r) return;
    btn.disabled = true;
    btn.textContent = 'Adding…';
    try {
      await API.addVideo({
        id: r.id,
        title: r.title,
        channel: r.channel,
        channel_url: r.channel_url,
        duration_seconds: r.duration_seconds,
        view_count: r.view_count,
        thumbnail: r.thumbnail,
        description: r.description,
      });
      btn.textContent = 'In collection';
      r.in_collection = true;
      toast('Added to collection', 'success');
      await refreshVideos();
    } catch (err) {
      toast(`Add failed: ${err.message}`, 'error');
      btn.disabled = false;
      btn.textContent = '+ Add';
    }
  }
});

// ---------- Player + time tracking ----------
let player = null;
let currentVideoId = null;
let currentSessionToken = null;
let isPlaying = false;
let sessionSeconds = 0;
let pendingSeconds = 0;   // accumulated, not yet flushed to server
let tickerHandle = null;
let ytApiReady = false;
let pendingOpen = null;   // { videoId, resumeAt }

window.onYouTubeIframeAPIReady = function () {
  ytApiReady = true;
  if (pendingOpen) {
    const { videoId, resumeAt } = pendingOpen;
    pendingOpen = null;
    createPlayer(videoId, resumeAt);
  }
};

function createPlayer(videoId, resumeAt) {
  player = new YT.Player('ytPlayer', {
    videoId,
    playerVars: { rel: 0, modestbranding: 1 },
    events: {
      onReady: (e) => {
        if (resumeAt && resumeAt > 5) {
          const dur = e.target.getDuration();
          // Don't seek if we're already at or past the end (e.g., previous session finished).
          if (!dur || resumeAt < dur - 5) {
            e.target.seekTo(resumeAt, true);
            toast(`Resuming from ${fmtDuration(resumeAt)}`);
          }
        }
      },
      onStateChange: (e) => {
        if (e.data === YT.PlayerState.PLAYING) {
          isPlaying = true;
          startTicker();
        } else {
          isPlaying = false;
          flushPending();
        }
      },
    },
  });
}

function destroyPlayer() {
  if (player && typeof player.destroy === 'function') {
    try { player.destroy(); } catch (_) {}
  }
  player = null;
  document.getElementById('playerContainer').innerHTML = '<div id="ytPlayer"></div>';
}

function startTicker() {
  if (tickerHandle) return;
  tickerHandle = setInterval(async () => {
    if (!isPlaying || !currentVideoId) return;
    sessionSeconds += 1;
    pendingSeconds += 1;
    // Update on-screen counters
    document.getElementById('sessionTime').textContent = fmt(sessionSeconds);
    const local = videos.find(v => v.id === currentVideoId);
    if (local) {
      local.watched_seconds += 1;
      document.getElementById('videoTotalTime').textContent = fmt(local.watched_seconds);
    }
    // Flush every 5s
    if (pendingSeconds >= 5) await flushPending();
  }, 1000);
}

function stopTicker() {
  if (tickerHandle) {
    clearInterval(tickerHandle);
    tickerHandle = null;
  }
}

function currentPosition() {
  try {
    if (player && typeof player.getCurrentTime === 'function') {
      const t = player.getCurrentTime();
      if (typeof t === 'number' && isFinite(t)) return t;
    }
  } catch (_) {}
  return null;
}

async function flushPending() {
  if (pendingSeconds <= 0 || !currentVideoId) return;
  const sec = pendingSeconds;
  pendingSeconds = 0;
  try {
    await API.watchTime(currentVideoId, sec, currentPosition(), currentSessionToken);
    await refreshStats();
    // Cache the position locally so subsequent opens resume correctly without a full reload.
    const local = videos.find(v => v.id === currentVideoId);
    if (local) local.last_position_seconds = currentPosition();
  } catch (err) {
    pendingSeconds += sec;
    console.warn('watch flush failed', err);
  }
}

// ---------- Modals ----------
const playerModal = document.getElementById('playerModal');
const addModal = document.getElementById('addModal');
const goalModal = document.getElementById('goalModal');

async function openPlayer(videoId) {
  // Resolve metadata from the local cache; if the video isn't in /api/videos
  // (e.g. came from the History tab after deletion), fall back to a stub.
  let v = videos.find(x => x.id === videoId);
  if (!v) {
    // refresh in case the cache is stale, then re-check
    await refreshVideos();
    v = videos.find(x => x.id === videoId);
  }
  if (!v) {
    toast('Video is no longer in your collection', 'error');
    return;
  }

  currentVideoId = videoId;
  currentSessionToken = genToken();
  sessionSeconds = 0;
  pendingSeconds = 0;
  document.getElementById('playerTitle').textContent = v.title;
  document.getElementById('sessionTime').textContent = '0s';
  document.getElementById('videoTotalTime').textContent = fmt(v.watched_seconds);
  playerModal.hidden = false;

  const resumeAt = v.last_position_seconds || 0;
  if (ytApiReady) createPlayer(videoId, resumeAt);
  else pendingOpen = { videoId, resumeAt };
}

async function closePlayer() {
  isPlaying = false;
  stopTicker();
  await flushPending();
  destroyPlayer();
  currentVideoId = null;
  currentSessionToken = null;
  playerModal.hidden = true;
  await refreshVideos();
  await refreshStats();
  // Refresh history in case the user switches to it next
  if (document.getElementById('historyPanel').classList.contains('active')) {
    refreshHistory();
  }
}

function openModal(m) { m.hidden = false; }
function closeModal(m) { m.hidden = true; }

document.querySelectorAll('.modal').forEach(modal => {
  modal.addEventListener('click', (e) => {
    if (!e.target.matches('[data-close]')) return;
    if (modal === playerModal) closePlayer();
    else closeModal(modal);
  });
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!playerModal.hidden) closePlayer();
  else if (!addModal.hidden) closeModal(addModal);
  else if (!goalModal.hidden) closeModal(goalModal);
});

// ---------- Tabs ----------
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const name = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.toggle('active', p.id === `${name}Panel`);
    });
    if (name === 'search') {
      setTimeout(() => document.getElementById('searchInput').focus(), 50);
    } else if (name === 'history') {
      refreshHistory();
    }
  });
});

// ---------- Forms ----------
document.getElementById('authForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('authUsername').value.trim();
  const password = document.getElementById('authPassword').value;
  const btn = document.getElementById('authSubmit');
  const error = document.getElementById('authError');
  error.hidden = true;
  btn.disabled = true;
  btn.textContent = authMode === 'register' ? 'Creating...' : 'Signing in...';
  try {
    const result = authMode === 'register'
      ? await API.register({ username, password })
      : await API.login({ username, password });
    setSignedInUser(result.username);
    showAuth(false);
    await Promise.all([refreshVideos(), refreshStats()]);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = authMode === 'register' ? 'Create account' : 'Sign in';
  }
});

document.getElementById('logoutBtn').addEventListener('click', async () => {
  await API.logout();
  setSignedInUser('');
  const status = await API.authStatus();
  showAuth(true, status);
});

document.getElementById('addVideoBtn').addEventListener('click', () => {
  document.getElementById('addForm').reset();
  openModal(addModal);
  setTimeout(() => document.getElementById('videoUrl').focus(), 50);
});

document.getElementById('addForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = document.getElementById('videoUrl').value;
  const id = extractYouTubeId(url);
  if (!id) { toast('Could not parse YouTube ID from that input', 'error'); return; }
  const btn = document.getElementById('addSubmit');
  btn.disabled = true;
  btn.textContent = 'Fetching…';
  try {
    await API.addVideo({ id });
    closeModal(addModal);
    await refreshVideos();
    toast('Added', 'success');
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Add';
  }
});

document.getElementById('editGoalBtn').addEventListener('click', async () => {
  const s = await API.stats();
  document.getElementById('goalMinutes').value = s.daily_goal_minutes;
  openModal(goalModal);
});

document.getElementById('goalForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const mins = parseInt(document.getElementById('goalMinutes').value, 10);
  if (Number.isFinite(mins) && mins > 0) {
    await API.setGoal(mins);
    await refreshStats();
    closeModal(goalModal);
  }
});

document.getElementById('resetBtn').addEventListener('click', async () => {
  if (!confirm('Reset all tracked time? Your video list is kept.')) return;
  await API.reset();
  await refreshVideos();
  await refreshStats();
});

document.getElementById('searchForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;
  const max = parseInt(document.getElementById('searchMax').value, 10) || 20;
  const btn = document.getElementById('searchSubmit');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  searchHint.textContent = 'Searching…';
  searchHint.hidden = false;
  searchGrid.innerHTML = '';
  try {
    const r = await API.search(q, max);
    lastSearch = r.results;
    renderSearch(r.results);
  } catch (err) {
    searchHint.textContent = `Search failed: ${err.message}`;
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Search';
  }
});

// Best-effort flush on tab close
window.addEventListener('pagehide', () => {
  if (pendingSeconds > 0 && currentVideoId && navigator.sendBeacon) {
    const payload = {
      seconds: pendingSeconds,
      position: currentPosition(),
      session_token: currentSessionToken,
    };
    const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
    navigator.sendBeacon(`/api/videos/${currentVideoId}/watch`, blob);
    pendingSeconds = 0;
  }
});

// ---------- Init ----------
(async function init() {
  try {
    const status = await API.authStatus();
    configureAuth(status);
    if (!status.authenticated) {
      showAuth(true, status);
      return;
    }
    setSignedInUser(status.username);
    showAuth(false);
    await Promise.all([refreshVideos(), refreshStats()]);
  } catch (err) {
    toast(`Failed to load: ${err.message}`, 'error');
    showAuth(true);
  }
})();
