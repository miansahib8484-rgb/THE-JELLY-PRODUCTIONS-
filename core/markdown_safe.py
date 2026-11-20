"""Markdown -> HTML with every tag/attribute outside a safe list removed (articles and policy pages are written by staff,
but raw HTML and scripts in Markdown must never reach visitors)."""
import markdown as _md

try:
    import nh3
except ImportError:  # pragma: no cover
    nh3 = None

ALLOWED_TAGS = {'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'b', 'i', 'u', 'code', 'pre', 'blockquote',
                'ul', 'ol', 'li', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'sup', 'sub'}
ALLOWED_ATTRS = {'a': {'href', 'title'}, 'img': {'src', 'alt', 'title'}, 'th': {'align'}, 'td': {'align'}}


def render_markdown(text):
    html = _md.markdown(text or '', extensions=['extra', 'sane_lists'])
    if nh3 is None:   # pragma: no cover  (nh3 is in requirements.txt)
        import html as _html
        return '<p>' + _html.escape(text or '').replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes={'http', 'https', 'mailto'}, link_rel='noopener noreferrer')
