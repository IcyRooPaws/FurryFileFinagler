# fff: run as 'fff web'; the __main__ block became parse_args(argv) + main(argv).
"""Local web browser for searching and previewing archived e621 posts."""

import argparse
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_file

from fff.archive import generic_tags
from fff.archive.config import load_config
from fff.archive.db import FLAG_TOKENS, connect_to_mysql
from fff.gui import thumbnails


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


# Shared look for every page: e621's layout (Verdana, boxes under page tabs, tag colors
# by category) in the palette of the user's artwork - purple-navy #33365f, near-black
# #1d2321, slate #5d6572, grey-blue #a8b4c2 and the eye's red #de3c2e. The red is only
# 2.6:1 on the boxes, so it marks things (active tab and toggles, deleted posts) and
# never colors text. e621's tag hues are kept, lightened to >= 4.3:1 on #33365f. Each page template
# has a "/* @theme */" marker where this goes (see the end of the templates).
THEME_CSS = """
    :root { --bg:#1d2321; --box:#33365f; --box-2:#3d4170; --box-3:#5d6572; --ink:#f1f3f5; --muted:#a8b4c2;
            --link:#a8b4c2; --link-hover:#ffffff; --accent:#de3c2e; --deleted:#ff6b5e;
            --t-artist:#f2ac08; --t-copyright:#f57af5; --t-character:#3ccf3c; --t-species:#ff8150;
            --t-general:#a8b4c2; --t-meta:#ffffff; --t-lore:#5cc05c; --t-invalid:#ff6b5e;
            --r-safe:#5fd06c; --r-questionable:#ffe666; --r-explicit:#ff7b6f; color-scheme:dark; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:var(--bg); font:13px/1.45 Verdana, Geneva, "DejaVu Sans", sans-serif; }
    a { color:var(--link); text-decoration:none; }
    a:hover { color:var(--link-hover); text-decoration:underline; }
    .wrap { max-width:1600px; margin:0 auto; padding:0 16px; }
    .site-head { padding-top:14px; }
    .brand { display:flex; flex-wrap:wrap; align-items:baseline; gap:10px; margin:0 4px 10px; }
    .brand a { color:var(--ink); font-size:24px; font-weight:700; line-height:1; }
    .brand a:hover { color:var(--accent); text-decoration:none; }
    .brand span { color:var(--muted); font-size:12px; }
    .tabs { display:flex; flex-wrap:wrap; gap:2px; }
    .tabs a { padding:6px 14px; border-radius:6px 6px 0 0; font-weight:700; }
    .tabs a:hover { background:var(--box-2); text-decoration:none; }
    .tabs a.current { background:var(--box); color:var(--ink); box-shadow:inset 0 3px 0 var(--accent); }
    .box { margin-bottom:24px; padding:14px 16px 20px; border-radius:0 6px 6px 6px; background:var(--box); }
    h1.page-title { margin:0 0 4px; font-size:20px; }
    .lead { margin:0 0 14px; color:var(--muted); }
    h2.section { margin:0 0 4px; color:var(--ink); font-size:14px; font-weight:700; }
    input, select { min-height:28px; padding:3px 7px; border:1px solid var(--box-3); border-radius:3px; background:#e8edf3; color:#1d2321; font:inherit; }
    button { font:inherit; }
    .btn { min-height:28px; padding:3px 12px; border:0; border-radius:3px; background:var(--box-3); color:var(--ink); font-weight:700; cursor:pointer; }
    .btn:hover { background:var(--accent); }
    .linkish { padding:0; border:0; background:none; color:var(--link); cursor:pointer; }
    .linkish:hover { color:var(--link-hover); text-decoration:underline; }
    .linkish:disabled { color:var(--muted); cursor:default; text-decoration:none; }
    .tag-artist { color:var(--t-artist); } .tag-copyright { color:var(--t-copyright); }
    .tag-character { color:var(--t-character); } .tag-species { color:var(--t-species); }
    .tag-general, .tag-fetish { color:var(--t-general); } .tag-meta { color:var(--t-meta); }
    .tag-lore { color:var(--t-lore); } .tag-invalid { color:var(--t-invalid); }
    .r-safe { color:var(--r-safe); } .r-questionable { color:var(--r-questionable); } .r-explicit { color:var(--r-explicit); }
"""


def site_header(current):
    """The brand line and page tabs shared by every page; ``current`` is "posts" or "trends"."""
    tabs = "".join('<a{} href="{}">{}</a>'.format(' class="current"' if key == current else "", href, label)
                   for key, href, label in (("posts", "/", "Posts"), ("trends", "/trends", "Trends")))
    return ('<div class="wrap site-head"><div class="brand"><a href="/">Local Archive</a>'
            '<span>your e621 favorites, on disk</span></div>'
            f'<nav class="tabs" aria-label="Pages">{tabs}</nav></div>')


PAGE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local E621 Archive</title>
  <style>/* @theme */
    .searchbar { display:flex; flex-wrap:wrap; gap:8px 10px; align-items:flex-end; margin-bottom:12px; padding-bottom:12px; border-bottom:1px solid var(--box-3); }
    .searchbar label { display:block; margin-bottom:2px; color:var(--muted); font-size:11px; }
    .searchbar .search { position:relative; flex:3 1 280px; }
    .searchbar .search input { width:100%; }
    .layout { display:grid; grid-template-columns:minmax(0, 1fr) 260px; gap:20px; align-items:start; }
    .stats { margin:0 0 12px; color:var(--muted); }
    .stats strong { color:var(--ink); }
    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:18px 10px; }
    .thumb { display:flex; flex-direction:column; align-items:center; min-width:0; text-align:center; }
    .thumb-img { position:relative; display:flex; align-items:center; justify-content:center; width:100%; height:180px; }
    .play-badge { position:absolute; left:50%; top:50%; display:grid; place-items:center; width:40px; height:40px; margin:-20px 0 0 -20px; border-radius:50%; background:#1d2321cc; color:#ffffff; font-size:16px; pointer-events:none; }
    .thumb-img img, .thumb-img video { display:block; max-width:100%; max-height:180px; border:2px solid transparent; border-radius:3px; }
    .thumb.deleted .thumb-img img, .thumb.deleted .thumb-img video, .thumb.deleted .missing { border-color:var(--deleted); }
    .missing { display:grid; place-items:center; width:150px; height:150px; border:2px dashed var(--box-3); border-radius:3px; color:var(--muted); font-size:11px; }
    .thumb-desc { display:flex; gap:8px; align-items:center; margin-top:4px; padding:1px 8px; border-radius:3px; background:var(--box-2); font-size:11px; }
    .rating-letter { font-weight:700; }
    .ftype { color:var(--muted); }
    .thumb-artist { max-width:100%; margin-top:3px; font-size:11px; overflow-wrap:anywhere; }
    .deleted-note { color:var(--deleted); font-size:11px; }
    .links { display:flex; flex-wrap:wrap; justify-content:center; gap:0 8px; margin-top:2px; font-size:11px; }
    .pager { display:flex; justify-content:center; align-items:center; gap:16px; margin-top:24px; font-weight:700; }
    .empty { grid-column:1 / -1; padding:50px 15px; color:var(--muted); text-align:center; }
    .sidebar { position:sticky; top:12px; max-height:calc(100vh - 24px); overflow-y:auto; padding-left:16px; border-left:1px solid var(--box-3); }
    .trending-head { display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .trend-order { display:flex; margin-top:8px; border-radius:3px; overflow:hidden; }
    .trend-order button { flex:1; padding:3px 8px; border:0; background:var(--box-2); color:var(--link); font-size:12px; font-weight:700; cursor:pointer; }
    .trend-order button:hover { background:var(--box-3); }
    .trend-order button[aria-pressed="true"] { background:var(--accent); color:#ffffff; }
    .trend-group h3 { margin:12px 0 2px; font-size:13px; }
    .trend-group ol { margin:0; padding:0; list-style:none; }
    .trending .note { margin:4px 0 0; color:var(--muted); font-size:12px; }
    .trend { display:flex; align-items:baseline; gap:6px; width:100%; padding:1px 4px; border:0; border-radius:2px; background:none; color:var(--t-general); text-align:left; cursor:pointer; }
    .trend .tname { flex:1; min-width:0; overflow-wrap:anywhere; }
    .trend:hover .tname { text-decoration:underline; }
    .trend.active { background:var(--box-3); }
    .trend .tcount { flex:none; color:var(--muted); font-size:11px; }
    .cat-artist .trend { color:var(--t-artist); } .cat-copyright .trend { color:var(--t-copyright); }
    .cat-character .trend { color:var(--t-character); } .cat-species .trend { color:var(--t-species); }
    .trending-more { display:block; margin-top:14px; font-weight:700; }
    .suggest { position:absolute; z-index:10; top:100%; left:0; right:0; margin:2px 0 0; padding:3px 0; list-style:none; border:1px solid var(--box-3); border-radius:3px; background:var(--box-2); box-shadow:0 10px 28px #00000080; max-height:360px; overflow-y:auto; }
    .suggest[hidden] { display:none; }
    .suggest li { display:flex; gap:10px; align-items:baseline; padding:4px 9px; color:var(--t-general); cursor:pointer; }
    .suggest li.active, .suggest li:hover { background:var(--box-3); }
    .suggest .name { flex:1; overflow-wrap:anywhere; }
    .suggest .name mark { background:none; color:inherit; font-weight:700; text-decoration:underline; }
    .suggest .kind, .suggest .count { color:var(--muted); font-size:11px; }
    @media (max-width:1000px) { .layout { grid-template-columns:1fr; } .sidebar { position:static; max-height:none; order:-1; padding:0 0 12px; border-left:0; border-bottom:1px solid var(--box-3); } .trend-groups { display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:0 20px; } }
    @media (max-width:600px) { .grid { grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); } .thumb-img { height:150px; } .thumb-img img, .thumb-img video { max-height:150px; } }
  </style>
</head>
<body>
  {{ site_header('posts')|safe }}
  <div class="wrap"><main class="box">
    <form id="filters" class="searchbar">
      <div class="search"><label for="q">Search</label><input id="q" name="q" placeholder="artist, tag, filename, or post ID" autocomplete="off" role="combobox" aria-autocomplete="list" aria-controls="suggest" aria-expanded="false"><ul id="suggest" class="suggest" role="listbox" hidden></ul></div>
      <div><label for="rating">Rating</label><select id="rating"><option value="">All ratings</option><option>safe</option><option>questionable</option><option>explicit</option></select></div>
      <div><label for="media">Media type</label><select id="media"><option value="">All media</option><option value="image">Images</option><option value="gif">GIFs</option><option value="video">Videos</option><option value="other">Other</option></select></div>
      <div><label for="deleted">Status</label><select id="deleted"><option value="">All posts</option><option value="0">Active</option><option value="1">Deleted</option></select></div>
      <div><label for="sort">Order</label><select id="sort"><option value="created_at_desc">Created: newest</option><option value="created_at_asc">Created: oldest</option><option value="deleted_at_desc">Deleted: newest</option><option value="deleted_at_asc">Deleted: oldest</option></select></div>
      <button class="btn" type="submit">Search</button>
    </form>
    <div class="layout">
      <div class="results">
        <div class="stats" id="stats">Loading archive...</div>
        <section class="grid" id="grid"></section>
        <div class="pager"><button class="linkish" id="prev">&laquo; Previous</button><span id="page"></span><button class="linkish" id="next">Next &raquo;</button></div>
      </div>
      <aside class="sidebar">
        <section class="trending" aria-labelledby="trending-title">
          <div class="trending-head"><h2 class="section" id="trending-title">Trending</h2><select id="trending-days" aria-label="Trending window"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select></div>
          <div class="trend-order" role="group" aria-label="Order trending lists by"><button type="button" data-order="top" aria-pressed="true" title="Most posts archived in this window first">Most posts</button><button type="button" data-order="rising" aria-pressed="false" title="Names showing up far more than usual first, even if they are on fewer posts">Rising</button></div>
          <div class="trend-groups" id="trending"><p class="note">Loading...</p></div>
          <a class="trending-more" href="/trends">Trends over time &raquo;</a>
        </section>
      </aside>
    </div>
  </main></div>
  <script>
    let currentPage = 1;
    const canOpen = {{ 'true' if can_open else 'false' }};
        const initialSearch = new URLSearchParams(window.location.search).get('q') || '';
    // true while q holds a name picked from a list (see post_filters); typing clears it
    let exactSearch = initialSearch !== '' && new URLSearchParams(window.location.search).get('exact') === '1';
        document.querySelector('#q').value = initialSearch;
    const params = () => new URLSearchParams({ q: document.querySelector('#q').value.trim(), rating: document.querySelector('#rating').value, media: document.querySelector('#media').value, deleted: document.querySelector('#deleted').value, sort: document.querySelector('#sort').value, page: currentPage, ...(exactSearch ? { exact: '1' } : {}) });
    const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    const RATING_LETTER = { safe: 'S', questionable: 'Q', explicit: 'E' };
    function card(post) {
      const media = post.has_file
        ? `<img loading="lazy" decoding="async" src="${post.thumb_url}" alt="Post ${post.id}" data-kind="${post.is_video ? 'video' : 'image'}">${post.is_video ? '<span class="play-badge" aria-hidden="true">&#9654;</span>' : ''}`
        : '<div class="missing">No file</div>';
      const kind = post.is_video ? 'VIDEO' : post.ext === 'gif' ? 'GIF' : '';
      const deleted = post.deleted ? `<div class="deleted-note" title="${esc(post.deletion_reason || '')}">deleted</div>` : '';
      const source = post.file_url ? `<a href="${esc(post.file_url)}" target="_blank" rel="noreferrer">Source</a>` : '';
      const local = post.has_file && canOpen ? `<button class="linkish" type="button" data-open="${post.id}" data-action="open">Open file</button><button class="linkish" type="button" data-open="${post.id}" data-action="reveal">Show in folder</button>` : '';
      return `<article class="thumb${post.deleted ? ' deleted' : ''}"><a class="thumb-img" href="/post/${post.id}" title="${esc(post.file_name || '')}">${media}</a>`
        + `<div class="thumb-desc"><span class="rating-letter r-${esc(post.rating)}" title="${esc(post.rating)}">${RATING_LETTER[post.rating] || '?'}</span><a href="/post/${post.id}">#${post.id}</a>${kind ? `<span class="ftype">${kind}</span>` : ''}</div>`
        + `<div class="thumb-artist tag-artist">${esc(post.artists || 'unknown artist')}</div>${deleted}<div class="links">${source}${local}</div></article>`;
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
    // A thumbnail whose file can't be read (moved, offline drive) shows the "No file" box, not alt text.
    document.querySelector('#grid').addEventListener('error', event => {
      const media = event.target;
      if (media.matches && media.matches('.thumb-img img, .thumb-img video')) {
        // a video whose frame couldn't be read still plays on its post page
        const box = document.createElement('div'); box.className = 'missing'; box.textContent = media.dataset.kind === 'video' ? 'Video' : 'No file';
        media.replaceWith(box);
      }
    }, true);
    document.querySelector('#grid').addEventListener('click', event => { const button = event.target.closest('button[data-open]'); if (button) openLocal(button); });
    async function load() {
      const grid = document.querySelector('#grid'); grid.innerHTML = '<div class="empty">Loading...</div>';
      markActiveTrend();
      const response = await fetch('/api/posts?' + params()); const data = await response.json();
      if (!response.ok) { grid.innerHTML = `<div class="empty">${esc(data.error || 'Unable to load posts')}</div>`; return; }
      const term = document.querySelector('#q').value.trim();
      document.querySelector('#stats').innerHTML = `<strong>${data.total.toLocaleString()}</strong> ${exactSearch && term ? `posts tagged <strong>${esc(term)}</strong> (exact match)` : 'matching posts'}`;
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
      list.innerHTML = suggestions.map((s, i) => `<li role="option" data-i="${i}" class="tag-${esc(s.type.toLowerCase())}${i === active ? ' active' : ''}"><span class="name">${highlight(s.name, term)}</span><span class="kind">${esc(s.type)}</span><span class="count">${s.count.toLocaleString()}</span></li>`).join('');
      list.hidden = false; q.setAttribute('aria-expanded', 'true');
      list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
    }
    function pick(i) { q.value = suggestions[i].name; exactSearch = true; hideSuggestions(); currentPage = 1; load(); }
    const trendingList = document.querySelector('#trending'), trendingDays = document.querySelector('#trending-days');
    function markActiveTrend() { trendingList.querySelectorAll('.trend').forEach(b => b.classList.toggle('active', b.dataset.tag === q.value.trim())); }
    const trendOrderButtons = document.querySelectorAll('.trend-order button');
    let trendOrder = 'top';
    try { if (localStorage.getItem('trendOrder') === 'rising') trendOrder = 'rising'; } catch {}
    function showTrendOrder() { trendOrderButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.order === trendOrder))); }
    showTrendOrder();
    trendOrderButtons.forEach(b => b.addEventListener('click', () => {
      if (b.dataset.order === trendOrder) return;
      trendOrder = b.dataset.order; showTrendOrder();
      try { localStorage.setItem('trendOrder', trendOrder); } catch {}
      loadTrending();
    }));
    const trendEmpty = { fetish: 'Nothing trending. Tags show here when listed in data/kink_tags.txt or marked fetish (key F) in the Generic tags tool.' };
    function trendItem(t, i, data) {
      const tip = `${t.recent.toLocaleString()} of ${data.recent_posts.toLocaleString()} posts archived in the last ${data.days} days (${t.total.toLocaleString()} all-time), ${t.lift}x its usual rate`;
      return `<li><button type="button" class="trend" data-tag="${esc(t.name)}" title="${esc(tip)}"><span class="tname">${esc(t.name.replace(/_/g, ' '))}</span><span class="tcount">${t.recent.toLocaleString()}</span></button></li>`;
    }
    async function loadTrending() {
      trendingList.innerHTML = '<p class="note">Loading...</p>';
      try {
        const response = await fetch('/api/trending?' + new URLSearchParams({ days: trendingDays.value, order: trendOrder })); const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        if (!data.recent_posts) { trendingList.innerHTML = `<p class="note">No posts archived in the last ${data.days} days.</p>`; return; }
        trendingList.innerHTML = data.categories.map(cat => `<section class="trend-group cat-${esc(cat.key)}"><h3>${esc(cat.title)}</h3>${cat.items.length
          ? `<ol>${cat.items.map((t, i) => trendItem(t, i, data)).join('')}</ol>`
          : `<p class="note">${esc(trendEmpty[cat.key] || 'Nothing stands out.')}</p>`}</section>`).join('');
        markActiveTrend();
      } catch { trendingList.innerHTML = '<p class="note">Trending is unavailable right now.</p>'; }
    }
    trendingList.addEventListener('click', event => { const b = event.target.closest('.trend'); if (b) { q.value = b.dataset.tag; exactSearch = true; hideSuggestions(); currentPage = 1; load(); } });
    trendingDays.addEventListener('change', loadTrending);
    q.addEventListener('input', () => {
      exactSearch = false;
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
  <style>/* @theme */
    .post-layout { display:grid; grid-template-columns:240px minmax(0, 1fr); gap:20px; align-items:start; }
    .post-sidebar section { margin-bottom:14px; }
    .tag-list, .info { margin:0; padding:0; list-style:none; }
    .tag-list li, .info li { padding:1px 0; overflow-wrap:anywhere; }
    .info .label { color:var(--muted); }
    .none { color:var(--muted); }
    .mono { font-family:Consolas, "Courier New", monospace; font-size:12px; overflow-wrap:anywhere; }
    .open-status { display:block; color:var(--deleted); }
    .notice { margin-bottom:10px; padding:8px 12px; border:1px solid var(--accent); border-radius:3px; background:#de3c2e26; }
    .post-media img, .post-media video { display:block; max-width:100%; max-height:85vh; }
    .post-media .missing { display:grid; place-items:center; min-height:300px; border:2px dashed var(--box-3); border-radius:3px; color:var(--muted); }
    .file-line { margin:8px 0 0; color:var(--muted); font-size:11px; overflow-wrap:anywhere; }
    @media (max-width:800px) { .post-layout { grid-template-columns:1fr; } .post-sidebar { order:2; } }
  </style>
</head>
<body>
  {{ site_header('posts')|safe }}
  <div class="wrap"><div class="box post-layout">
    <aside class="post-sidebar">
      <section><h2 class="section">Artists</h2><ul class="tag-list">{% for artist in artists %}<li><a class="tag-artist" href="/?q={{ artist|urlencode }}&amp;exact=1">{{ artist|replace('_', ' ') }}</a></li>{% else %}<li class="none">None recorded</li>{% endfor %}</ul></section>
      {% for category, category_tags in tags.items() %}
      <section><h2 class="section">{{ section_titles.get(category|lower, category|title) }}</h2><ul class="tag-list">{% for tag in category_tags %}<li><a class="tag-{{ category|lower }}" href="/?q={{ tag|urlencode }}&amp;exact=1">{{ tag|replace('_', ' ') }}</a></li>{% endfor %}</ul></section>
      {% endfor %}
      <section><h2 class="section">Information</h2><ul class="info">
        <li><span class="label">ID:</span> {{ post.id }}</li>
        <li><span class="label">Rating:</span> <span class="r-{{ post.rating or '' }}">{{ (post.rating or 'unknown')|title }}</span></li>
        <li><span class="label">Type:</span> {{ post.file_type or 'Unknown' }}</li>
        <li><span class="label">MD5:</span> <span class="mono">{{ post.md5 or 'Unavailable' }}</span></li>
        {% if post.deleted %}<li><span class="label">Deleted:</span> {{ post.deleted_at or 'yes' }}</li>{% endif %}
      </ul></section>
      <section><h2 class="section">Options</h2><ul class="info">
        {% if post.has_file and can_open %}<li class="local-actions"><button class="linkish" type="button" data-action="open">Open file</button><br><button class="linkish" type="button" data-action="reveal">Show in folder</button><span class="open-status" id="open-status"></span></li>{% endif %}
        <li><a href="https://e621.net/posts/{{ post.id }}" target="_blank" rel="noreferrer">View on e621</a></li>
      </ul></section>
      {% if sources %}<section><h2 class="section">Sources</h2><ul class="info">{% for source in sources %}<li><a href="{{ source }}" target="_blank" rel="noreferrer">{{ source }}</a></li>{% endfor %}</ul></section>{% endif %}
    </aside>
    <section class="post-content">
      {% if post.deleted %}<div class="notice">This post was deleted{% if post.deletion_reason %}: {{ post.deletion_reason }}{% endif %}</div>{% endif %}
      <div class="post-media">
        {% if post.has_file %}
          {% if post.is_video %}<video controls autoplay loop src="{{ post.media_url }}"></video>{% else %}<img src="{{ post.media_url }}" alt="Post {{ post.id }}">{% endif %}
        {% else %}<div class="missing">The local file is unavailable.</div>{% endif %}
      </div>
      <p class="file-line">{{ post.file_name or 'No filename recorded' }}{% if post.file_path %} &middot; {{ post.file_path }}{% endif %}</p>
    </section>
  </div></div>
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


TRENDS_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trends over time | Local E621 Archive</title>
  <style>/* @theme */
    .filters { display:flex; flex-wrap:wrap; gap:10px 16px; align-items:flex-end; margin-bottom:14px; padding-bottom:12px; border-bottom:1px solid var(--box-3); }
    .label { display:block; margin-bottom:2px; color:var(--muted); font-size:11px; }
    .seg { display:flex; flex-wrap:wrap; gap:2px; }
    .seg button { min-height:28px; padding:3px 10px; border:0; border-radius:3px; background:var(--box-2); color:var(--link); font-weight:700; cursor:pointer; }
    .seg button:hover { background:var(--box-3); }
    .seg button[aria-pressed="true"] { background:var(--accent); color:#ffffff; }
    .check { display:flex; gap:6px; align-items:center; min-height:28px; cursor:pointer; }
    .check input { min-height:0; }
    .legend { display:flex; flex-wrap:wrap; gap:6px 12px; align-items:center; margin-bottom:10px; color:var(--muted); font-size:12px; }
    .legend strong { color:var(--ink); }
    .ramp { display:flex; gap:2px; }
    .ramp span { width:20px; height:12px; border-radius:2px; }
    .scroll { overflow-x:auto; transition:opacity .15s; }
    .scroll.loading { opacity:.45; }
    table.heat { border-collapse:separate; border-spacing:2px; font-size:12px; }
    .heat th { color:var(--muted); font-weight:700; }
    .heat thead th { padding:0 2px 6px; font-size:11px; line-height:1.2; text-align:center; white-space:nowrap; vertical-align:bottom; }
    .heat thead th small { display:block; font-weight:400; }
    .heat th.name { position:sticky; left:0; z-index:1; min-width:190px; max-width:260px; padding:0 12px 0 0; background:var(--box); text-align:left; font-weight:400; }
    .heat thead th.name { color:var(--ink); font-weight:700; }
    .heat th.name a { overflow-wrap:anywhere; }
    .heat .total { margin-left:6px; color:var(--muted); font-size:11px; }
    .heat td { width:56px; min-width:56px; height:26px; padding:0 2px; border-radius:3px; text-align:center; color:var(--ink); font-size:11px; font-variant-numeric:tabular-nums; }
    .heat td.zero { box-shadow:inset 0 0 0 1px var(--box-3); }
    .heat td.gap, .heat th.gap { width:10px; min-width:10px; padding:0; }
    .heat tbody td:not(.gap):hover { outline:2px solid var(--ink); outline-offset:-1px; }
    .heat tr.volume td { height:50px; vertical-align:bottom; box-shadow:none; }
    .heat tr.volume th { vertical-align:bottom; color:var(--muted); font-weight:700; }
    .vbar { width:100%; border-radius:4px 4px 0 0; background:var(--muted); }
    .vnum { display:block; color:var(--muted); font-size:11px; }
    .note { margin:12px 0 0; color:var(--muted); font-size:12px; }
    .empty { padding:40px 0; color:var(--muted); text-align:center; }
    #tip { position:fixed; z-index:20; max-width:300px; padding:6px 9px; border:1px solid var(--box-3); border-radius:3px; background:var(--bg); box-shadow:0 8px 24px #00000080; font-size:12px; pointer-events:none; }
    #tip strong { display:block; font-size:15px; }
    #tip span { display:block; color:var(--muted); }
    @media (max-width:700px) { .heat th.name { min-width:130px; } }
  </style>
</head>
<body>
  {{ site_header('trends')|safe }}
  <div class="wrap"><main class="box">
    <h1 class="page-title">Trends over time</h1>
    <p class="lead">What made up the posts you archived each month. Each cell is the share of that month's posts carrying the name, so busy and quiet months compare fairly.</p>
    <div class="filters">
      <div><label class="label" for="range">Range</label><select id="range"><option value="3">Last 3 months</option><option value="6">Last 6 months</option><option value="12">Last 12 months</option></select></div>
      <div><span class="label" id="category-label">Show</span><div class="seg" id="category" role="group" aria-labelledby="category-label"></div></div>
      <div><label class="label" for="rows">Rows</label><select id="rows"><option value="10">Top 10</option><option value="20">Top 20</option><option value="30">Top 30</option></select></div>
      <div><label class="label" for="shade">Shade</label><select id="shade"><option value="row">Against each row's own peak</option><option value="all">One scale for every row</option></select></div>
      <div><span class="label">Table view</span><label class="check"><input type="checkbox" id="numbers"> Show numbers</label></div>
    </div>
    <section aria-live="polite">
      <div class="legend" id="legend"></div>
      <div class="scroll" id="scroll"><div class="empty">Loading...</div></div>
      <p class="note" id="note"></p>
    </section>
  </main></div>
  <div id="tip" hidden></div>
  <script>
    const CATEGORIES = {{ categories|tojson }};
    // Sequential grey-blue from the artwork (slate #5d6572 -> #a8b4c2 -> near white): near zero
    // recedes toward the purple box, more is lighter.
    const RAMP = ['#454b6a', '#4e556d', '#575e70', '#666f7c', '#828c9a', '#9faab8', '#b8c2ce', '#d0d8e1', '#e8edf3'];
    const DARK_TEXT_FROM = 5;  // from this step dark text out-reads light (APCA 54 vs 43) in "Show numbers"
    const $ = (sel) => document.querySelector(sel);
    const url = new URLSearchParams(location.search);
    const pick = (value, allowed, fallback) => allowed.includes(value) ? value : fallback;
    const state = {
      range: pick(url.get('range'), ['3', '6', '12'], '12'),
      category: pick(url.get('category'), CATEGORIES.map(c => c[0]), 'species'),
      rows: pick(url.get('rows'), ['10', '20', '30'], '20'),
      shade: pick(url.get('shade'), ['row', 'all'], 'row'),
      numbers: url.get('numbers') === '1',
    };
    let current = null, seq = 0;
    const cellInfo = new WeakMap();

    function el(tag, props = {}, ...children) {
      const node = document.createElement(tag);
      Object.assign(node, props);
      for (const child of children) node.append(child);
      return node;
    }
    const pretty = (name) => name.replace(/_/g, ' ');
    function pct(share) {
      const v = share * 100;
      return v === 0 ? '0%' : v < 0.1 ? '<0.1%' : v < 10 ? v.toFixed(1) + '%' : v.toFixed(0) + '%';
    }
    const stepFor = (v) => v <= 0 ? -1 : Math.min(RAMP.length - 1, Math.max(0, Math.ceil(v * RAMP.length) - 1));

    function syncControls() {
      $('#range').value = state.range; $('#rows').value = state.rows; $('#shade').value = state.shade; $('#numbers').checked = state.numbers;
      document.querySelectorAll('#category button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.key === state.category)));
      const q = new URLSearchParams({ range: state.range, category: state.category, rows: state.rows, shade: state.shade });
      if (state.numbers) q.set('numbers', '1');
      history.replaceState(null, '', '?' + q);
    }

    async function load() {
      syncControls();
      const mine = ++seq;
      $('#scroll').classList.add('loading');  // keep the old render while refetching
      try {
        const response = await fetch('/api/trends?' + new URLSearchParams({ range: state.range, category: state.category, rows: state.rows }));
        const data = await response.json();
        if (mine !== seq) return;
        if (!response.ok) throw new Error(data.error || 'Unable to load trends');
        current = data; render();
      } catch (error) {
        if (mine !== seq) return;
        $('#scroll').replaceChildren(el('div', { className: 'empty', textContent: error.message }));
        $('#legend').replaceChildren(); $('#note').textContent = '';
      } finally {
        if (mine === seq) $('#scroll').classList.remove('loading');
      }
    }

    function render() {
      const data = current;
      const columns = [{ key: 'before', label: 'Earlier', sub: data.before.short, posts: data.before.posts, before: true }]
        .concat(data.months.map(m => ({ key: m.key, label: m.label.split(' ')[0], sub: m.label.split(' ')[1], full: m.label, posts: m.posts })));
      if (!data.rows.length) {
        $('#scroll').replaceChildren(el('div', { className: 'empty', textContent: data.category === 'fetish'
          ? 'No fetish tags yet. List them in data/kink_tags.txt or mark them (key F) in the Generic tags tool.'
          : 'Nothing archived in this range.' }));
        $('#legend').replaceChildren(); $('#note').textContent = ''; return;
      }
      const shares = data.rows.map(r => [r.before, ...r.counts].map((n, i) => columns[i].posts ? n / columns[i].posts : 0));
      const globalMax = Math.max(...shares.flat(), 0);

      // header
      const head = el('tr', {}, el('th', { className: 'name', scope: 'col', textContent: data.title }));
      columns.forEach((c, i) => {
        head.append(el('th', { scope: 'col', title: c.before ? data.before.label : c.full }, c.label, el('small', { textContent: c.sub || '' })));
        if (i === 0) head.append(el('th', { className: 'gap' }));
      });

      // posts archived per month (its own bars; the import column is a number, it would dwarf the rest)
      const monthMax = Math.max(...data.months.map(m => m.posts), 1);
      const volume = el('tr', { className: 'volume' }, el('th', { className: 'name', scope: 'row', textContent: 'Posts archived' }));
      columns.forEach((c, i) => {
        const td = el('td');
        if (c.before) td.append(el('span', { className: 'vnum', textContent: c.posts.toLocaleString() }));
        else {
          if (state.numbers) td.append(el('span', { className: 'vnum', textContent: c.posts.toLocaleString() }));
          td.append(el('div', { className: 'vbar', style: `height:${c.posts ? Math.max(2, Math.round(30 * c.posts / monthMax)) : 0}px` }));
        }
        cellInfo.set(td, { value: c.posts.toLocaleString() + ' posts', lines: [c.before ? data.before.label : 'archived in ' + c.full] });
        volume.append(td);
        if (i === 0) volume.append(el('td', { className: 'gap' }));
      });

      const body = el('tbody', {}, volume);
      data.rows.forEach((row, r) => {
        const link = el('a', { className: 'tag-' + data.category, href: '/?' + new URLSearchParams({ q: row.name, exact: '1' }), textContent: pretty(row.name) });
        const tr = el('tr', {}, el('th', { className: 'name', scope: 'row' }, link, el('span', { className: 'total', textContent: row.total.toLocaleString() })));
        const rowMax = Math.max(...shares[r]);
        columns.forEach((c, i) => {
          const share = shares[r][i], count = i === 0 ? row.before : row.counts[i - 1];
          const step = stepFor(state.shade === 'row' ? (rowMax ? share / rowMax : 0) : (globalMax ? share / globalMax : 0));
          const td = el('td');
          if (step >= 0) td.style.background = RAMP[step]; else td.className = 'zero';
          if (state.numbers && count) { td.textContent = pct(share); td.style.color = step >= DARK_TEXT_FROM ? '#1d2321' : '#f1f3f5'; }
          td.setAttribute('aria-label', `${pretty(row.name)}, ${c.before ? data.before.label : c.full}: ${pct(share)} (${count} of ${c.posts} posts)`);
          cellInfo.set(td, { value: pct(share), lines: [`${count.toLocaleString()} of ${c.posts.toLocaleString()} posts`, `${pretty(row.name)} · ${c.before ? data.before.label : c.full}`] });
          tr.append(td);
          if (i === 0) tr.append(el('td', { className: 'gap' }));
        });
        body.append(tr);
      });
      $('#scroll').replaceChildren(el('table', { className: 'heat' }, el('thead', {}, head), body));

      const ramp = el('span', { className: 'ramp' }); RAMP.forEach(c => ramp.append(el('span', { style: `background:${c}` })));
      $('#legend').replaceChildren(el('strong', { textContent: "Share of each month's posts" }),
        el('span', { textContent: state.shade === 'row' ? 'low' : '0%' }), ramp,
        el('span', { textContent: state.shade === 'row' ? "each row's busiest month" : pct(globalMax) }),
        el('span', { textContent: 'Hover a cell for numbers.' }));
      $('#note').textContent = `"Earlier" is the ${data.before.posts.toLocaleString()} posts imported on ${data.before.date}: everything favorited before FFF started tracking. `
        + 'Months count posts by the date FFF archived them, close to when you favorited them. Click a name to browse its posts.';
    }

    // one tooltip, positioned at the pointer; built with textContent
    const tip = $('#tip');
    $('#scroll').addEventListener('pointermove', event => {
      const td = event.target.closest('td'), info = td && cellInfo.get(td);
      if (!info) { tip.hidden = true; return; }
      tip.replaceChildren(el('strong', { textContent: info.value }), ...info.lines.map(line => el('span', { textContent: line })));
      tip.hidden = false;
      const x = Math.min(event.clientX + 14, innerWidth - tip.offsetWidth - 8), y = event.clientY + 16 + tip.offsetHeight > innerHeight ? event.clientY - tip.offsetHeight - 12 : event.clientY + 16;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    $('#scroll').addEventListener('pointerleave', () => { tip.hidden = true; });

    CATEGORIES.forEach(([key, title]) => {
      const button = el('button', { type: 'button', textContent: title });
      button.dataset.key = key;
      button.addEventListener('click', () => { if (state.category !== key) { state.category = key; load(); } });
      $('#category').append(button);
    });
    $('#range').addEventListener('change', e => { state.range = e.target.value; load(); });
    $('#rows').addEventListener('change', e => { state.rows = e.target.value; load(); });
    $('#shade').addEventListener('change', e => { state.shade = e.target.value; syncControls(); if (current) render(); });
    $('#numbers').addEventListener('change', e => { state.numbers = e.target.checked; syncControls(); if (current) render(); });
    load();
  </script>
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
    # exact=1: q is one tag or artist name picked from a list (trending, suggestions, a
    # post's tags), so match that name only. Typed searches match substrings, which
    # would make "macro" also find every "macropod" post.
    exact = args.get("exact", "") == "1"
    rating = args.get("rating", "").strip().lower()
    deleted = args.get("deleted", "").strip()
    media = args.get("media", "").strip().lower()

    if query and exact:
        where.append("(p.id IN (SELECT qa.post_id FROM posts_artists qa JOIN artists qar ON qar.artist_id = qa.artist_id WHERE qar.name = %s)"
                     " OR p.id IN (SELECT qt.post_id FROM posts_tags qt JOIN tags qtag ON qtag.tag_id = qt.tag_id WHERE qtag.name = %s))")
        values.extend([query, query])
    elif query:
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
    return render_template_string(PAGE, can_open=is_local_request(), site_header=site_header)


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
                                  can_open=is_local_request(), site_header=site_header,
                                  section_titles=TAG_SECTION_TITLES)


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
        remember_path(post_id, file_path)
        result.append({
            "id": post_id, "file_name": file_name, "file_url": file_url,
            "rating": rating or "unknown", "deleted": bool(deleted),
            "deletion_reason": reason, "artists": artists, "has_file": exists,
            "is_video": extension in VIDEO_EXTENSIONS, "ext": extension,
            "media_url": f"/media/{post_id}" if exists else None,
            "thumb_url": f"/thumb/{post_id}" if exists else None,
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


# post id -> file path, filled from each page of /api/posts so the ~48 thumbnail requests that
# follow need no database round trip each. A path that no longer exists is looked up again.
_known_paths: "OrderedDict[int, str]" = OrderedDict()
_KNOWN_PATHS_MAX = 20000
_known_paths_lock = threading.Lock()


def remember_path(post_id, file_path):
    if not file_path:
        return
    with _known_paths_lock:
        _known_paths[post_id] = file_path
        _known_paths.move_to_end(post_id)
        while len(_known_paths) > _KNOWN_PATHS_MAX:
            _known_paths.popitem(last=False)


def known_file_path(post_id):
    """Like post_file_path, but uses the path remembered from the post listing when it still exists."""
    with _known_paths_lock:
        path = _known_paths.get(post_id)
    if path and os.path.isfile(path):
        return path
    path = post_file_path(post_id)
    remember_path(post_id, path)
    return path


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
# One top-10 list each, in sidebar order. Copyright, character and species are e621 tag types;
# e621 files fetish tags under General, so that list holds tags marked fetish in the
# Generic tags tool (fff gui generic-tags) or named in its default list
# (data/kink_tags.txt). Tags marked generic there never trend.
TRENDING_CATEGORIES = (("artist", "Artists"), ("copyright", "Copyrights"), ("character", "Characters"),
                       ("species", "Species"), ("fetish", "Fetish"))
# How each list is ordered (the sidebar toggle):
#   top     most recent posts first.
#   rising  by lift: share of recent posts against share of the whole archive, so
#           names that suddenly turn up a lot beat ones that are always common.
TRENDING_ORDERS = ("top", "rising")
# e621 files these warnings and placeholders under "artist", so they end up in the
# artists table; they would top the Artists list by post count.
NOT_ARTISTS = frozenset(name.lower() for name in FLAG_TOKENS) | {
    "avoid_posting", "unknown", "unknown_artist", "unknown_artist_signature", "anonymous_artist",
    "epilepsy_warning", "jumpscare_warning", "artist_request"}
_trending_cache = {}


def trending_candidates(cursor, days, now=None, default_fetish=None):
    """Every artist/tag on enough posts archived in the last `days`, bucketed by list.

    Each bucket holds (name, recent posts, all-time posts). A minimum recent count
    keeps a name seen on one or two new posts out of the lists. Tags with no saved
    mark count as fetish when the default fetish list names them.
    """
    if default_fetish is None:
        default_fetish = generic_tags.default_fetish_names()
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
        buckets["artist"] = [(name, recent, total) for name, recent, total in cursor.fetchall()
                             if name.lower() not in NOT_ARTISTS]
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
            if is_generic is None and name.lower() in default_fetish:
                is_fetish = True  # no saved mark: the default list applies
            key = "fetish" if is_fetish else kind
            if key in buckets and key != "artist":
                buckets[key].append((name, recent, total))
    return {"days": days, "recent_posts": recent_posts, "total_posts": total_posts, "buckets": buckets}


def rank_trending(candidates, order="top", limit=TRENDING_LIMIT):
    """The sidebar's lists from trending_candidates, ordered by `order` (see TRENDING_ORDERS)."""
    recent_posts, total_posts = candidates["recent_posts"], candidates["total_posts"]
    if order == "rising":
        def key(row):
            return -(row[1] / row[2]), -row[1], row[0]
    else:
        def key(row):
            return -row[1], -(row[1] / row[2]), row[0]

    def ranked(rows):
        return [{"name": name, "recent": recent, "total": total,
                 "lift": round((recent / recent_posts) / (total / total_posts), 1)}
                for name, recent, total in sorted(rows, key=key)[:limit]]

    return {"days": candidates["days"], "order": order, "recent_posts": recent_posts,
            "categories": [{"key": cat, "title": title, "items": ranked(candidates["buckets"][cat])}
                           for cat, title in TRENDING_CATEGORIES]}


def trending(cursor, days, now=None, limit=TRENDING_LIMIT, order="top", default_fetish=None):
    return rank_trending(trending_candidates(cursor, days, now=now, default_fetish=default_fetish), order, limit)


@app.get("/api/trending")
def trending_api():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    if days not in TRENDING_WINDOWS:
        days = 30
    order = request.args.get("order", "top")
    if order not in TRENDING_ORDERS:
        order = "top"
    cached = _trending_cache.get(days)
    if cached and time.monotonic() - cached[0] < TRENDING_CACHE_SECONDS:
        return jsonify(rank_trending(cached[1], order))
    try:
        connection = database_connection()
        try:
            generic_tags.ensure_table(connection)
            cursor = connection.cursor()
            candidates = trending_candidates(cursor, days)
            cursor.close()
        finally:
            connection.close()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    _trending_cache[days] = (time.monotonic(), candidates)  # both orders rank from these
    return jsonify(rank_trending(candidates, order))


for _name in ("PAGE", "DETAIL_PAGE", "TRENDS_PAGE"):
    globals()[_name] = globals()[_name].replace("/* @theme */", THEME_CSS)
TAG_SECTION_TITLES = {"copyright": "Copyrights", "character": "Characters", "species": "Species",
                      "general": "General", "lore": "Lore", "meta": "Meta", "invalid": "Invalid"}


TREND_RANGES = (3, 6, 12)      # months; the trends page offers these
TREND_ROW_CHOICES = (10, 20, 30)
_trends_cache = {}


def _category_links(cursor, category, default_fetish):
    """(post_id, name) for every link in one trending category, with the same rules as the
    sidebar: generic tags never count, and a fetish tag counts only under Fetish.

    Bucketing by month happens in Python: grouping by month in MySQL ran through a temporary
    table over every tag link (~37s for Species); fetching the links is ~1s.
    """
    if category == "artist":
        cursor.execute("SELECT pa.post_id, ar.name FROM posts_artists pa JOIN artists ar ON ar.artist_id = pa.artist_id")
        return [(post_id, name) for post_id, name in cursor.fetchall() if name.lower() not in NOT_ARTISTS]
    classified = f"LEFT JOIN {generic_tags.TABLE} tc ON tc.tag_id = t.tag_id"
    if category == "fetish":
        names = sorted(default_fetish)
        listed = f" OR (tc.tag_id IS NULL AND t.name IN ({_placeholders(names)}))" if names else ""
        cursor.execute(f"SELECT pt.post_id, t.name FROM posts_tags pt JOIN tags t ON t.tag_id = pt.tag_id {classified}"
                       f" WHERE tc.is_fetish = 1{listed}", names)
        return cursor.fetchall()
    cursor.execute(f"SELECT pt.post_id, t.name, tc.tag_id IS NULL FROM posts_tags pt JOIN tags t ON t.tag_id = pt.tag_id"
                   f" {classified} WHERE LOWER(t.tag_type) = %s AND COALESCE(tc.is_generic, 0) = 0"
                   " AND COALESCE(tc.is_fetish, 0) = 0", (category,))
    return [(post_id, name) for post_id, name, unsaved in cursor.fetchall()
            if not (unsaved and name.lower() in default_fetish)]  # listed fetish tags count as fetish


def _as_datetime(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _month_key(day) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def trends_overview(cursor, months, category, limit=20, now=None, default_fetish=None):
    """Month-by-month counts for the top names in one category, for the trends page.

    Months go by when FFF archived each post. The first day posts were archived is the
    initial import (everything favorited before tracking began), so it becomes its own
    "before" column instead of swamping its month. Rows are the names on the most
    posts archived within the range.
    """
    now = now or datetime.now()
    if default_fetish is None:
        default_fetish = generic_tags.default_fetish_names()
    title = dict(TRENDING_CATEGORIES)[category]
    cursor.execute("SELECT id, ingested_at FROM posts WHERE ingested_at IS NOT NULL")
    posts = [(post_id, _as_datetime(ts)) for post_id, ts in cursor.fetchall()]
    if not posts:
        return {"category": category, "title": title, "range": months, "months": [], "rows": [],
                "before": {"posts": 0, "label": "", "short": "", "date": ""}}
    import_day = min(ts for _, ts in posts).date()
    tracking_start = datetime.combine(import_day + timedelta(days=1), datetime.min.time())

    first = date(now.year, now.month, 1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    first = max(first, tracking_start.date().replace(day=1))
    keys, day = [], first
    while day <= now.date():
        keys.append(_month_key(day))
        day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)

    bucket_of, month_posts, before_posts = {}, dict.fromkeys(keys, 0), 0
    for post_id, ts in posts:
        if ts < tracking_start:
            bucket_of[post_id] = "before"
            before_posts += 1
        elif _month_key(ts) in month_posts:
            bucket_of[post_id] = _month_key(ts)
            month_posts[_month_key(ts)] += 1

    counts = {}
    for post_id, name in _category_links(cursor, category, default_fetish):
        bucket = bucket_of.get(post_id)
        if bucket:
            per_name = counts.setdefault(name, {})
            per_name[bucket] = per_name.get(bucket, 0) + 1

    def in_range(per_name):
        return sum(n for bucket, n in per_name.items() if bucket != "before")

    top = sorted(counts.items(), key=lambda item: (-in_range(item[1]), -item[1].get("before", 0), item[0]))[:limit]
    return {
        "category": category, "title": title, "range": months,
        "before": {"posts": before_posts, "date": f"{import_day:%b} {import_day.day}, {import_day.year}",
                   "short": f"{import_day:%b} {import_day.day} import",
                   "label": f"Imported {import_day:%b} {import_day.day}, {import_day.year} (favorited before tracking)"},
        "months": [{"key": key, "label": f"{date(int(key[:4]), int(key[5:]), 1):%b %Y}", "posts": month_posts[key]}
                   for key in keys],
        "rows": [{"name": name, "total": in_range(per_name), "before": per_name.get("before", 0),
                  "counts": [per_name.get(key, 0) for key in keys]} for name, per_name in top],
    }


@app.get("/trends")
def trends_page():
    return render_template_string(TRENDS_PAGE, categories=TRENDING_CATEGORIES, site_header=site_header)


@app.get("/api/trends")
def trends_api():
    def choice(name, allowed, default):
        try:
            value = int(request.args.get(name, default))
        except ValueError:
            return default
        return value if value in allowed else default

    months = choice("range", TREND_RANGES, 12)
    limit = choice("rows", TREND_ROW_CHOICES, 20)
    category = request.args.get("category", "species")
    if category not in dict(TRENDING_CATEGORIES):
        category = "species"
    key = (months, category, limit)
    cached = _trends_cache.get(key)
    if cached and time.monotonic() - cached[0] < TRENDING_CACHE_SECONDS:
        return jsonify(cached[1])
    try:
        connection = database_connection()
        try:
            generic_tags.ensure_table(connection)
            cursor = connection.cursor()
            result = trends_overview(cursor, months, category, limit)
            cursor.close()
        finally:
            connection.close()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    _trends_cache[key] = (time.monotonic(), result)
    return jsonify(result)


# Browsers may reuse files for this long before asking again (a changed file gets a new
# thumbnail, and at worst shows the old one until then).
MEDIA_MAX_AGE = 24 * 3600
THUMB_MAX_AGE = 7 * 24 * 3600


def _cached_privately(response):
    """Let the browser keep the file, but no shared cache (proxy): the archive is private."""
    response.cache_control.public = False
    response.cache_control.private = True
    return response


@app.get("/media/<int:post_id>")
def media(post_id):
    try:
        path = known_file_path(post_id)
    except Exception:
        abort(404)
    if not path:
        abort(404)
    return _cached_privately(send_file(path, conditional=True, max_age=MEDIA_MAX_AGE))


@app.get("/thumb/<int:post_id>")
def thumb(post_id):
    """A small cached preview (fff.gui.thumbnails) for the grid: images shrunk, videos a frame."""
    try:
        path = known_file_path(post_id)
    except Exception:
        abort(404)
    if not path:
        abort(404)
    is_video = Path(path).suffix.lower().lstrip(".") in VIDEO_EXTENSIONS
    preview = thumbnails.thumbnail(path, video=is_video)
    if preview is None:
        abort(404)
    return _cached_privately(send_file(preview, mimetype="image/webp", conditional=True, max_age=THUMB_MAX_AGE))


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
