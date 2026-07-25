"""Page snapshot: an injected DOM walker + text-tree formatting.

``SNAPSHOT_JS`` runs in the page (default realm) and:
  1. walks the DOM including *open* shadow roots,
  2. keeps visible, interactive/informative elements,
  3. assigns refs ``e1, e2, ...`` and stores ``window.__claudeRefs`` (ref -> element),
  4. returns a JSON string that Python turns into a compact indented tree.

Interaction tools later resolve a ref against ``window.__claudeRefs``; because
the script runs in the context's default realm, that map survives between the
snapshot call and the interaction call (until navigation).
"""

from __future__ import annotations

from typing import Any

# Cap so a pathological page can't return tens of thousands of nodes.
_MAX_ITEMS = 2000

SNAPSHOT_JS = r"""
() => {
  const refs = new Map();
  window.__claudeRefs = refs;
  let n = 0;
  const items = [];
  const MAX = %d;

  const INTERACTIVE_ROLES = new Set([
    'button','link','checkbox','radio','textbox','combobox','listbox','menuitem',
    'menuitemcheckbox','menuitemradio','tab','switch','searchbox','slider','option','spinbutton'
  ]);

  function isVisible(el){
    const style = window.getComputedStyle(el);
    if(!style) return false;
    if(style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    if(parseFloat(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if(r.width <= 0 && r.height <= 0) return false;
    return true;
  }

  function truncate(s, max){ s = s || ''; return s.length > max ? s.slice(0, max - 1) + '...' : s; }

  function labelFor(el){
    const al = el.getAttribute && el.getAttribute('aria-label');
    if(al && al.trim()) return al.trim();
    const lb = el.getAttribute && el.getAttribute('aria-labelledby');
    if(lb){
      const parts = lb.split(/\s+/).map(function(id){
        const t = document.getElementById(id);
        return t ? (t.innerText || t.textContent || '') : '';
      }).join(' ').trim();
      if(parts) return parts;
    }
    if(el.labels && el.labels.length){
      const t = Array.from(el.labels).map(function(l){ return l.innerText || l.textContent || ''; }).join(' ').trim();
      if(t) return t;
    }
    const ph = el.getAttribute && el.getAttribute('placeholder');
    if(ph && ph.trim()) return ph.trim();
    const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if(txt) return txt;
    const title = el.getAttribute && el.getAttribute('title');
    if(title && title.trim()) return title.trim();
    const img = el.querySelector && el.querySelector('img[alt]');
    if(img){ const a = img.getAttribute('alt'); if(a && a.trim()) return a.trim(); }
    return '';
  }

  function classify(el){
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const role = el.getAttribute ? el.getAttribute('role') : null;
    const type = (el.getAttribute && (el.getAttribute('type') || '')).toLowerCase();

    if(/^h[1-6]$/.test(tag)){
      const name = truncate(labelFor(el), 120);
      if(!name) return null;
      return {interactive:false, role:'heading', name:name, level: parseInt(tag[1], 10)};
    }

    if(tag === 'a' && el.hasAttribute('href')){
      return {interactive:true, role:'link', name: truncate(labelFor(el), 120), href: el.getAttribute('href')};
    }

    if(tag === 'button' || role === 'button' ||
       (tag === 'input' && ['button','submit','reset','image'].indexOf(type) >= 0)){
      let name = labelFor(el);
      if(!name && tag === 'input') name = el.value || type;
      const isSubmit = (type === 'submit') || (tag === 'button' && (el.type === 'submit' || (!el.type && !!el.form)));
      return {interactive:true, role:'button', name: truncate(name, 120), submit: !!isSubmit};
    }

    if((tag === 'input' && type === 'checkbox') || role === 'checkbox'){
      return {interactive:true, role:'checkbox', name: truncate(labelFor(el), 120), checked: !!el.checked};
    }
    if((tag === 'input' && type === 'radio') || role === 'radio'){
      return {interactive:true, role:'radio', name: truncate(labelFor(el), 120), checked: !!el.checked};
    }

    if(tag === 'select'){
      const sel = (el.options && el.selectedIndex >= 0) ? el.options[el.selectedIndex].text : '';
      return {interactive:true, role:'combobox', name: truncate(labelFor(el), 120), value: truncate(sel, 60)};
    }

    const TEXT_TYPES = ['text','search','email','url','tel','number','password','',
                        'date','datetime-local','month','week','time'];
    if(tag === 'textarea' || (tag === 'input' && TEXT_TYPES.indexOf(type) >= 0)){
      const isPw = (type === 'password');
      const val = isPw ? (el.value ? '***' : '') : truncate(el.value || '', 60);
      return {interactive:true, role: isPw ? 'password' : 'textbox',
              name: truncate(labelFor(el), 120), value: val, password: isPw};
    }

    if(role && INTERACTIVE_ROLES.has(role)){
      return {interactive:true, role: role, name: truncate(labelFor(el), 120)};
    }

    if(el.hasAttribute && el.hasAttribute('onclick')){
      const name = truncate(labelFor(el), 120);
      if(name) return {interactive:true, role:'clickable', name:name};
    }

    return null;
  }

  function collect(root){
    const kids = root.children;
    if(!kids) return;
    for(let i = 0; i < kids.length; i++){
      if(items.length >= MAX) return;
      const el = kids[i];
      let info = null;
      try { if(isVisible(el)) info = classify(el); } catch(e){}
      if(info){
        if(info.interactive){
          n++;
          const ref = 'e' + n;
          refs.set(ref, el);
          info.ref = ref;
        }
        items.push(info);
      }
      if(el.shadowRoot){ collect(el.shadowRoot); }
      collect(el);
    }
  }

  collect(document);
  return JSON.stringify({url: location.href, title: document.title, count: n, items: items});
}
""" % _MAX_ITEMS


READ_PAGE_JS = r"""
() => {
  const t = document.body ? (document.body.innerText || '') : '';
  return JSON.stringify({url: location.href, title: document.title, text: t});
}
"""


def format_snapshot(data: dict[str, Any]) -> str:
    """Turn the snapshot JSON into the compact ref tree Claude reads."""
    if not data:
        return "(snapshot returned nothing - is a page loaded?)"
    lines: list[str] = [
        f'Page: "{data.get("title", "")}"  ({data.get("url", "")})',
        f'{data.get("count", 0)} interactive element(s):',
        "",
    ]
    items = data.get("items", [])
    for it in items:
        role = it.get("role", "")
        name = it.get("name", "")
        if not it.get("interactive"):
            if role == "heading":
                lines.append(f'heading "{name}"')
            else:
                lines.append(f'{role} "{name}"')
            continue
        ref = it.get("ref", "?")
        line = f'[{ref}] {role} "{name}"'
        if role == "textbox":
            line += f' value="{it.get("value", "")}"'
        elif role == "password":
            if it.get("value"):
                line += " value=***"
        elif role in ("checkbox", "radio", "switch"):
            line += f' checked={str(bool(it.get("checked"))).lower()}'
        elif role == "combobox":
            line += f' value="{it.get("value", "")}"'
        lines.append(line)
    if not items:
        lines.append("(no visible interactive elements found)")
    return "\n".join(lines)
