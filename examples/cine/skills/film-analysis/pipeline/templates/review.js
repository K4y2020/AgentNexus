(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('review-data').textContent);
  const $ = id => document.getElementById(id);
  const player = $('player'), scroll = $('timeline-scroll');
  const trackNames = {story: '剧情', shot: '镜头', dialogue: '对白'};
  const imageMap = new Map(data.images.map(image => [image.id, image]));
  let selected = null, repeatWindow = null, pixelsPerSecond = 1, frame = 0, localURL = null;
  let cueButtons = [];
  const time = seconds => {
    const tenths = Math.max(0, Math.floor(seconds * 10));
    return `${String(Math.floor(tenths / 600)).padStart(2, '0')}:${String(Math.floor(tenths / 10) % 60).padStart(2, '0')}.${tenths % 10}`;
  };
  const make = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  function tab(name) {
    for (const value of ['summary', 'detail']) {
      $(`${value}-panel`).hidden = name !== value;
      $(`${value}-tab`).setAttribute('aria-selected', String(name === value));
      $(`${value}-tab`).tabIndex = name === value ? 0 : -1;
    }
  }
  function seek(seconds) {
    if (player.readyState === 0) return;
    player.currentTime = Math.min(Math.max(0, seconds), Number.isFinite(player.duration) ? player.duration : seconds);
    sync();
  }
  function imageDetail(image) {
    const button = make('button', undefined, 'detail-image');
    button.type = 'button';
    const img = make('img');
    img.src = image.url;
    img.alt = `原片抽帧 ${time(image.start)}`;
    img.loading = 'lazy';
    button.append(img, make('span', `${time(image.start)} · ${image.id}`));
    button.addEventListener('click', () => {
      $('full-image').src = image.url;
      $('full-image').alt = img.alt;
      $('full-image-caption').textContent = `${time(image.start)} · ${image.id} · 抽帧证据`;
      $('image-dialog').showModal();
    });
    return button;
  }
  function select(cue, jump = true) {
    selected = cue;
    repeatWindow = null;
    $('replay').disabled = player.readyState === 0 || cue.type === 'evidence';
    $('detail-status').textContent = cue.status;
    $('detail-title').textContent = cue.title;
    $('detail-time').textContent = cue.type === 'evidence' ? time(cue.start) : `${time(cue.start)} → ${time(cue.end)} · ${(cue.end - cue.start).toFixed(1)} 秒${cue.timingNote ? ` · ${cue.timingNote} 原记录 ${cue.rawInterval.join('–')} 秒` : ''}`;
    $('detail-speaker').textContent = cue.speaker || '';
    $('detail-source').textContent = cue.provenance ? `对白来源：${cue.provenance}` : '';
    $('detail-text').textContent = cue.text || (cue.type === 'shot' ? '尚无逐镜描述' : '尚无剧情记录');
    const compare = $('detail-dialogue-compare');
    compare.replaceChildren();
    const compareRows = [['字幕', cue.subtitleText], ['ASR', cue.asrText]].filter(([, value]) => value);
    if (compareRows.length) {
      compare.hidden = false;
      for (const [label, value] of compareRows) compare.append(make('p', `${label}：${value}`, 'compare-row'));
      for (const conflict of cue.conflicts || []) compare.append(make('p', conflict, 'compare-conflict'));
    } else compare.hidden = true;
    $('detail-connection').textContent = cue.connection ? `承接：${cue.connection}` : '';
    $('detail-id').textContent = cue.sourceId;
    $('detail-uncertainties').replaceChildren(...(cue.uncertainties || []).map(text => make('li', text)));
    const images = (cue.imageIds || []).map(id => imageMap.get(id)).filter(Boolean);
    $('detail-images').replaceChildren(...(images.length ? images.map(imageDetail) : [make('p', '此区间暂无抽帧证据', 'empty')]));
    tab('detail');
    if (jump) { player.pause(); seek(cue.start); }
    sync();
  }
  function renderTimeline() {
    const duration = Math.max(data.duration, 1);
    const zoom = $('zoom').value;
    const width = Math.max(scroll.clientWidth, 1);
    pixelsPerSecond = width / (zoom === 'all' ? duration : Math.min(Number(zoom), duration));
    const timelineWidth = Math.max(width, duration * pixelsPerSecond);
    $('timeline').style.width = `${timelineWidth}px`;
    const query = $('search').value.trim().toLocaleLowerCase();
    const enabled = new Set([...document.querySelectorAll('[data-track]:checked')].map(el => el.dataset.track));
    $('tracks').replaceChildren(); $('lane-labels').replaceChildren(); $('ruler').replaceChildren();
    cueButtons = [];
    const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600].find(n => n * pixelsPerSecond >= 65) || 3600;
    for (let second = 0; second < duration; second += step) {
      const tick = make('span', time(second), 'tick');
      tick.style.left = `${second * pixelsPerSecond}px`;
      if ((duration - second) * pixelsPerSecond > 50) $('ruler').append(tick);
    }
    for (const [type, label] of Object.entries(trackNames)) {
      const all = data.cues.filter(cue => cue.type === type);
      $(`count-${type}`).textContent = String(all.length);
      if (!enabled.has(type)) continue;
      const cues = all.filter(cue => `${cue.title} ${cue.text} ${cue.sourceId} ${cue.speaker || ''}`.toLocaleLowerCase().includes(query));
      const lane = make('div', undefined, 'lane');
      const ends = [];
      for (const cue of cues) {
        let stack = ends.findIndex(end => end <= cue.start);
        if (stack < 0) stack = ends.length;
        ends[stack] = cue.end;
        const button = make('button', cue.title, `cue ${type}`);
        button.type = 'button'; button.dataset.cueId = cue.id;
        button.title = `${label} · ${time(cue.start)}–${time(cue.end)} · ${cue.title}`;
        button.setAttribute('aria-label', button.title);
        button.style.left = `${cue.start * pixelsPerSecond}px`;
        const cueWidth = Math.max(2, (cue.end - cue.start) * pixelsPerSecond - 2);
        button.style.width = `${cueWidth}px`;
        if (cueWidth < 20) { button.style.paddingInline = '0'; button.textContent = ''; }
        button.style.top = `${6 + stack * 31}px`;
        button.addEventListener('click', () => select(cue));
        lane.append(button); cueButtons.push({button, cue});
      }
      const height = 12 + Math.max(1, ends.length) * 31;
      lane.style.height = `${height}px`;
      if (!cues.length) lane.append(make('span', all.length ? '无匹配记录' : '尚无记录', 'empty'));
      const laneLabel = make('div', label, `lane-label ${type}`); laneLabel.style.height = `${height}px`;
      $('lane-labels').append(laneLabel); $('tracks').append(lane);
    }
    $('no-results').hidden = cueButtons.length > 0;
    sync();
  }
  function sync() {
    const current = player.currentTime || 0;
    $('clock').textContent = time(current);
    $('playhead').style.left = `${current * pixelsPerSecond}px`;
    for (const {button, cue} of cueButtons) {
      button.classList.toggle('active', current >= cue.start && current < cue.end);
      button.classList.toggle('selected', selected?.id === cue.id);
      button.setAttribute('aria-pressed', String(selected?.id === cue.id));
    }
    const ids = new Set(selected?.imageIds || []);
    document.querySelectorAll('.evidence-thumb').forEach(button => button.classList.toggle('active', ids.has(button.dataset.imageId)));
    if ($('follow').checked) {
      const x = current * pixelsPerSecond;
      if (x < scroll.scrollLeft || x > scroll.scrollLeft + scroll.clientWidth * .85) scroll.scrollLeft = Math.max(0, x - scroll.clientWidth * .2);
    }
  }
  function tick() {
    if (repeatWindow && player.currentTime >= repeatWindow.end) {
      if ($('loop').checked) seek(repeatWindow.start);
      else { player.pause(); seek(repeatWindow.end); repeatWindow = null; }
    }
    sync();
    if (!player.paused) frame = requestAnimationFrame(tick);
  }
  function playSelected() {
    if (!selected || selected.type === 'evidence') return;
    repeatWindow = {start: selected.start, end: selected.end};
    seek(selected.start);
    player.play().catch(() => { $('media-error').hidden = false; });
  }
  $('replay').addEventListener('click', playSelected);
  player.addEventListener('play', () => { cancelAnimationFrame(frame); tick(); });
  player.addEventListener('pause', () => { cancelAnimationFrame(frame); sync(); });
  player.addEventListener('seeked', sync);
  player.addEventListener('timeupdate', sync);
  player.addEventListener('ended', () => { if (repeatWindow && $('loop').checked) playSelected(); });
  player.addEventListener('loadedmetadata', () => {
    $('media-error').hidden = true;
    $('replay').disabled = !selected || selected.type === 'evidence';
    if (Math.abs(player.duration - data.duration) > 1) {
      $('media-error').textContent = '当前视频时长与索引不一致，请核对原片。'; $('media-error').hidden = false;
    }
    sync();
  });
  const mediaError = () => { $('media-error').textContent = '原片暂时无法播放，请重新选择本地原片。'; $('media-error').hidden = false; };
  player.addEventListener('error', mediaError);
  player.querySelector('source').addEventListener('error', mediaError);
  $('ruler').addEventListener('click', event => {
    repeatWindow = null;
    seek((event.clientX - $('ruler').getBoundingClientRect().left) / pixelsPerSecond);
  });
  $('zoom').addEventListener('change', renderTimeline);
  $('search').addEventListener('input', renderTimeline);
  document.querySelectorAll('[data-track]').forEach(input => input.addEventListener('change', renderTimeline));
  new ResizeObserver(renderTimeline).observe(scroll);
  for (const name of ['summary', 'detail']) {
    $(`${name}-tab`).addEventListener('click', () => tab(name));
    $(`${name}-tab`).addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const next = event.key === 'Home' ? 'summary' : event.key === 'End' ? 'detail' : name === 'summary' ? 'detail' : 'summary';
        tab(next); $(`${next}-tab`).focus();
      }
    });
  }
  for (const image of data.images) {
    const button = make('button', undefined, 'evidence-thumb'); button.type = 'button'; button.dataset.imageId = image.id;
    const img = make('img'); img.src = image.url; img.alt = `抽帧 ${time(image.start)}`; img.loading = 'lazy';
    button.append(img, make('span', time(image.start)));
    button.addEventListener('click', () => select({id: `evidence:${image.id}`, sourceId: image.id,
      title: `抽帧 ${time(image.start)}`, type: 'evidence', start: image.start, end: image.end,
      text: '代表性静帧', status: '抽帧证据 · 待核验', imageIds: [image.id]}));
    $('evidence-strip').append(button);
  }
  $('evidence-count').textContent = `${data.images.length} 张`;
  if (!data.images.length) $('evidence-strip').append(make('p', '暂无可用截图', 'empty'));
  $('local-video').addEventListener('change', event => {
    const file = event.target.files[0]; if (!file) return;
    player.pause(); repeatWindow = null;
    if (localURL) URL.revokeObjectURL(localURL);
    localURL = URL.createObjectURL(file); player.src = localURL;
  });
  $('close-image').addEventListener('click', () => $('image-dialog').close());
  $('image-dialog').addEventListener('click', event => { if (event.target === $('image-dialog')) $('image-dialog').close(); });
  renderTimeline();
})();
