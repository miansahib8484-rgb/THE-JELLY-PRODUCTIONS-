"""Sidebar menus for the three dashboards.

Entry formats:
  ('heading', 'Label')
  ('namespace:url_name', 'material_icon', 'Label')                        a link
  ('namespace:url_name', 'material_icon', 'Label', ['ns:other', ...])      link that is also "active" on other URLs
"""
_OTHERS = [
    ('heading', 'Others'),
    ('accounts:notifications', 'notifications', 'Notifications'),
    ('accounts:profile', 'person', 'Profile'),
    ('accounts:settings', 'lock', 'Settings & password'),
]

MENUS = {
    'student': [
        ('heading', 'Main'),
        ('student:dashboard', 'dashboard', 'Dashboard'),
        ('heading', 'Learning'),
        ('student:courses', 'auto_stories', 'My courses', ['student:player']),
        ('student:live', 'videocam', 'Live classes', ['student:watch']),
        ('student:certificates', 'workspace_premium', 'Certificates'),
        ('student:reviews', 'star', 'My reviews'),
        ('student:billing', 'payments', 'Billing', ['student:pay']),
    ] + _OTHERS,
    'teacher': [
        ('heading', 'Main'),
        ('teacher:dashboard', 'dashboard', 'Dashboard'),
        ('heading', 'Teaching'),
        ('teacher:courses', 'auto_stories', 'My courses', ['teacher:course_create', 'teacher:course_edit', 'teacher:builder']),
        ('teacher:live', 'videocam', 'Live classes', ['teacher:live_create', 'teacher:live_edit', 'teacher:recording']),
        ('teacher:certificates', 'workspace_premium', 'Certificates'),
        ('teacher:students', 'groups', 'Students', ['teacher:student_detail']),
        ('teacher:announcements', 'campaign', 'Announcements'),
        ('teacher:reviews', 'star', 'Reviews'),
        ('teacher:performance', 'monitoring', 'Performance'),
    ] + _OTHERS,
    'admin': [
        ('heading', 'Main'),
        ('adminpanel:overview', 'dashboard', 'Overview'),
        ('adminpanel:reports', 'monitoring', 'Reports'),
        ('heading', 'Manage'),
        ('adminpanel:courses', 'auto_stories', 'Courses', ['adminpanel:course_create', 'adminpanel:course_edit', 'adminpanel:builder']),
        ('adminpanel:teachers', 'school', 'Teachers'),
        ('adminpanel:students', 'groups', 'Students'),
        ('adminpanel:enrollments', 'assignment', 'Enrollments'),
        ('adminpanel:live', 'videocam', 'Live classes', ['adminpanel:live_create', 'adminpanel:live_edit', 'adminpanel:recording']),
        ('adminpanel:certificates', 'workspace_premium', 'Certificates'),
        ('adminpanel:payments', 'account_balance_wallet', 'Payments'),
        ('adminpanel:reviews', 'star', 'Reviews'),
        ('heading', 'Inbox'),
        ('adminpanel:contact', 'inbox', 'Contact inbox'),
        ('adminpanel:newsletter', 'mail', 'Newsletter'),
        ('heading', 'Others'),
        ('accounts:notifications', 'notifications', 'Notifications'),
        ('adminpanel:content', 'web', 'Site content'),
        ('adminpanel:blog', 'article', 'Blog', ['adminpanel:blog_new', 'adminpanel:blog_edit']),
        ('adminpanel:settings', 'settings', 'Payments & promos'),
        ('accounts:profile', 'person', 'Profile'),
        ('accounts:settings', 'lock', 'Account & password'),
    ],
}
