from django.contrib import admin

from . import models as m


class ModuleInline(admin.TabularInline):
    model = m.CurriculumModule
    extra = 0


class BatchInline(admin.TabularInline):
    model = m.Batch
    extra = 0


@admin.register(m.Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'level', 'price', 'teacher', 'is_published', 'is_featured')
    list_filter = ('level', 'is_published', 'is_featured')
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}
    inlines = [ModuleInline, BatchInline]


@admin.register(m.LiveClass)
class LiveClassAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'starts_at', 'platform', 'ended_early')
    list_filter = ('course', 'platform')


class RecordsAdmin(admin.ModelAdmin):
    """Money, enrollments and certificates change only through the dashboards, which keep notifications, seat counters, promo
    usage and the audit fields (reviewed_by / reviewed_at) consistent. This site is for looking things up, not for editing them."""
    list_per_page = 50
    editable_fields = ()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name not in self.editable_fields and f.editable] + list(self.readonly_fields)


@admin.register(m.Payment)
class PaymentAdmin(RecordsAdmin):
    list_display = ('enrollment', 'amount', 'gateway', 'status', 'created_at')
    list_filter = ('status', 'gateway')
    list_select_related = ('enrollment__user', 'enrollment__course')
    search_fields = ('enrollment__user__email', 'transaction_reference')
    editable_fields = ('admin_note',)


@admin.register(m.Enrollment)
class EnrollmentAdmin(RecordsAdmin):
    list_display = ('user', 'course', 'batch', 'status', 'enrolled_at')
    list_filter = ('status',)
    list_select_related = ('user', 'course', 'batch')
    search_fields = ('user__email', 'course__title')
    editable_fields = ('notes',)


@admin.register(m.Certificate)
class CertificateAdmin(RecordsAdmin):
    list_display = ('student', 'course', 'issued_on', 'code')
    list_select_related = ('student', 'course')
    search_fields = ('student__email', 'code')


@admin.register(m.Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('course', 'user', 'rating', 'created_at')
    list_select_related = ('course', 'user')
    raw_id_fields = ('user',)          # a drop-down of every user would not scale


@admin.register(m.Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'is_read', 'created_at')
    list_select_related = ('user',)
    raw_id_fields = ('user',)
    list_per_page = 50


@admin.register(m.LectureProgress)
class LectureProgressAdmin(admin.ModelAdmin):
    raw_id_fields = ('user', 'lecture')
    list_per_page = 50


for model in (m.Profile, m.CurriculumLecture, m.Batch, m.PaymentAccountInfo, m.PromoCode, m.Testimonial, m.ContactSubmission,
              m.NewsletterSubscriber, m.SiteStat, m.ClassRecording, m.CurriculumModule):
    if not admin.site.is_registered(model):
        admin.site.register(model)
