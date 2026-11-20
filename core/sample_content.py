"""Load or remove the original design's sample content (see sample_content_data.py). Works with real models and, inside a data
migration, with the historical models of the `apps` registry."""
from pathlib import Path

from . import sample_content_data as d

SAMPLE_MODELS = ['HeroSlide', 'Partner', 'Milestone', 'GalleryImage', 'TeamMember', 'PageQuote', 'Faq', 'Testimonial', 'BlogPost']


def _picsum(seed, w, h):
    return f'https://picsum.photos/seed/{seed}/{w}/{h}'


def _blog_files():
    from django.conf import settings
    return sorted((Path(settings.BASE_DIR) / 'content' / 'sample_blog').glob('*.md'))


def load_sample_content(apps=None):
    """Fill every list that is still empty with the original content. Lists you already filled are never touched.
    Returns {model name: rows created}."""
    if apps is None:
        from django.apps import apps as django_apps
        get = lambda name: django_apps.get_model('core', name)
    else:
        get = lambda name: apps.get_model('core', name)
    made = {}

    def fill(name, rows):
        model = get(name)
        if model.objects.exists():
            return
        for row in rows:
            model.objects.create(is_sample=True, **row)
        made[name] = len(rows)

    fill('HeroSlide', [dict(image_url=_picsum(seed, 1600, 900), sort_order=i) for i, seed in enumerate(d.HERO_SEEDS)])
    fill('Partner', [dict(name=n, sort_order=i) for i, n in enumerate(d.PARTNERS)])
    fill('Milestone', [dict(year=y, title=t, text=x, sort_order=i) for i, (y, t, x) in enumerate(d.TIMELINE)])
    fill('GalleryImage', [dict(image_url=_picsum(seed, 700, 520), sort_order=i) for i, seed in enumerate(d.GALLERY_SEEDS)])
    fill('TeamMember', [dict(name=n, role=r, photo_url=_picsum(seed, 300, 300), sort_order=i) for i, (n, r, seed) in enumerate(d.TEAM)])
    fill('PageQuote', [dict(page=page, quote=q['quote'], name=q['name'], role=q['role'], photo_url=_picsum(q['seed'], 100, 100))
                       for page, q in d.QUOTES.items()])
    fill('Faq', [dict(page=page, question=q, answer=a, sort_order=i) for page, items in d.FAQS.items() for i, (q, a) in enumerate(items)])
    fill('Testimonial', [dict(name=n, role_label=r, quote=q) for n, r, q in d.TESTIMONIALS])
    posts = get('BlogPost')
    if not posts.objects.exists():
        n = 0
        for path in _blog_files():
            meta, body = {}, path.read_text(encoding='utf-8')
            if body.startswith('---') and body.count('---') >= 2:        # a file with an unclosed header is used as plain text, not a crash
                _, header, body = body.split('---', 2)
                for line in header.strip().splitlines():
                    if ':' in line:
                        k, v = line.split(':', 1)
                        meta[k.strip().lower()] = v.strip()
            published = None
            try:
                import datetime
                from django.utils import timezone
                published = timezone.make_aware(datetime.datetime.combine(datetime.date.fromisoformat(meta['date']), datetime.time(9, 0)))
            except (KeyError, ValueError):
                pass
            fields = dict(slug=path.stem, title=meta.get('title', path.stem.replace('-', ' ').title()), category=meta.get('category', 'news'),
                          author_name=meta.get('author', ''), excerpt=meta.get('excerpt', ''), body=body.strip(),
                          is_featured=meta.get('featured', '').lower() in ('1', 'true', 'yes'), is_sample=True)
            if published:
                fields['published_at'] = published
            post = posts.objects.create(**fields)
            if meta.get('read_time', '').isdigit():              # the real model computes read_time from the words; the design's own value wins
                posts.objects.filter(pk=post.pk).update(read_time=int(meta['read_time']))
            elif apps is not None:
                posts.objects.filter(pk=post.pk).update(read_time=max(1, round(len(body.split()) / 200)))
            n += 1
        if n:
            made['BlogPost'] = n
    return made


def clear_sample_content():
    """Delete every row that came from load_sample_content. Rows you added or edited stay. Returns {model: rows deleted}."""
    from django.apps import apps
    gone = {}
    for name in SAMPLE_MODELS:
        qs = apps.get_model('core', name).objects.filter(is_sample=True)
        n = qs.count()
        if n:
            qs.delete()
            gone[name] = n
    return gone
