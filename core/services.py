"""Business rules for payments and enrollments, used by the admin dashboard, the student pages and tests.

Seat and "N enrolled" counters are never edited by hand: they are recounted from the enrollments by the signals in
core/signals.py whenever an enrollment or batch changes (see `recount`)."""
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from django.db.models import F

from .models import Batch, Course, Enrollment, Payment, PromoCode
from .utils import notify

LEARNING = ('active', 'completed')
HOLDS_SEAT = ('pending_payment', 'active', 'completed')


class PaymentStateError(Exception):
    """The requested change is not allowed from the payment's or enrollment's current state."""


def recount(course_id, batch_id=None):
    """Recompute 'students enrolled' (paid learners only) and the batch's held seats (reservations count) from real rows."""
    Course.objects.filter(pk=course_id).update(
        students_enrolled_count=Enrollment.objects.filter(course_id=course_id, status__in=LEARNING).count())
    if batch_id:
        batch = Batch.objects.filter(pk=batch_id).first()
        if not batch:
            return
        held = Enrollment.objects.filter(batch_id=batch_id, status__in=HOLDS_SEAT).count()
        status = batch.status
        if status == 'open' and held >= batch.seats_total:
            status = 'full'
        elif status == 'full' and held < batch.seats_total:
            status = 'open'
        # an 'upcoming' batch keeps its status (it used to become 'open' for good after one full/free cycle); the enroll view still
        # refuses it when seats_left is 0
        Batch.objects.filter(pk=batch_id).update(seats_filled=held, status=status)


@transaction.atomic
def approve_payment(payment, admin_user):
    payment = Payment.objects.select_for_update().select_related('enrollment__course', 'enrollment__user').get(pk=payment.pk)
    e = payment.enrollment
    if payment.status != 'pending':
        raise PaymentStateError('This payment was already reviewed.')
    if e.status != 'pending_payment':
        raise PaymentStateError(f'This enrollment is {e.get_status_display().lower()}: the student has to reserve a seat again first.')
    payment.status, payment.reviewed_by, payment.reviewed_at, payment.admin_note = 'paid', admin_user, timezone.now(), ''
    payment.save()
    e.status = 'active'
    e.save(update_fields=['status'])
    if payment.promo_code:       # a code is "used" once the discounted payment is approved
        promo = PromoCode.objects.select_for_update().filter(code=payment.promo_code).first()
        if promo and promo.max_uses is not None and promo.uses_count >= promo.max_uses:
            raise PaymentStateError(f'The promo code {promo.code} has already been used {promo.max_uses} time(s). Raise its limit in '
                                    'Payments & promos, or reject this payment so the student can resubmit without the code.')
        if promo:
            PromoCode.objects.filter(pk=promo.pk).update(uses_count=F('uses_count') + 1)
    notify(e.user, 'Payment approved', f'You can start {e.course.title} now.', reverse('student:player', args=[e.course.slug]), email=True)


@transaction.atomic
def reject_payment(payment, admin_user, note=''):
    payment = Payment.objects.select_for_update().select_related('enrollment__user').get(pk=payment.pk)
    if payment.status != 'pending':
        raise PaymentStateError('This payment was already reviewed.')
    payment.status, payment.reviewed_by, payment.reviewed_at, payment.admin_note = 'failed', admin_user, timezone.now(), note
    payment.save()
    notify(payment.enrollment.user, 'Payment needs attention', note or 'Please re-submit your payment proof.', reverse('student:billing'), email=True)


@transaction.atomic
def cancel_enrollment(enrollment, quiet=False):
    """Cancel an enrollment that has not been paid. Paid ones must be refunded (refund_enrollment).
    quiet=True skips the notification (used when a student simply moves to another batch)."""
    if enrollment.status in ('cancelled', 'refunded'):
        return
    payment = Payment.objects.filter(enrollment=enrollment).first()
    if payment and payment.status == 'paid':
        raise PaymentStateError('This enrollment is paid. Use Refund so the payment record stays correct.')
    if payment and payment.status == 'pending':
        payment.status, payment.admin_note = 'failed', 'Enrollment cancelled'
        payment.save(update_fields=['status', 'admin_note'])
    enrollment.status = 'cancelled'
    enrollment.save(update_fields=['status'])
    if not quiet:
        notify(enrollment.user, 'Enrollment cancelled', f'Your reservation for {enrollment.course.title} was cancelled.', reverse('student:billing'), email=True)


@transaction.atomic
def refund_enrollment(enrollment, admin_user, note=''):
    """Mark a paid enrollment as refunded: payment -> refunded, enrollment -> refunded, seat released."""
    payment = Payment.objects.filter(enrollment=enrollment).first()
    if enrollment.status in ('cancelled', 'refunded') or not payment or payment.status != 'paid':
        raise PaymentStateError('Only an approved payment can be refunded.')
    payment.status, payment.reviewed_by, payment.reviewed_at = 'refunded', admin_user, timezone.now()
    payment.admin_note = note or payment.admin_note
    payment.save()
    enrollment.status = 'refunded'
    enrollment.save(update_fields=['status'])
    if payment.promo_code:          # approving counted one use of the code; a refund gives it back
        PromoCode.objects.filter(code=payment.promo_code, uses_count__gt=0).update(uses_count=F('uses_count') - 1)
    notify(enrollment.user, 'Payment refunded', f'Your payment for {enrollment.course.title} was refunded.' + (f' Note: {note}' if note else ''), reverse('student:billing'), email=True)


@transaction.atomic
def mark_enrollment_paid(enrollment, admin_user):
    """Admin confirms money was received outside the site."""
    if enrollment.status in ('cancelled', 'refunded'):
        raise PaymentStateError('This enrollment is cancelled: the student has to reserve a seat again first.')
    payment, _ = Payment.objects.get_or_create(enrollment=enrollment, defaults={'amount': enrollment.course.price})
    if payment.status == 'paid':
        return
    payment.status = 'pending'
    payment.save(update_fields=['status'])
    approve_payment(payment, admin_user)
