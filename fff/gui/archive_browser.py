# fff: run as 'fff web'; the __main__ block became parse_args(argv) + main(argv).
"""Local web browser for searching and previewing archived e621 posts."""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_file

from fff.archive import generic_tags
from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql


app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

PAGE_SIZE = 48
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v", "avi", "mkv"}
# Media-type filter. posts.file_type holds the category ingest derived from
# file_url, but deleted posts keep the post-page URL there (no extension) and
# end up 'unknown', so for anything outside these categories fall back to the
# extension of the local file_path.
MEDIA_EXTENSIONS = {
    "image": ("jpg", "jpeg", "png", "bmp", "webp", "avif", "heic", "heif", "tif", "tiff"),
    "gif": ("gif",),
    "video": tuple(sorted(VIDEO_EXTENSIONS)),
}
MEDIA_TYPES = tuple(MEDIA_EXTENSIONS)
_PATH_EXTENSION = "LOWER(SUBSTRING_INDEX(p.file_path, '.', -1))"


def _placeholders(items):
    return ", ".join(["%s"] * len(items))


def media_filter(media):
    """WHERE clause and values for one media-type choice, or None for no filter."""
    known = f"COALESCE(p.file_type, '') NOT IN ({_placeholders(MEDIA_TYPES)})"
    if media in MEDIA_EXTENSIONS:
        exts = MEDIA_EXTENSIONS[media]
        return (f"(p.file_type = %s OR ({known} AND {_PATH_EXTENSION} IN ({_placeholders(exts)})))",
                [media, *MEDIA_TYPES, *exts])
    if media == "other":
        exts = [ext for group in MEDIA_EXTENSIONS.values() for ext in group]
        return (f"({known} AND (p.file_path IS NULL OR {_PATH_EXTENSION} NOT IN ({_placeholders(exts)})))",
                [*MEDIA_TYPES, *exts])
    return None


PAGE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local E621 Archive</title>
  <style>
    :root { --ink:#f1f3f5; --muted:#a8b4c2; --line:#5d6572; --paper:#1d2321; --panel:#33365f; --accent:#de3c2e; --accent-dark:#a8b4c2; --teal:#a8b4c2; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:radial-gradient(circle at 50% -10%, #33365f 0, transparent 34rem), var(--paper); font:15px/1.45 Georgia, serif; }
    header { padding:32px 24px 26px; border-bottom:1px solid var(--line); background:#1d2321ee; text-align:center; }
    h1 { margin:0; font:700 clamp(28px, 4vw, 48px)/1.05 "Trebuchet MS", sans-serif; letter-spacing:0; }
    .subtitle { color:var(--muted); margin:8px 0 0; }
    main { max-width:1560px; margin:0 auto; padding:28px 24px 50px; }
    .controls { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin-bottom:18px; }
    .controls > div { flex:1 1 150px; }
    .controls > .search { flex:3 1 260px; }
    .controls > button { flex:0 0 auto; width:auto; padding-inline:18px; }
    .layout { display:grid; grid-template-columns:minmax(0, 1fr) 300px; gap:24px; align-items:start; }
    .sidebar { position:sticky; top:16px; max-height:calc(100vh - 32px); overflow-y:auto; }
    label { display:block; color:var(--muted); font:600 12px "Trebuchet MS", sans-serif; text-transform:uppercase; letter-spacing:.04em; }
    input, select, button { width:100%; min-height:42px; padding:9px 11px; border:1px solid var(--line); border-radius:4px; background:#1d2321; color:var(--ink); font:inherit; }
    button { cursor:pointer; background:var(--teal); border-color:var(--teal); color:white; font-family:"Trebuchet MS", sans-serif; font-weight:700; }
    button:hover { background:#de3c2e; border-color:#de3c2e; }
    .stats { display:flex; gap:18px; align-items:center; min-height:29px; color:var(--muted); }
    .stats strong { color:var(--ink); }
    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:14px; }
    .card { overflow:hidden; border:1px solid var(--line); border-radius:6px; background:var(--panel); box-shadow:0 8px 24px #00000040; }
    .card-open { display:block; text-decoration:none; }
    .preview { display:block; width:100%; aspect-ratio:1.15; object-fit:cover; background:#1d2321; }
    .missing { display:grid; place-items:center; color:var(--muted); font-family:"Trebuchet MS", sans-serif; }
    .body { padding:11px 12px 13px; }
    .topline { display:flex; justify-content:space-between; gap:8px; align-items:center; font-family:"Trebuchet MS", sans-serif; }
    .id { color:var(--teal); font-weight:700; }
    .rating { padding:2px 6px; border-radius:3px; color:white; background:var(--accent); font-size:11px; text-transform:uppercase; }
    .rating.safe { background:#2f9e44; }
    .rating.questionable { background:#f0c419; color:#1d2321; }
    .artists { margin:7px 0 3px; color:var(--accent-dark); font-weight:700; overflow-wrap:anywhere; }
    .meta { color:var(--muted); font-size:13px; }
    .deleted { margin-top:8px; padding:6px 8px; border-left:3px solid var(--accent); background:#de3c2e18; color:var(--accent); font-size:13px; }
    .links { display:flex; flex-wrap:wrap; gap:4px 12px; margin-top:8px; font-family:"Trebuchet MS", sans-serif; font-size:12px; }
    .links button { width:auto; min-height:0; padding:0; border:0; background:none; color:var(--teal); font:inherit; font-weight:400; text-decoration:underline; }
    .links button:hover { background:none; color:var(--accent); }
    a { color:var(--teal); }
    .pager { display:flex; justify-content:center; align-items:center; gap:14px; margin-top:24px; }
    .pager button { width:auto; min-width:90px; }
    .empty { padding:50px 15px; text-align:center; color:var(--muted); }
    .trending { padding:14px; border:1px solid var(--line); border-radius:6px; background:#33365f80; }
    .trending-head { display:flex; justify-content:space-between; align-items:center; gap:12px; }
    .trending h2 { margin:0; color:var(--muted); font:600 12px "Trebuchet MS", sans-serif; text-transform:uppercase; letter-spacing:.04em; }
    .trending select { width:auto; min-height:0; padding:4px 8px; font-size:13px; }
    .trend-group { margin-top:14px; }
    .trend-group h3 { margin:0 0 4px; padding-bottom:4px; border-bottom:1px solid var(--line); font:700 14px "Trebuchet MS", sans-serif; }
    .trend-group ol { margin:0; padding:0; list-style:none; }
    .trending .note { margin:6px 0 0; color:var(--muted); font-size:13px; }
    .trend { display:flex; align-items:baseline; gap:8px; width:100%; min-height:0; padding:4px 6px; border:0; border-radius:4px; background:none; color:var(--ink); font:400 14px Georgia, serif; text-align:left; }
    .trend:hover { background:#1d2321aa; border-color:transparent; }
    .trend.active { background:#de3c2e2a; }
    .trend .rank { flex:none; width:1.5em; text-align:right; color:var(--muted); font:700 12px "Trebuchet MS", sans-serif; }
    .trend .tname { flex:1; min-width:0; overflow-wrap:anywhere; }
    .trend .tcount { flex:none; color:var(--accent); font:700 12px "Trebuchet MS", sans-serif; }
    @media (max-width:1000px) { .layout { grid-template-columns:1fr; } .sidebar { position:static; max-height:none; order:-1; } .trend-groups { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:0 20px; } }
    .search { position:relative; }
    .suggest { position:absolute; z-index:10; top:100%; left:0; right:0; margin:4px 0 0; padding:4px 0; list-style:none; border:1px solid var(--line); border-radius:4px; background:#1d2321; box-shadow:0 10px 28px #00000080; max-height:360px; overflow-y:auto; }
    .suggest[hidden] { display:none; }
    .suggest li { display:flex; gap:10px; align-items:baseline; padding:7px 11px; cursor:pointer; }
    .suggest li.active, .suggest li:hover { background:var(--panel); }
    .suggest .name { flex:1; overflow-wrap:anywhere; }
    .suggest .name mark { background:none; color:var(--accent); font-weight:700; }
    .suggest .kind, .suggest .count { color:var(--muted); font:12px "Trebuchet MS", sans-serif; }
    .suggest .kind.artist { color:var(--accent); }
    @media (max-width:700px) { .controls > .search { flex-basis:100%; } .grid { grid-template-columns:repeat(auto-fill, minmax(160px, 1fr)); gap:9px; } }
  </style>
</head>
<body>
  <header><h1>Local E621 Archive</h1><p class="subtitle">Browse the files and metadata already in your archive.</p></header>
  <main>
    <form id="filters" class="controls">
      <div class="search"><label for="q">Search</label><input id="q" name="q" placeholder="artist, tag, filename, or post ID" autocomplete="off" role="combobox" aria-autocomplete="list" aria-controls="suggest" aria-expanded="false"><ul id="suggest" class="suggest" role="listbox" hidden></ul></div>
      <div><label for="rating">Rating</label><select id="rating"><option value="">All ratings</option><option>safe</option><option>questionable</option><option>explicit</option></select></div>
      <div><label for="media">Media type</label><select id="media"><option value="">All media</option><option value="image">Images</option><option value="gif">GIFs</option><option value="video">Videos</option><option value="other">Other</option></select></div>
      <div><label for="deleted">Status</label><select id="deleted"><option value="">All posts</option><option value="0">Active</option><option value="1">Deleted</option></select></div>
    <div><label for="sort">Sort by</label><select id="sort"><option value="created_at_desc">Created: newest</option><option value="created_at_asc">Created: oldest</option><option value="deleted_at_desc">Deleted: newest</option><option value="deleted_at_asc">Deleted: oldest</option></select></div>
      <button type="submit">Search archive</button>
    </form>
    <div class="layout">
      <div class="results">
        <div class="stats" id="stats">Loading archive...</div>
        <section class="grid" id="grid"></section>
        <div class="pager"><button id="prev">Previous</button><span id="page"></span><button id="next">Next</button></div>
      </div>
      <aside class="sidebar">
        <section class="trending" aria-labelledby="trending-title">
          <div class="trending-head"><h2 id="trending-title">Trending</h2><select id="trending-days" aria-label="Trending window"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select></div>
          <div class="trend-groups" id="trending"><p class="note">Loading...</p></div>
        </section>
      </aside>
    </div>
  </main>
  <script>
    let currentPage = 1;
    const canOpen = {{ 'true' if can_open else 'false' }};
        const initialSearch = new URLSearchParams(window.location.search).get('q') || '';
        document.querySelector('#q').value = initialSearch;
    const params = () => new URLSearchParams({ q: document.querySelector('#q').value.trim(), rating: document.querySelector('#rating').value, media: document.querySelector('#media').value, deleted: document.querySelector('#deleted').value, sort: document.querySelector('#sort').value, page: currentPage });
    const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    function card(post) {
      const media = post.has_file ? (post.is_video ? `<video class="preview" controls preload="metadata" src="${post.media_url}"></video>` : `<img class="preview" loading="lazy" src="${post.media_url}" alt="Post ${post.id}">`) : '<div class="preview missing">File unavailable</div>';
      const deleted = post.deleted ? `<div class="deleted">Deleted${post.deletion_reason ? ': ' + esc(post.deletion_reason) : ''}</div>` : '';
    return `<article class="card"><a class="card-open" href="/post/${post.id}">${media}</a><div class="body"><div class="topline"><a class="id" href="/post/${post.id}">#${post.id}</a><span class="rating ${esc(post.rating)}">${esc(post.rating)}</span></div><div class="artists">${esc(post.artists || 'Unknown artist')}</div><div class="meta">${esc(post.file_name || 'No filename')}</div>${deleted}<div class="links">${post.file_url ? `<a href="${esc(post.file_url)}" target="_blank" rel="noreferrer">Open source</a>` : ''}${post.has_file && canOpen ? `<button type="button" data-open="${post.id}" data-action="open">Open file</button><button type="button" data-open="${post.id}" data-action="reveal">Show in folder</button>` : ''}</div></div></article>`;
    }
    async function openLocal(button) {
      const label = button.textContent;
      try {
        const response = await fetch(`/api/open/${button.dataset.open}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: button.dataset.action }) });
        if (response.ok) return;
        button.textContent = (await response.json().catch(() => ({}))).error || 'Could not open';
      } catch { button.textContent = 'Could not open'; }
      setTimeout(() => { button.textContent = label; }, 3000);
    }
    document.querySelector('#grid').addEventListener('click', event => { const button = event.target.closest('button[data-open]'); if (button) openLocal(button); });
    async function load() {
      const grid = document.querySelector('#grid'); grid.innerHTML = '<div class="empty">Loading...</div>';
      markActiveTrend();
      const response = await fetch('/api/posts?' + params()); const data = await response.json();
      if (!response.ok) { grid.innerHTML = `<div class="empty">${esc(data.error || 'Unable to load posts')}</div>`; return; }
      document.querySelector('#stats').innerHTML = `<strong>${data.total.toLocaleString()}</strong> matching posts`;
      grid.innerHTML = data.posts.length ? data.posts.map(card).join('') : '<div class="empty">No posts matched those filters.</div>';
      document.querySelector('#page').textContent = `Page ${data.page} of ${Math.max(1, data.pages)}`;
      document.querySelector('#prev').disabled = data.page <= 1; document.querySelector('#next').disabled = data.page >= data.pages;
    }
    document.querySelector('#filters').addEventListener('submit', event => { event.preventDefault(); hideSuggestions(); currentPage = 1; load(); });
    const q = document.querySelector('#q'), list = document.querySelector('#suggest');
    let suggestions = [], active = -1, suggestTimer = null, suggestSeq = 0;
    function hideSuggestions() { clearTimeout(suggestTimer); suggestSeq++; list.hidden = true; q.setAttribute('aria-expanded', 'false'); suggestions = []; active = -1; }
    function highlight(name, term) {
      const i = name.toLowerCase().indexOf(term);
      return i < 0 ? esc(name) : esc(name.slice(0, i)) + '<mark>' + esc(name.slice(i, i + term.length)) + '</mark>' + esc(name.slice(i + term.length));
    }
    function renderSuggestions(term) {
      if (!suggestions.length) { hideSuggestions(); return; }
      list.innerHTML = suggestions.map((s, i) => `<li role="option" data-i="${i}" class="${i === active ? 'active' : ''}"><span class="name">${highlight(s.name, term)}</span><span class="kind ${esc(s.type)}">${esc(s.type)}</span><span class="count">${s.count.toLocaleString()}</span></li>`).join('');
      list.hidden = false; q.setAttribute('aria-expanded', 'true');
      list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
    }
    function pick(i) { q.value = suggestions[i].name; hideSuggestions(); currentPage = 1; load(); }
    const trendingList = document.querySelector('#trending'), trendingDays = document.querySelector('#trending-days');
    function markActiveTrend() { trendingList.querySelectorAll('.trend').forEach(b => b.classList.toggle('active', b.dataset.tag === q.value.trim())); }
    const trendEmpty = { fetish: 'Nothing trending. Tags only show here once marked fetish (key F) in the Generic tags tool.' };
    function trendItem(t, i, data) {
      const tip = `${t.recent.toLocaleString()} of ${data.recent_posts.toLocaleString()} posts archived in the last ${data.days} days (${t.total.toLocaleString()} all-time), ${t.lift}x its usual rate`;
      return `<li><button type="button" class="trend" data-tag="${esc(t.name)}" title="${esc(tip)}"><span class="rank">${i + 1}</span><span class="tname">${esc(t.name.replace(/_/g, ' '))}</span><span class="tcount">${t.recent.toLocaleString()}</span></button></li>`;
    }
    async function loadTrending() {
      trendingList.innerHTML = '<p class="note">Loading...</p>';
      try {
        const response = await fetch('/api/trending?' + new URLSearchParams({ days: trendingDays.value })); const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        if (!data.recent_posts) { trendingList.innerHTML = `<p class="note">No posts archived in the last ${data.days} days.</p>`; return; }
        trendingList.innerHTML = data.categories.map(cat => `<section class="trend-group"><h3>${esc(cat.title)}</h3>${cat.items.length
          ? `<ol>${cat.items.map((t, i) => trendItem(t, i, data)).join('')}</ol>`
          : `<p class="note">${esc(trendEmpty[cat.key] || 'Nothing stands out.')}</p>`}</section>`).join('');
        markActiveTrend();
      } catch { trendingList.innerHTML = '<p class="note">Trending is unavailable right now.</p>'; }
    }
    trendingList.addEventListener('click', event => { const b = event.target.closest('.trend'); if (b) { q.value = b.dataset.tag; hideSuggestions(); currentPage = 1; load(); } });
    trendingDays.addEventListener('change', loadTrending);
    q.addEventListener('input', () => {
      clearTimeout(suggestTimer);
      const term = q.value.trim().toLowerCase().replace(/ /g, '_');
      if (term.length < 2) { hideSuggestions(); return; }
      suggestTimer = setTimeout(async () => {
        const seq = ++suggestSeq;
        try {
          const data = await (await fetch('/api/suggest?' + new URLSearchParams({ q: term }))).json();
          if (seq !== suggestSeq) return;  // a newer keystroke already took over
          suggestions = data.suggestions || []; active = -1; renderSuggestions(term);
        } catch { hideSuggestions(); }
      }, 150);
    });
    q.addEventListener('keydown', event => {
      if (list.hidden) return;
      const term = q.value.trim().toLowerCase().replace(/ /g, '_');
      if (event.key === 'ArrowDown') { event.preventDefault(); active = (active + 1) % suggestions.length; renderSuggestions(term); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); active = active <= 0 ? suggestions.length - 1 : active - 1; renderSuggestions(term); }
      else if ((event.key === 'Enter' || event.key === 'Tab') && active >= 0) { event.preventDefault(); pick(active); }
      else if (event.key === 'Escape') { hideSuggestions(); }
    });
    list.addEventListener('mousedown', event => { const li = event.target.closest('li'); if (li) { event.preventDefault(); pick(+li.dataset.i); } });
    q.addEventListener('blur', () => setTimeout(hideSuggestions, 100));
    document.querySelector('#prev').addEventListener('click', () => { if (currentPage > 1) { currentPage--; load(); } });
    document.querySelector('#next').addEventListener('click', () => { currentPage++; load(); });
    load();
    loadTrending();
  </script>
</body>
</html>
"""

DETAIL_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Post {{ post.id }} | Local E621 Archive</title>
    <style>
        :root { --ink:#f1f3f5; --muted:#a8b4c2; --line:#5d6572; --paper:#1d2321; --panel:#33365f; --accent:#de3c2e; --teal:#a8b4c2; }
        * { box-sizing:border-box; }
        body { margin:0; color:var(--ink); background:radial-gradient(circle at 50% -10%, #33365f 0, transparent 34rem), var(--paper); font:15px/1.45 Georgia, serif; }
        header { padding:24px; border-bottom:1px solid var(--line); background:#1d2321ee; text-align:center; }
        header a { color:var(--teal); font:700 14px "Trebuchet MS", sans-serif; text-decoration:none; }
        main { max-width:1280px; margin:0 auto; padding:28px 24px 50px; }
        .layout { display:grid; grid-template-columns:minmax(0, 1.35fr) minmax(300px, .65fr); gap:24px; align-items:start; }
        .media-panel, .info-panel { border:1px solid var(--line); border-radius:6px; background:var(--panel); box-shadow:0 8px 24px #00000040; }
        .media-panel { display:grid; place-items:center; min-height:420px; padding:14px; background:#1d2321; }
        .media-panel img, .media-panel video { display:block; max-width:100%; max-height:78vh; object-fit:contain; }
        .missing { color:var(--muted); font-family:"Trebuchet MS", sans-serif; }
        .info-panel { padding:20px; }
        h1 { margin:0; font:700 clamp(26px, 4vw, 42px)/1.05 "Trebuchet MS", sans-serif; }
        h2 { margin:24px 0 8px; border-bottom:1px solid var(--line); padding-bottom:6px; font:700 16px "Trebuchet MS", sans-serif; text-transform:uppercase; }
        .subhead { margin:7px 0 0; color:var(--muted); }
        .rating { display:inline-block; margin-top:14px; padding:4px 8px; border-radius:3px; color:white; background:var(--accent); font:700 12px "Trebuchet MS", sans-serif; text-transform:uppercase; }
        .rating.safe { background:#2f9e44; }
        .rating.questionable { background:#f0c419; color:#1d2321; }
        .deleted { margin-top:16px; padding:10px; border-left:3px solid var(--accent); background:#de3c2e18; color:var(--accent); }
        .tags { display:flex; flex-wrap:wrap; gap:6px; }
        .tag { display:inline-block; padding:4px 7px; border:1px solid var(--line); border-radius:3px; background:#1d2321; color:var(--teal); text-decoration:none; font-size:13px; overflow-wrap:anywhere; }
        .tag.artist { background:#de3c2e18; color:var(--accent); }
        dl { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin:0; }
        dt { color:var(--muted); }
        dd { margin:0; overflow-wrap:anywhere; }
        a { color:var(--teal); }
        .sources { margin:0; padding-left:20px; overflow-wrap:anywhere; }
        .local-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:12px; }
        .local-actions button { padding:7px 12px; border:1px solid var(--teal); border-radius:4px; background:var(--teal); color:white; font:700 13px "Trebuchet MS", sans-serif; cursor:pointer; }
        .local-actions button:hover { background:var(--accent); border-color:var(--accent); }
        .open-status { color:var(--accent); font-size:13px; }
        @media (max-width:850px) { .layout { grid-template-columns:1fr; } .media-panel { min-height:260px; } }
    </style>
</head>
<body>
    <header><a href="/">&larr; Back to archive</a></header>
    <main>
        <div class="layout">
            <section class="media-panel">
                {% if post.has_file %}
                    {% if post.is_video %}<video controls autoplay loop src="{{ post.media_url }}"></video>{% else %}<img src="{{ post.media_url }}" alt="Post {{ post.id }}">{% endif %}
                {% else %}<div class="missing">The local file is unavailable.</div>{% endif %}
            </section>
            <aside class="info-panel">
                <h1>Post #{{ post.id }}</h1>
                <p class="subhead">{{ post.file_name or 'No filename recorded' }}</p>
                <span class="rating {{ post.rating or '' }}">{{ post.rating or 'unknown' }}</span>
                {% if post.deleted %}<div class="deleted"><strong>Deleted post</strong>{% if post.deletion_reason %}<br>{{ post.deletion_reason }}{% endif %}</div>{% endif %}
                <h2>Artists</h2>
                <div class="tags">{% for artist in artists %}<a class="tag artist" href="/?q={{ artist|urlencode }}">{{ artist }}</a>{% else %}<span>None recorded</span>{% endfor %}</div>
                {% for category, category_tags in tags.items() %}
                    <h2>{{ category|title }}</h2>
                    <div class="tags">{% for tag in category_tags %}<a class="tag" href="/?q={{ tag|urlencode }}">{{ tag }}</a>{% endfor %}</div>
                {% endfor %}
                <h2>File Details</h2>
                <dl><dt>Path</dt><dd>{{ post.file_path or 'Unavailable' }}</dd><dt>Type</dt><dd>{{ post.file_type or 'Unknown' }}</dd><dt>MD5</dt><dd>{{ post.md5 or 'Unavailable' }}</dd><dt>Deleted at</dt><dd>{{ post.deleted_at or 'Not deleted' }}</dd></dl>
                {% if post.has_file and can_open %}
                <div class="local-actions"><button type="button" data-action="open">Open file</button><button type="button" data-action="reveal">Show in folder</button><span class="open-status" id="open-status"></span></div>
                {% endif %}
                <h2>Links</h2>
                <p><a href="https://e621.net/posts/{{ post.id }}" target="_blank" rel="noreferrer">View on e621</a></p>
                {% if sources %}<ul class="sources">{% for source in sources %}<li><a href="{{ source }}" target="_blank" rel="noreferrer">{{ source }}</a></li>{% endfor %}</ul>{% endif %}
            </aside>
        </div>
    </main>
    {% if post.has_file and can_open %}
    <script>
        document.querySelector('.local-actions').addEventListener('click', async event => {
            const button = event.target.closest('button[data-action]'), status = document.querySelector('#open-status');
            if (!button) return;
            status.textContent = '';
            try {
                const response = await fetch('/api/open/{{ post.id }}', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: button.dataset.action }) });
                if (!response.ok) status.textContent = (await response.json().catch(() => ({}))).error || 'Could not open the file';
            } catch { status.textContent = 'Could not open the file'; }
        });
    </script>
    {% endif %}
</body>
</html>
"""


# e621's order on a post page: the specific sections first, the long General list
# after them, then Meta. (Alphabetical put Species last, under 50+ General tags.)
TAG_SECTION_ORDER = ("copyright", "character", "species", "general", "lore", "meta")


def tag_section_rank(category):
    category = (category or "").lower()
    if category in TAG_SECTION_ORDER:
        return TAG_SECTION_ORDER.index(category), category
    return len(TAG_SECTION_ORDER), category  # unknown types go last, alphabetically


def database_connection():
    config = load_config()
    connection = connect_to_mysql(config.get("mysql", {}))
    if connection is None:
        raise RuntimeError("Could not connect to the configured MySQL database")
    return connection


def post_filters(args):
    where = []
    values = []
    query = args.get("q", "").strip()
    rating = args.get("rating", "").strip().lower()
    deleted = args.get("deleted", "").strip()
    media = args.get("media", "").strip().lower()

    if query:
        like = f"%{query}%"
        # Resolve matching tags/artists first and look their posts up by id; a
        # per-post EXISTS re-scans the tag names for every post (~400x slower).
        text_match = ("p.file_name LIKE %s OR p.deletion_reason LIKE %s"
                      " OR p.id IN (SELECT qa.post_id FROM posts_artists qa JOIN artists qar ON qar.artist_id = qa.artist_id WHERE qar.name LIKE %s)"
                      " OR p.id IN (SELECT qt.post_id FROM posts_tags qt JOIN tags qtag ON qtag.tag_id = qt.tag_id WHERE qtag.name LIKE %s)")
        if query.isdecimal():
            where.append(f"(p.id = %s OR {text_match})")
            values.append(int(query))
        else:
            where.append(f"({text_match})")
        values.extend([like, like, like, like])
    if rating in {"safe", "questionable", "explicit"}:
        where.append("p.rating = %s")
        values.append(rating)
    media_clause = media_filter(media)
    if media_clause:
        where.append(media_clause[0])
        values.extend(media_clause[1])
    if deleted in {"0", "1"}:
        where.append("p.deleted = %s")
        values.append(int(deleted))
    return (" WHERE " + " AND ".join(where)) if where else "", values


def post_sort(args):
    sort_options = {
        "created_at_desc": "p.created_at DESC, p.id DESC",
        "created_at_asc": "p.created_at IS NULL, p.created_at ASC, p.id ASC",
        "deleted_at_desc": "p.deleted_at IS NULL, p.deleted_at DESC, p.id DESC",
        "deleted_at_asc": "p.deleted_at IS NULL, p.deleted_at ASC, p.id ASC",
    }
    return sort_options.get(args.get("sort", "created_at_desc"), sort_options["created_at_desc"])


@app.get("/")
def index():
    return render_template_string(PAGE, can_open=is_local_request())


@app.get("/post/<int:post_id>")
def post_detail(post_id):
    connection = None
    try:
        connection = database_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT id, file_name, file_path, file_url, file_type, md5, rating,
                   deleted, deleted_at, deletion_reason
            FROM posts
            WHERE id = %s
        """, (post_id,))
        row = cursor.fetchone()
        if not row:
            abort(404)

        cursor.execute("""
            SELECT a.name
            FROM posts_artists pa
            JOIN artists a ON a.artist_id = pa.artist_id
            WHERE pa.post_id = %s
            ORDER BY a.name
        """, (post_id,))
        artists = [artist[0] for artist in cursor.fetchall()]

        cursor.execute("""
            SELECT t.tag_type, t.name
            FROM posts_tags pt
            JOIN tags t ON t.tag_id = pt.tag_id
            WHERE pt.post_id = %s
            ORDER BY t.tag_type, t.name
        """, (post_id,))
        tags = {}
        for category, tag in cursor.fetchall():
            tags.setdefault(category or "other", []).append(tag)
        tags = dict(sorted(tags.items(), key=lambda item: tag_section_rank(item[0])))

        cursor.execute("""
            SELECT source_link
            FROM source_links
            WHERE post_id = %s
            ORDER BY source_link
        """, (post_id,))
        sources = [source[0] for source in cursor.fetchall()]
        cursor.close()
    except Exception:
        if connection:
            connection.close()
        raise
    finally:
        if connection:
            connection.close()

    file_path = row[2]
    exists = bool(file_path and os.path.isfile(file_path))
    extension = Path(file_path).suffix.lower().lstrip(".") if file_path else ""
    post = {
        "id": row[0], "file_name": row[1], "file_path": file_path,
        "file_url": row[3], "file_type": row[4], "md5": row[5],
        "rating": row[6], "deleted": bool(row[7]), "deleted_at": row[8],
        "deletion_reason": row[9], "has_file": exists,
        "is_video": extension in VIDEO_EXTENSIONS,
        "media_url": f"/media/{post_id}" if exists else None,
    }
    return render_template_string(DETAIL_PAGE, post=post, artists=artists, tags=tags, sources=sources,
                                  can_open=is_local_request())


@app.get("/api/posts")
def posts():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    where, values = post_filters(request.args)
    order_by = post_sort(request.args)
    try:
        connection = database_connection()
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM posts p{where}", values)
        total = cursor.fetchone()[0]
        cursor.execute(f"""
            SELECT p.id, p.file_name, p.file_path, p.file_url, p.rating,
                   p.deleted, p.deletion_reason,
                   GROUP_CONCAT(DISTINCT a.name ORDER BY a.name SEPARATOR ', ') AS artists
            FROM posts p
            LEFT JOIN posts_artists pa ON pa.post_id = p.id
            LEFT JOIN artists a ON a.artist_id = pa.artist_id
            {where}
            GROUP BY p.id, p.file_name, p.file_path, p.file_url, p.rating, p.deleted, p.deletion_reason
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """, values + [PAGE_SIZE, (page - 1) * PAGE_SIZE])
        rows = cursor.fetchall()
        cursor.close()
        connection.close()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    result = []
    for post_id, file_name, file_path, file_url, rating, deleted, reason, artists in rows:
        # Avoid blocking the list view on slow or unavailable network drives.
        # The media endpoint performs the authoritative existence check on demand.
        exists = bool(file_path)
        extension = Path(file_path).suffix.lower().lstrip(".") if file_path else ""
        result.append({
            "id": post_id, "file_name": file_name, "file_url": file_url,
            "rating": rating or "unknown", "deleted": bool(deleted),
            "deletion_reason": reason, "artists": artists, "has_file": exists,
            "is_video": extension in VIDEO_EXTENSIONS,
            "media_url": f"/media/{post_id}" if exists else None,
        })
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return jsonify({"posts": result, "total": total, "page": page, "pages": pages})


def like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.get("/api/suggest")
def suggest():
    # Tags are stored with underscores; let people type spaces instead.
    term = request.args.get("q", "").strip().lower().replace(" ", "_")
    if len(term) < 2:
        return jsonify({"suggestions": []})
    prefix = like_escape(term) + "%"
    contains = "%" + like_escape(term) + "%"
    # Prefix matches first, then substring matches, each ranked by how many
    # archived posts use the name.
    queries = (
        """
            SELECT a.name, 'artist', COUNT(*) AS uses
            FROM artists a JOIN posts_artists pa ON pa.artist_id = a.artist_id
            WHERE a.name LIKE %s
            GROUP BY a.artist_id, a.name
            ORDER BY a.name NOT LIKE %s, uses DESC LIMIT %s
        """,
        """
            SELECT t.name, COALESCE(t.tag_type, 'general'), COUNT(*) AS uses
            FROM tags t JOIN posts_tags pt ON pt.tag_id = t.tag_id
            WHERE t.name LIKE %s
            GROUP BY t.tag_id, t.name, t.tag_type
            ORDER BY t.name NOT LIKE %s, uses DESC LIMIT %s
        """,
    )
    candidates = []
    try:
        connection = database_connection()
        cursor = connection.cursor()
        for sql in queries:
            cursor.execute(sql, (contains, prefix, 25))
            candidates.extend(cursor.fetchall())
        cursor.close()
        connection.close()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    seen = set()
    suggestions = []
    for name, kind, count in sorted(candidates, key=lambda c: (not c[0].lower().startswith(term), -c[2], c[0])):
        if name in seen:
            continue
        seen.add(name)
        suggestions.append({"name": name, "type": kind, "count": count})
        if len(suggestions) >= 12:
            break
    return jsonify({"suggestions": suggestions})


def post_file_path(post_id):
    """The post's local file path if it exists on disk, else None."""
    connection = database_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT file_path FROM posts WHERE id = %s", (post_id,))
        row = cursor.fetchone()
        cursor.close()
    finally:
        connection.close()
    if not row or not row[0] or not os.path.isfile(row[0]):
        return None
    return row[0]


TRENDING_WINDOWS = (7, 30, 90)  # days; the UI offers these
TRENDING_LIMIT = 10
TRENDING_CACHE_SECONDS = 600
# One top-10 list each, in sidebar order. Character and species are e621 tag types;
# e621 files fetish tags under General, so that list only holds tags marked fetish in
# the Generic tags tool (fff gui generic-tags). Tags marked generic there never trend.
TRENDING_CATEGORIES = (("artist", "Artists"), ("character", "Characters"), ("species", "Species"),
                       ("fetish", "Fetish"))
_trending_cache = {}


def trending(cursor, days, now=None, limit=TRENDING_LIMIT):
    """Artists and tags that show up far more in recently archived posts than usual.

    Raw recent counts just repeat the all-time leaders (mammal, canine, ...),
    so everything is ranked by lift: its share of posts archived in the last
    `days` against its share of the whole archive. A minimum recent count
    keeps a name seen on one or two new posts from topping a list.
    """
    cutoff = (now or datetime.now()) - timedelta(days=days)
    cursor.execute("SELECT COUNT(*) FROM posts WHERE ingested_at >= %s", (cutoff,))
    recent_posts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM posts")
    total_posts = cursor.fetchone()[0]
    buckets = {key: [] for key, _ in TRENDING_CATEGORIES}
    if recent_posts:
        min_uses = max(5, recent_posts // 200)
        cursor.execute("""
            SELECT ar.name, r.uses, a.uses
            FROM (SELECT pa.artist_id, COUNT(*) AS uses
                  FROM posts p JOIN posts_artists pa ON pa.post_id = p.id
                  WHERE p.ingested_at >= %s
                  GROUP BY pa.artist_id
                  HAVING COUNT(*) >= %s) r
            JOIN artists ar ON ar.artist_id = r.artist_id
            JOIN (SELECT artist_id, COUNT(*) AS uses FROM posts_artists GROUP BY artist_id) a
              ON a.artist_id = r.artist_id
        """, (cutoff, min_uses))
        buckets["artist"] = [(name, recent, total) for name, recent, total in cursor.fetchall()]
        cursor.execute(f"""
            SELECT t.name, LOWER(COALESCE(t.tag_type, 'general')), r.uses, a.uses, tc.is_generic, tc.is_fetish
            FROM (SELECT pt.tag_id, COUNT(*) AS uses
                  FROM posts p JOIN posts_tags pt ON pt.post_id = p.id
                  WHERE p.ingested_at >= %s
                  GROUP BY pt.tag_id
                  HAVING COUNT(*) >= %s) r
            JOIN tags t ON t.tag_id = r.tag_id
            JOIN (SELECT tag_id, COUNT(*) AS uses FROM posts_tags GROUP BY tag_id) a ON a.tag_id = r.tag_id
            LEFT JOIN {generic_tags.TABLE} tc ON tc.tag_id = t.tag_id
        """, (cutoff, min_uses))
        for name, kind, recent, total, is_generic, is_fetish in cursor.fetchall():
            if is_generic:
                continue
            key = "fetish" if is_fetish else kind
            if key in buckets and key != "artist":
                buckets[key].append((name, recent, total))

    def ranked(rows):
        rows = sorted(rows, key=lambda r: (-(r[1] / r[2]), -r[1], r[0]))[:limit]
        return [{"name": name, "recent": recent, "total": total,
                 "lift": round((recent / recent_posts) / (total / total_posts), 1)}
                for name, recent, total in rows]

    return {"days": days, "recent_posts": recent_posts,
            "categories": [{"key": key, "title": title, "items": ranked(buckets[key])}
                           for key, title in TRENDING_CATEGORIES]}


@app.get("/api/trending")
def trending_api():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    if days not in TRENDING_WINDOWS:
        days = 30
    cached = _trending_cache.get(days)
    if cached and time.monotonic() - cached[0] < TRENDING_CACHE_SECONDS:
        return jsonify(cached[1])
    try:
        connection = database_connection()
        try:
            generic_tags.ensure_table(connection)
            cursor = connection.cursor()
            result = trending(cursor, days)
            cursor.close()
        finally:
            connection.close()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    _trending_cache[days] = (time.monotonic(), result)
    return jsonify(result)


@app.get("/media/<int:post_id>")
def media(post_id):
    try:
        path = post_file_path(post_id)
    except Exception:
        abort(404)
    if not path:
        abort(404)
    return send_file(path, conditional=True)


def is_local_request():
    """Opening files acts on the machine running the server, so only allow it
    from a browser on that same machine (matters when run with --host 0.0.0.0)."""
    return request.remote_addr in {"127.0.0.1", "::1"}


def launch(path, reveal=False):
    """Open path in its default app, or reveal it in the file manager."""
    if sys.platform == "win32":
        if reveal:
            # explorer wants '/select,"path"' as one unsplit argument.
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if reveal else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if reveal else path])


@app.post("/api/open/<int:post_id>")
def open_local(post_id):
    if not is_local_request():
        return jsonify({"error": "Files can only be opened from the machine running the browser server"}), 403
    # Requiring JSON means another site can't trigger this with a plain form
    # post: a cross-origin JSON fetch needs a CORS preflight this app never grants.
    if not request.is_json:
        return jsonify({"error": "Expected a JSON request"}), 400
    action = (request.get_json(silent=True) or {}).get("action", "open")
    if action not in {"open", "reveal"}:
        return jsonify({"error": f"Unknown action {action!r}"}), 400
    try:
        path = post_file_path(post_id)
        if not path:
            return jsonify({"error": "The local file is unavailable"}), 404
        launch(path, reveal=action == "reveal")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


def parse_args(argv=None):  # fff: the original parsed args inside the __main__ block
    parser = argparse.ArgumentParser(description="Browse the local e621 archive in a web browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5000, type=int)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
