"""Public-site data that is computed from real records (never typed in by hand)."""

from django.db.models import Avg, Count, Q
from django.utils import timezone

from .models import Batch, Certificate, Course, Enrollment, Faq, Review, SiteSettings, SiteStat
from .request_cache import per_request


@per_request
def live_stats():
    """Counts shown in the public stat band. A figure is shown only when it is real and greater than zero."""
    learners = Enrollment.objects.filter(status__in=['active', 'completed']).values('user').distinct().count()
    courses = Course.objects.filter(is_published=True).count()
    reviews = Review.objects.aggregate(a=Avg('rating'), n=Count('id'))
    done = Enrollment.objects.filter(status='completed').count()
    total = Enrollment.objects.filter(status__in=['active', 'completed']).count()
    items = []
    if learners:
        items.append({'value': f'{learners:,}', 'label': 'student learning with us' if learners == 1 else 'students learning with us'})
    if courses:
        items.append({'value': str(courses), 'label': f'live course{"s" if courses != 1 else ""}'})
    if reviews['n']:
        items.append({'value': f"{reviews['a']:.1f}/5", 'label': f"average rating from {reviews['n']} review{'s' if reviews['n'] != 1 else ''}"})
    if total and done:
        items.append({'value': f'{round(done / total * 100)}%', 'label': 'of learners finish the course'})
    certs = Certificate.objects.count()
    if certs:
        items.append({'value': f'{certs:,}', 'label': 'certificate issued' if certs == 1 else 'certificates issued'})
    items += [{'value': s.value, 'label': s.label} for s in SiteStat.objects.all()]   # optional extras the admin typed
    return items[:4]


@per_request
def ticker_items():
    """Scrolling announcements: real next batch, seats left and best discount, then the admin's own lines."""
    out = []
    today = timezone.localdate()
    b = (Batch.objects.filter(status__in=['upcoming', 'open'], start_date__gte=today, course__is_published=True)
         .select_related('course').order_by('start_date').first())
    if b:
        out.append(f'🎓 Next batch: {b.course.title} starts {b.start_date:%d %b}' + (f', {b.seats_left} seats left' if b.seats_left > 0 else ', full'))
    else:
        out.append('\U0001F393 Next batch starts soon \u2014 seats are limited')
    best = max((c.discount_percent for c in Course.objects.filter(is_published=True)), default=0)
    if best:
        out.append(f'💸 Save up to {best}% on selected courses')
    site = SiteSettings.get()
    if site.batch_cap:
        out.append(f'👥 Small live batches, capped at {site.batch_cap}')
    if site.refund_days:
        out.append(f'🛡️ {site.refund_days}-day honest refund window')
    out += site.ticker_lines
    return out


def faqs(page):
    return Faq.objects.filter(Q(page='all') | Q(page=page))


def instructors():
    """Active teachers who actually teach a published course."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return (User.objects.filter(is_active=True, profile__role='teacher', courses_taught__is_published=True).select_related('profile')
            .annotate(n=Count('courses_taught', filter=Q(courses_taught__is_published=True), distinct=True)).order_by('-n', 'first_name'))


@per_request
def mentor():
    """The instructor spotlight: the founder from Site settings (the original design's Arshad by default), else the busiest real
    teacher. None when neither exists. `first` is the first name used in the copy ("Arshad reviews your actual work")."""
    from .models import SiteSettings, stock_photo
    site = SiteSettings.get()
    if site.founder_name:
        photo = site.founder_photo.url if site.founder_photo else (stock_photo('arshad-nadeem', 700, 700) if SiteSettings.stock_enabled() else '')
        return {'name': site.founder_name, 'first': site.founder_name.split()[0], 'title': site.founder_title, 'quote': site.founder_quote,
                'home_quote': site.home_quote or site.founder_quote, 'home_title': site.home_byline or site.founder_title,
                'photo': photo, 'photo_small': photo.replace('/700/700', '/600/600') if 'picsum' in photo else photo}
    t = instructors().first()
    if t:
        name = t.get_full_name() or t.get_username()
        photo = t.profile.avatar_url
        return {'name': name, 'first': name.split()[0], 'title': t.profile.title, 'quote': t.profile.bio, 'home_quote': t.profile.bio,
                'home_title': t.profile.title, 'photo': photo, 'photo_small': photo}
    return None


@per_request
def hero_slides():
    """Pictures behind the home headline (uploaded ones first; stock-photo sample rows vanish when stock photos are off)."""
    from .models import HeroSlide
    return [src for src in (s.src for s in HeroSlide.objects.all()) if src]


@per_request
def team():
    """People for the About page: the real instructors first (teacher accounts with a published course), then the staff added in
    Admin > Site content > Team. A staff row with the same name as an instructor is not shown twice.
    Returns (cards, number_of_staff_cards); a card has .name .role .src."""
    from .models import TeamMember
    cards, names = [], set()
    rows = list(TeamMember.objects.all())
    by_name = {m.name.lower(): m for m in rows}
    for t in instructors():
        name = t.get_full_name() or t.get_username()
        names.add(name.lower())
        row = by_name.get(name.lower())
        src = t.profile.avatar_url if (t.profile.avatar or not row) else row.src          # an uploaded photo wins, else the team card's picture
        cards.append({'name': name, 'role': (row.role if row and row.role else t.profile.title) or 'Instructor', 'src': src})
    staff = [m for m in rows if m.name.lower() not in names]
    cards += [{'name': m.name, 'role': m.role, 'src': m.src} for m in staff]
    return cards, len(staff)


@per_request
def milestones():
    from .models import Milestone
    return list(Milestone.objects.all())


@per_request
def gallery():
    from .models import GalleryImage
    return [g for g in GalleryImage.objects.all() if g.src]


def page_quote(page):
    from .models import PageQuote
    return PageQuote.objects.filter(page=page).first()
