"""Race conditions: need a real database with row locks (PostgreSQL). Skipped on SQLite."""
import threading
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from core import services
from core.models import Batch, Course, Enrollment, Payment, PromoCode

User = get_user_model()


def _mk(email):
    u = User.objects.create_user(username=email, email=email, password='Passw0rd!x', first_name='s')
    return u


@skipUnlessDBFeature('has_select_for_update')
class Races(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin@x.com', 'admin@x.com', 'Passw0rd!x')
        self.course = Course.objects.create(title='C1', price=1000)
        self.batch = Batch.objects.create(course=self.course, start_date=timezone.localdate() + timedelta(days=5), seats_total=1, status='open')

    def _parallel(self, fns):
        out, barrier = [None] * len(fns), threading.Barrier(len(fns))

        def run(i, fn):
            try:
                barrier.wait()
                out[i] = fn()
            except Exception as exc:       # noqa: BLE001
                out[i] = exc
            finally:
                connections.close_all()
        ts = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        return out

    def test_last_seat_goes_to_one_student(self):
        users = [_mk(f'u{i}@x.com') for i in range(6)]

        def reserve(u):
            def go():
                c = Client()
                c.force_login(u)
                return c.post(reverse('website:enroll'), dict(course=self.course.pk, batch=self.batch.pk, phone='0300 1111111', city='x',
                                                               gateway='easypaisa', terms='on', transaction_reference='T')).status_code
            return go
        self._parallel([reserve(u) for u in users])
        held = Enrollment.objects.filter(batch=self.batch, status__in=['pending_payment', 'active', 'completed']).count()
        self.batch.refresh_from_db()
        self.assertEqual(held, 1, 'more students than seats')
        self.assertEqual((self.batch.seats_filled, self.batch.status), (1, 'full'))

    def test_same_payment_approved_once(self):
        s = _mk('s@x.com')
        e = Enrollment.objects.create(user=s, course=self.course, batch=self.batch)
        p = Payment.objects.create(enrollment=e, amount=1000, transaction_reference='T')
        res = self._parallel([lambda: services.approve_payment(p, self.admin) for _ in range(4)])
        self.assertEqual(sum(1 for r in res if r is None), 1)
        self.assertTrue(all(isinstance(r, services.PaymentStateError) for r in res if r is not None))

    def test_promo_limit_under_parallel_approval(self):
        PromoCode.objects.create(code='ONCE', percent_off=10, max_uses=1)
        self.batch.seats_total = 5
        self.batch.save()
        pays = []
        for i in range(4):
            e = Enrollment.objects.create(user=_mk(f'p{i}@x.com'), course=self.course, batch=self.batch)
            pays.append(Payment.objects.create(enrollment=e, amount=900, transaction_reference='T', promo_code='ONCE'))
        self._parallel([(lambda p=p: services.approve_payment(p, self.admin)) for p in pays])
        self.assertEqual(PromoCode.objects.get(code='ONCE').uses_count, 1)
