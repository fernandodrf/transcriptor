  // ---- State ----
  let selectedProvider = 'soniox';
  let selectedFile = null;
  let fileQueue = [];
  let providerStatus = {};
  let timerInterval = null;
  let speakerNames = {};
  let currentHistoryId = null;

  // ---- Transcript History Storage ----
  const HISTORY_KEY = 'transcriptHistory';
  const HISTORY_MAX = 50;

  function loadHistory() {
    try {
      return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    } catch { return []; }
  }

  function saveToHistory(result, filename) {
    const history = loadHistory();
    const entry = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      date: new Date().toISOString(),
      provider: result.provider || selectedProvider,
      duration_sec: result.duration_sec,
      segments: result.segments || [],
      filename: filename || 'unknown',
      speaker_names: {},
    };
    history.unshift(entry);
    if (history.length > HISTORY_MAX) history.length = HISTORY_MAX;
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    currentHistoryId = entry.id;
    speakerNames = {};
    return entry;
  }

  function deleteFromHistory(id) {
    const history = loadHistory().filter(e => e.id !== id);
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    return history;
  }

  function clearHistory() {
    localStorage.removeItem(HISTORY_KEY);
  }

  // ---- History UI ----
  function renderHistory() {
    const history = loadHistory();
    const section = document.getElementById('historySection');
    const list = document.getElementById('historyList');
    const clearBtn = document.getElementById('btnClearHistory');

    if (!history.length) {
      section.style.display = 'none';
      return;
    }

    section.style.display = '';
    clearBtn.style.display = '';

    list.innerHTML = history.map(entry => {
      const d = new Date(entry.date);
      const dateStr = `${String(d.getMonth()+1).padStart(2,'0')}/${String(d.getDate()).padStart(2,'0')}`;
      const segCount = (entry.segments || []).length;
      const dur = entry.duration_sec != null ? `${Math.round(entry.duration_sec)}s` : '';
      return `
        <div class="history-entry" onclick="loadHistoryEntry('${entry.id}')">
          <span class="history-date">${dateStr}</span>
          <span class="history-filename" title="${escapeHtml(entry.filename)}">${escapeHtml(entry.filename)}</span>
          <span class="history-provider" data-p="${escapeHtml(entry.provider)}">${escapeHtml(entry.provider)}</span>
          <span class="history-meta">${segCount} seg${segCount !== 1 ? 's' : ''} · ${dur}</span>
          <button class="history-delete" onclick="event.stopPropagation(); deleteHistoryEntry('${entry.id}')" title="Delete">✕</button>
        </div>`;
    }).join('');
  }

  function loadHistoryEntry(id) {
    const history = loadHistory();
    const entry = history.find(e => e.id === id);
    if (!entry) return;
    currentHistoryId = id;
    const result = {
      provider: entry.provider,
      duration_sec: entry.duration_sec,
      segments: entry.segments || [],
    };
    window._lastResult = result;
    speakerNames = entry.speaker_names || {};
    renderResult(result);
  }

  function deleteHistoryEntry(id) {
    deleteFromHistory(id);
    renderHistory();
  }

  function confirmClearHistory() {
    if (!confirm('Clear all transcript history?')) return;
    clearHistory();
    renderHistory();
  }

  // ---- Speaker renaming ----
  function saveSpeakerNames() {
    if (!currentHistoryId) return;
    const history = loadHistory();
    const entry = history.find(e => e.id === currentHistoryId);
    if (!entry) return;
    entry.speaker_names = { ...speakerNames };
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  }

  function getSpeakerName(originalLabel) {
    return speakerNames[originalLabel] || originalLabel;
  }

  function startRename(span) {
    const original = span.dataset.original;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'speaker-label-input';
    input.value = getSpeakerName(original);
    input.dataset.original = original;
    span.replaceWith(input);
    input.focus();
    input.select();

    function commit() {
      const newName = input.value.trim();
      if (newName && newName !== original) {
        speakerNames[original] = newName;
      } else {
        delete speakerNames[original];
      }
      saveSpeakerNames();
      updateSpeakerLabels(original);
    }

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { updateSpeakerLabels(original); }
    });
    input.addEventListener('blur', commit);
  }

  function updateSpeakerLabels(originalLabel) {
    const name = getSpeakerName(originalLabel);
    // Replace any active inputs back to spans
    document.querySelectorAll(`.speaker-label-input[data-original="${originalLabel}"]`).forEach(input => {
      const span = document.createElement('span');
      span.className = 'speaker-label';
      span.dataset.original = originalLabel;
      span.textContent = name;
      span.onclick = () => startRename(span);
      input.replaceWith(span);
    });
    // Update existing spans
    document.querySelectorAll(`.speaker-label[data-original="${originalLabel}"]`).forEach(span => {
      span.textContent = name;
    });
  }

  // ---- Provider selection ----
  function selectProvider(el) {
    if (el.classList.contains('unavailable')) return;
    document.querySelectorAll('.provider-pill').forEach(p => p.classList.remove('active'));
    el.classList.add('active');
    selectedProvider = el.dataset.provider;
  }

  // ---- Check server status ----
  async function checkServer() {
    const url = document.getElementById('serverUrl').value;
    try {
      const resp = await fetch(`${url}/api/providers`);
      providerStatus = await resp.json();
      document.getElementById('statusDot').classList.remove('offline');
      document.getElementById('statusDot').title = 'Server connected';

      document.querySelectorAll('.provider-pill').forEach(pill => {
        const p = pill.dataset.provider;
        if (providerStatus[p] === undefined) {
          pill.style.display = 'none';
        } else if (providerStatus[p] === false) {
          pill.style.display = '';
          pill.classList.add('unavailable');
          pill.title = `No API key configured for ${p}`;
        } else {
          pill.style.display = '';
          pill.classList.remove('unavailable');
          pill.title = '';
        }
      });

      // Auto-select first available provider if current is unavailable
      const currentPill = document.querySelector(`.provider-pill[data-provider="${selectedProvider}"]`);
      if (!currentPill || currentPill.style.display === 'none' || currentPill.classList.contains('unavailable')) {
        const firstAvailable = document.querySelector('.provider-pill:not(.unavailable):not([style*="display: none"])');
        if (firstAvailable) selectProvider(firstAvailable);
      }
    } catch {
      document.getElementById('statusDot').classList.add('offline');
      document.getElementById('statusDot').title = 'Server offline';
    }
  }

  // ---- File handling ----
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');

  dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', e => { if (e.target.files.length) handleFiles(e.target.files); });

  function handleFiles(files) {
    for (const file of files) {
      fileQueue.push(file);
    }
    renderQueue();
    document.getElementById('btnTranscribe').disabled = fileQueue.length === 0;

    const lastFile = fileQueue[fileQueue.length - 1];
    if (lastFile) {
      selectedFile = lastFile;
      const nameEl = document.getElementById('fileName');
      if (fileQueue.length === 1) {
        const sizeMB = (lastFile.size / 1024 / 1024).toFixed(1);
        nameEl.textContent = `${lastFile.name} (${sizeMB} MB)`;
      } else {
        nameEl.textContent = `${fileQueue.length} files selected`;
      }
      nameEl.style.display = 'block';

      // Show audio player with last added file's blob URL
      const audioPlayer = document.getElementById('audioPlayer');
      const audioEl = document.getElementById('audioElement');
      if (audioEl._blobUrl) URL.revokeObjectURL(audioEl._blobUrl);
      const blobUrl = URL.createObjectURL(lastFile);
      audioEl._blobUrl = blobUrl;
      audioEl.src = blobUrl;
      audioPlayer.style.display = '';
    }
  }

  function renderQueue() {
    const container = document.getElementById('fileQueue');
    if (fileQueue.length <= 1) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = fileQueue.map((file, i) => {
      const sizeMB = (file.size / 1024 / 1024).toFixed(1);
      return `<div class="queue-item fade-in">
        <span class="queue-item-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
        <span class="queue-item-size">${sizeMB} MB</span>
        <button class="queue-item-remove" onclick="removeFromQueue(${i})" title="Remove">&#10005;</button>
      </div>`;
    }).join('');
  }

  function removeFromQueue(index) {
    fileQueue.splice(index, 1);
    renderQueue();
    const nameEl = document.getElementById('fileName');
    if (fileQueue.length === 0) {
      selectedFile = null;
      document.getElementById('btnTranscribe').disabled = true;
      nameEl.style.display = 'none';
      document.getElementById('audioPlayer').style.display = 'none';
    } else if (fileQueue.length === 1) {
      const sizeMB = (fileQueue[0].size / 1024 / 1024).toFixed(1);
      nameEl.textContent = `${fileQueue[0].name} (${sizeMB} MB)`;
    } else {
      nameEl.textContent = `${fileQueue.length} files selected`;
    }
  }

  // ---- Transcription ----
  async function startTranscription() {
    const queue = [...fileQueue];
    if (queue.length === 0) return;

    const btn = document.getElementById('btnTranscribe');
    const statusText = document.getElementById('statusText');
    const progressBar = document.getElementById('progressBar');
    const container = document.getElementById('results');
    const url = document.getElementById('serverUrl').value;
    const language = document.getElementById('language').value;

    btn.disabled = true;
    btn.classList.add('processing');
    progressBar.classList.add('active');

    const isBatch = queue.length > 1;
    if (isBatch) container.innerHTML = '';

    // Timer
    let startTime = Date.now();
    timerInterval = setInterval(() => {
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
      statusText.innerHTML = `<span class="elapsed">${elapsed}s</span> elapsed`;
    }, 500);

    for (let i = 0; i < queue.length; i++) {
      const file = queue[i];
      btn.textContent = isBatch ? `Processing ${i + 1}/${queue.length}` : 'Processing…';
      if (isBatch) {
        statusText.innerHTML = `Processing <span class="elapsed">${i + 1}/${queue.length}: ${escapeHtml(file.name)}</span>`;
      }

      const formData = new FormData();
      formData.append('file', file);
      formData.append('provider', selectedProvider);
      formData.append('language', language);

      try {
        const resp = await fetch(`${url}/api/transcribe`, {
          method: 'POST',
          body: formData,
        });
        if (!resp.ok) {
          const err = await resp.text();
          throw new Error(err);
        }
        const result = await resp.json();
        saveToHistory(result, file.name);
        renderResult(result, { append: isBatch, filename: file.name });
        renderHistory();
      } catch (err) {
        if (isBatch) {
          container.insertAdjacentHTML('beforeend', `
            <div class="empty-state fade-in" style="color:var(--danger)">
              Error (${escapeHtml(file.name)}): ${escapeHtml(err.message)}
            </div>`);
        } else {
          container.innerHTML = `
            <div class="empty-state" style="color:var(--danger)">
              Error: ${escapeHtml(err.message)}
            </div>`;
        }
      }
    }

    clearInterval(timerInterval);
    btn.classList.remove('processing');
    btn.textContent = 'Transcribe';
    progressBar.classList.remove('active');
    statusText.textContent = '';

    // Clear queue after processing
    fileQueue = [];
    selectedFile = null;
    renderQueue();
    document.getElementById('fileName').style.display = 'none';
    btn.disabled = true;
    fileInput.value = '';
  }

  // ---- Render result ----
  function renderResult(result, { append = false, filename = '' } = {}) {
    const container = document.getElementById('results');
    const segments = result.segments || [];
    const provider = result.provider || selectedProvider;

    if (!segments.length) {
      const msg = '<div class="empty-state">No segments returned. Check audio quality or try another provider.</div>';
      if (append) container.insertAdjacentHTML('beforeend', msg);
      else container.innerHTML = msg;
      return;
    }

    // Extract speaker number from label
    function speakerNum(label) {
      const m = label.match(/(\d+)/);
      return m ? m[1] : '0';
    }

    function fmtTime(sec) {
      const s = Math.round(sec || 0);
      const mm = String(Math.floor(s / 60)).padStart(2, '0');
      const ss = String(s % 60).padStart(2, '0');
      return `${mm}:${ss}`;
    }

    let segmentsHTML;
    if (append) {
      // Batch mode: read-only segments (no edit/seek — use history for interaction)
      segmentsHTML = segments.map((seg) => `
        <div class="segment fade-in" data-speaker="${speakerNum(seg.speaker)}">
          <div class="segment-speaker">
            <span class="speaker-label" data-original="${escapeHtml(seg.speaker)}">${escapeHtml(getSpeakerName(seg.speaker))}</span>
            <span class="segment-time">${fmtTime(seg.start)} → ${fmtTime(seg.end)}</span>
          </div>
          <div class="segment-text" style="cursor:default">${escapeHtml(seg.text)}</div>
        </div>
      `).join('');
    } else {
      segmentsHTML = segments.map((seg, i) => `
        <div class="segment fade-in" data-speaker="${speakerNum(seg.speaker)}" onclick="seekToSegment(${i})">
          <div class="segment-speaker">
            <span class="speaker-label" data-original="${escapeHtml(seg.speaker)}" onclick="event.stopPropagation(); startRename(this)">${escapeHtml(getSpeakerName(seg.speaker))}</span>
            <span class="segment-time">${fmtTime(seg.start)} → ${fmtTime(seg.end)}</span>
            ${seg._edited ? '<span class="edited-badge">edited</span>' : ''}
          </div>
          <div class="segment-text" onclick="event.stopPropagation(); startEditSegment(this, ${i})">${escapeHtml(seg.text)}</div>
        </div>
      `).join('');
    }

    const filenameLabel = append && filename
      ? `<span class="result-meta" style="font-weight:500;color:var(--text-primary)">${escapeHtml(filename)}</span>` : '';

    const resultHTML = `
      <div class="result-header fade-in">
        ${filenameLabel}
        <span class="result-provider" data-p="${provider}">${provider}</span>
        <span class="result-meta">${segments.length} segments · ${result.duration_sec}s processing</span>
        ${!append ? `<div class="result-actions">
          <button class="btn-small" onclick="exportJSON()">Export JSON</button>
          <button class="btn-small" onclick="exportMarkdown()">Export Markdown</button>
          <button class="btn-small" onclick="copyText(this)">Copy Text</button>
        </div>` : ''}
      </div>
      ${!append ? `<div class="search-bar fade-in">
        <input type="text" class="search-input" id="searchInput" placeholder="Search transcript…" oninput="searchTranscript()" onkeydown="if(event.key==='Escape'){this.value='';searchTranscript();this.blur();}" />
        <span class="search-count" id="searchCount"></span>
      </div>` : ''}
      <div class="transcript" ${!append ? 'id="transcript"' : ''}>
        ${segmentsHTML}
      </div>
    `;

    if (append) {
      container.insertAdjacentHTML('beforeend', resultHTML);
    } else {
      container.innerHTML = resultHTML;
    }

    // Store for export
    window._lastResult = result;

    // Setup audio-transcript sync (single mode only)
    if (!append) setupPlaybackSync();
  }

  // ---- Transcript search ----
  function searchTranscript() {
    const input = document.getElementById('searchInput');
    const countEl = document.getElementById('searchCount');
    const transcript = document.getElementById('transcript');
    if (!input || !transcript || !window._lastResult) return;

    const query = input.value.trim();
    const segments = window._lastResult.segments || [];

    if (!query) {
      // Restore full transcript
      countEl.textContent = '';
      transcript.querySelectorAll('.segment').forEach((el, i) => {
        el.style.display = '';
        const textEl = el.querySelector('.segment-text');
        if (textEl && segments[i]) textEl.textContent = segments[i].text;
      });
      return;
    }

    const queryLower = query.toLowerCase();
    let matchCount = 0;

    transcript.querySelectorAll('.segment').forEach((el, i) => {
      const seg = segments[i];
      if (!seg) return;
      const textLower = seg.text.toLowerCase();
      if (textLower.includes(queryLower)) {
        el.style.display = '';
        matchCount++;
        // Highlight matches
        const textEl = el.querySelector('.segment-text');
        if (textEl) textEl.innerHTML = highlightMatches(seg.text, query);
      } else {
        el.style.display = 'none';
      }
    });

    countEl.textContent = matchCount === 1 ? '1 match' : `${matchCount} matches`;
  }

  function highlightMatches(text, query) {
    const escaped = escapeHtml(text);
    const queryEscaped = escapeHtml(query);
    // Case-insensitive replace on the escaped strings
    const regex = new RegExp(queryEscaped.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    return escaped.replace(regex, match => `<span class="search-highlight">${match}</span>`);
  }

  // ---- Inline segment editing ----
  function startEditSegment(el, index) {
    if (el.tagName === 'TEXTAREA') return;
    const seg = window._lastResult && window._lastResult.segments[index];
    if (!seg) return;

    const textarea = document.createElement('textarea');
    textarea.className = 'segment-text-edit';
    textarea.value = seg.text;
    el.replaceWith(textarea);
    textarea.focus();

    let committed = false;
    function commit() {
      if (committed) return;
      committed = true;
      const newText = textarea.value.trim();
      if (newText && newText !== seg.text) {
        seg.text = newText;
        seg._edited = true;
        updateHistorySegment(index, newText);
      }
      restoreSegmentText(textarea, index);
    }

    function cancel() {
      if (committed) return;
      committed = true;
      restoreSegmentText(textarea, index);
    }

    textarea.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    textarea.addEventListener('blur', commit);
  }

  function restoreSegmentText(textarea, index) {
    const seg = window._lastResult && window._lastResult.segments[index];
    if (!seg) return;

    const div = document.createElement('div');
    div.className = 'segment-text';
    div.textContent = seg.text;
    div.setAttribute('onclick', `startEditSegment(this, ${index})`);
    textarea.replaceWith(div);

    // Add or remove edited badge
    const segmentEl = div.closest('.segment');
    if (segmentEl) {
      const speakerDiv = segmentEl.querySelector('.segment-speaker');
      let badge = speakerDiv.querySelector('.edited-badge');
      if (seg._edited && !badge) {
        badge = document.createElement('span');
        badge.className = 'edited-badge';
        badge.textContent = 'edited';
        speakerDiv.appendChild(badge);
      }
    }

    // Re-apply search if active
    const searchInput = document.getElementById('searchInput');
    if (searchInput && searchInput.value.trim()) searchTranscript();
  }

  function updateHistorySegment(index, newText) {
    if (!currentHistoryId) return;
    const history = loadHistory();
    const entry = history.find(e => e.id === currentHistoryId);
    if (entry && entry.segments && entry.segments[index]) {
      entry.segments[index].text = newText;
      entry.segments[index]._edited = true;
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    }
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ---- Audio-transcript sync ----
  function seekToSegment(index) {
    const audioEl = document.getElementById('audioElement');
    const seg = window._lastResult && window._lastResult.segments[index];
    if (!seg || !audioEl.src) return;
    audioEl.currentTime = seg.start || 0;
    if (audioEl.paused) audioEl.play();
  }

  let _activePlaybackIndex = -1;

  function setupPlaybackSync() {
    const audioEl = document.getElementById('audioElement');
    if (audioEl._syncHandler) audioEl.removeEventListener('timeupdate', audioEl._syncHandler);
    _activePlaybackIndex = -1;

    audioEl._syncHandler = function() {
      const segments = window._lastResult && window._lastResult.segments;
      if (!segments) return;
      const transcript = document.getElementById('transcript');
      if (!transcript) return;

      const time = audioEl.currentTime;
      let newIndex = -1;
      for (let i = 0; i < segments.length; i++) {
        if (time >= (segments[i].start || 0) && time < (segments[i].end || 0)) {
          newIndex = i;
          break;
        }
      }

      if (newIndex === _activePlaybackIndex) return;

      const segEls = transcript.querySelectorAll('.segment');
      if (_activePlaybackIndex >= 0 && _activePlaybackIndex < segEls.length) {
        segEls[_activePlaybackIndex].classList.remove('active-playback');
      }
      if (newIndex >= 0 && newIndex < segEls.length) {
        segEls[newIndex].classList.add('active-playback');
        segEls[newIndex].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      _activePlaybackIndex = newIndex;
    };

    audioEl.addEventListener('timeupdate', audioEl._syncHandler);
  }

  // ---- Export functions ----
  function exportJSON() {
    if (!window._lastResult) return;
    const r = window._lastResult;
    const segments = (r.segments || []).map(seg => ({
      speaker: seg.speaker,
      start: seg.start,
      end: seg.end,
      text: seg.text,
    }));
    const speakers = {};
    for (const seg of segments) {
      if (!(seg.speaker in speakers)) {
        speakers[seg.speaker] = getSpeakerName(seg.speaker);
      }
    }
    const exported = {
      provider: r.provider,
      duration_sec: r.duration_sec,
      speakers,
      segments,
    };
    download(JSON.stringify(exported, null, 2), 'transcript.json', 'application/json');
  }

  function exportMarkdown() {
    if (!window._lastResult) return;
    const r = window._lastResult;
    let md = `# Meeting Transcript\n\n- **Provider:** ${r.provider}\n- **Processing time:** ${r.duration_sec}s\n\n---\n\n`;
    let currentSpeaker = null;
    for (const seg of r.segments) {
      const displayName = getSpeakerName(seg.speaker);
      if (seg.speaker !== currentSpeaker) {
        const mm = String(Math.floor((seg.start||0) / 60)).padStart(2,'0');
        const ss = String(Math.round((seg.start||0) % 60)).padStart(2,'0');
        md += `\n### ${displayName} \`${mm}:${ss}\`\n\n`;
        currentSpeaker = seg.speaker;
      }
      md += `${seg.text}\n\n`;
    }
    download(md, 'transcript.md', 'text/markdown');
  }

  function copyText(btn) {
    if (!window._lastResult) return;
    let text = '';
    let currentSpeaker = null;
    for (const seg of window._lastResult.segments) {
      if (seg.speaker !== currentSpeaker) {
        text += `\n[${getSpeakerName(seg.speaker)}]\n`;
        currentSpeaker = seg.speaker;
      }
      text += `${seg.text}\n`;
    }
    navigator.clipboard.writeText(text.trim());
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy Text', 1500);
  }

  function download(content, filename, mime) {
    const blob = new Blob([content], { type: mime });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ---- Theme toggle ----
  function getPreferredTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) return saved;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.getElementById('themeToggle').textContent = theme === 'dark' ? '\u263E' : '\u2600';
    document.getElementById('themeToggle').title = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyTheme(next);
  }

  // ---- Init ----
  applyTheme(getPreferredTheme());
  document.getElementById('serverUrl').value = window.location.origin;
  renderHistory();
  checkServer();
  setInterval(checkServer, 10000);
  document.getElementById('serverUrl').addEventListener('change', () => { checkServer(); });

