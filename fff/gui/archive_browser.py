# fff: run as 'fff web'; the __main__ block became parse_args(argv) + main(argv).
"""Local web browser for searching and previewing archived e621 posts."""

import argparse
import mimetypes
import os
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, render_template_string, request, send_file

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql


app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

PAGE_SIZE = 48
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v", "avi", "mkv"}

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
    main { max-width:1280px; margin:0 auto; padding:28px 24px 50px; }
    .controls { display:grid; grid-template-columns:minmax(220px, 1fr) 160px 160px 190px auto; gap:10px; align-items:end; margin-bottom:18px; }
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
    .artists { margin:7px 0 3px; color:var(--accent-dark); font-weight:700; overflow-wrap:anywhere; }
    .meta { color:var(--muted); font-size:13px; }
    .deleted { margin-top:8px; padding:6px 8px; border-left:3px solid var(--accent); background:#de3c2e18; color:var(--accent); font-size:13px; }
    .links { margin-top:8px; font-family:"Trebuchet MS", sans-serif; font-size:12px; }
    a { color:var(--teal); }
    .pager { display:flex; justify-content:center; align-items:center; gap:14px; margin-top:24px; }
    .pager button { width:auto; min-width:90px; }
    .empty { padding:50px 15px; text-align:center; color:var(--muted); }
    @media (max-width:700px) { .controls { grid-template-columns:1fr 1fr; } .controls .search { grid-column:1/-1; } .grid { grid-template-columns:repeat(auto-fill, minmax(160px, 1fr)); gap:9px; } }
  </style>
</head>
<body>
  <header><h1>Local E621 Archive</h1><p class="subtitle">Browse the files and metadata already in your archive.</p></header>
  <main>
    <form id="filters" class="controls">
      <div class="search"><label for="q">Search</label><input id="q" name="q" placeholder="artist, tag, filename, or post ID"></div>
      <div><label for="rating">Rating</label><select id="rating"><option value="">All ratings</option><option>safe</option><option>questionable</option><option>explicit</option></select></div>
      <div><label for="deleted">Status</label><select id="deleted"><option value="">All posts</option><option value="0">Active</option><option value="1">Deleted</option></select></div>
    <div><label for="sort">Sort by</label><select id="sort"><option value="created_at_desc">Created: newest</option><option value="created_at_asc">Created: oldest</option><option value="deleted_at_desc">Deleted: newest</option><option value="deleted_at_asc">Deleted: oldest</option></select></div>
      <button type="submit">Search archive</button>
    </form>
    <div class="stats" id="stats">Loading archive...</div>
    <section class="grid" id="grid"></section>
    <div class="pager"><button id="prev">Previous</button><span id="page"></span><button id="next">Next</button></div>
  </main>
  <script>
    let currentPage = 1;
        const initialSearch = new URLSearchParams(window.location.search).get('q') || '';
        document.querySelector('#q').value = initialSearch;
    const params = () => new URLSearchParams({ q: document.querySelector('#q').value.trim(), rating: document.querySelector('#rating').value, deleted: document.querySelector('#deleted').value, sort: document.querySelector('#sort').value, page: currentPage });
    const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    function card(post) {
      const media = post.has_file ? (post.is_video ? `<video class="preview" controls preload="metadata" src="${post.media_url}"></video>` : `<img class="preview" loading="lazy" src="${post.media_url}" alt="Post ${post.id}">`) : '<div class="preview missing">File unavailable</div>';
      const deleted = post.deleted ? `<div class="deleted">Deleted${post.deletion_reason ? ': ' + esc(post.deletion_reason) : ''}</div>` : '';
    return `<article class="card"><a class="card-open" href="/post/${post.id}">${media}</a><div class="body"><div class="topline"><a class="id" href="/post/${post.id}">#${post.id}</a><span class="rating">${esc(post.rating)}</span></div><div class="artists">${esc(post.artists || 'Unknown artist')}</div><div class="meta">${esc(post.file_name || 'No filename')}</div>${deleted}<div class="links">${post.file_url ? `<a href="${esc(post.file_url)}" target="_blank" rel="noreferrer">Open source</a>` : ''}</div></div></article>`;
    }
    async function load() {
      const grid = document.querySelector('#grid'); grid.innerHTML = '<div class="empty">Loading...</div>';
      const response = await fetch('/api/posts?' + params()); const data = await response.json();
      if (!response.ok) { grid.innerHTML = `<div class="empty">${esc(data.error || 'Unable to load posts')}</div>`; return; }
      document.querySelector('#stats').innerHTML = `<strong>${data.total.toLocaleString()}</strong> matching posts`;
      grid.innerHTML = data.posts.length ? data.posts.map(card).join('') : '<div class="empty">No posts matched those filters.</div>';
      document.querySelector('#page').textContent = `Page ${data.page} of ${Math.max(1, data.pages)}`;
      document.querySelector('#prev').disabled = data.page <= 1; document.querySelector('#next').disabled = data.page >= data.pages;
    }
    document.querySelector('#filters').addEventListener('submit', event => { event.preventDefault(); currentPage = 1; load(); });
    document.querySelector('#prev').addEventListener('click', () => { if (currentPage > 1) { currentPage--; load(); } });
    document.querySelector('#next').addEventListener('click', () => { currentPage++; load(); });
    load();
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
        .deleted { margin-top:16px; padding:10px; border-left:3px solid var(--accent); background:#de3c2e18; color:var(--accent); }
        .tags { display:flex; flex-wrap:wrap; gap:6px; }
        .tag { display:inline-block; padding:4px 7px; border:1px solid var(--line); border-radius:3px; background:#1d2321; color:var(--teal); text-decoration:none; font-size:13px; overflow-wrap:anywhere; }
        .tag.artist { background:#de3c2e18; color:var(--accent); }
        dl { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin:0; }
        dt { color:var(--muted); }
        dd { margin:0; overflow-wrap:anywhere; }
        a { color:var(--teal); }
        .sources { margin:0; padding-left:20px; overflow-wrap:anywhere; }
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
                <span class="rating">{{ post.rating or 'unknown' }}</span>
                {% if post.deleted %}<div class="deleted"><strong>Deleted post</strong>{% if post.deletion_reason %}<br>{{ post.deletion_reason }}{% endif %}</div>{% endif %}
                <h2>Artists</h2>
                <div class="tags">{% for artist in artists %}<a class="tag artist" href="/?q={{ artist|urlencode }}">{{ artist }}</a>{% else %}<span>None recorded</span>{% endfor %}</div>
                {% for category, category_tags in tags.items() %}
                    <h2>{{ category|title }}</h2>
                    <div class="tags">{% for tag in category_tags %}<a class="tag" href="/?q={{ tag|urlencode }}">{{ tag }}</a>{% endfor %}</div>
                {% endfor %}
                <h2>File Details</h2>
                <dl><dt>Path</dt><dd>{{ post.file_path or 'Unavailable' }}</dd><dt>Type</dt><dd>{{ post.file_type or 'Unknown' }}</dd><dt>MD5</dt><dd>{{ post.md5 or 'Unavailable' }}</dd><dt>Deleted at</dt><dd>{{ post.deleted_at or 'Not deleted' }}</dd></dl>
                <h2>Links</h2>
                <p><a href="https://e621.net/posts/{{ post.id }}" target="_blank" rel="noreferrer">View on e621</a></p>
                {% if sources %}<ul class="sources">{% for source in sources %}<li><a href="{{ source }}" target="_blank" rel="noreferrer">{{ source }}</a></li>{% endfor %}</ul>{% endif %}
            </aside>
        </div>
    </main>
</body>
</html>
"""


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

    if query:
        like = f"%{query}%"
        if query.isdecimal():
            where.append("(p.id = %s OR p.file_name LIKE %s OR p.deletion_reason LIKE %s OR EXISTS (SELECT 1 FROM posts_artists qa JOIN artists qar ON qar.artist_id = qa.artist_id WHERE qa.post_id = p.id AND qar.name LIKE %s) OR EXISTS (SELECT 1 FROM posts_tags qt JOIN tags qtag ON qtag.tag_id = qt.tag_id WHERE qt.post_id = p.id AND qtag.name LIKE %s))")
            values.extend([int(query), like, like, like, like])
        else:
            where.append("(p.file_name LIKE %s OR p.deletion_reason LIKE %s OR EXISTS (SELECT 1 FROM posts_artists qa JOIN artists qar ON qar.artist_id = qa.artist_id WHERE qa.post_id = p.id AND qar.name LIKE %s) OR EXISTS (SELECT 1 FROM posts_tags qt JOIN tags qtag ON qtag.tag_id = qt.tag_id WHERE qt.post_id = p.id AND qtag.name LIKE %s))")
            values.extend([like, like, like, like])
    if rating in {"safe", "questionable", "explicit"}:
        where.append("p.rating = %s")
        values.append(rating)
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
    return render_template_string(PAGE)


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
    return render_template_string(DETAIL_PAGE, post=post, artists=artists, tags=tags, sources=sources)


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


@app.get("/media/<int:post_id>")
def media(post_id):
    try:
        connection = database_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT file_path FROM posts WHERE id = %s", (post_id,))
        row = cursor.fetchone()
        cursor.close()
        connection.close()
    except Exception:
        abort(404)
    if not row or not row[0] or not os.path.isfile(row[0]):
        abort(404)
    return send_file(row[0], conditional=True)


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
