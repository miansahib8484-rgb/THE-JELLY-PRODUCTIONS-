"""Find out why somebody cannot sign in.

    python manage.py check_login hafiz                       # by user name or e-mail
    python manage.py check_login hafiz@gmail.com --password "the-password"      # also tries the password
    python manage.py check_login hafiz --set-password "a-new-Strong-pass-1"     # and/or sets a new one

It tells you: is there such an account, is it active, which role it has, WHICH of the three sign-ins (Student / Teacher / Admin) it must use,
whether the password is right, and whether it is locked out for too many wrong attempts.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.views import ACCOUNT_FAILURE_LIMIT, IP_FAILURE_LIMIT, SIGN_IN_ROLES
from core.permissions import get_role
from core.security import digest
from core.throttle import limited


class Command(BaseCommand):
    help = 'Explain why a person cannot sign in (account, role, sign-in to use, password, lock-out).'

    def add_arguments(self, parser):
        parser.add_argument('identifier', help='the user name or the e-mail address the person types')
        parser.add_argument('--password', help='also check this password')
        parser.add_argument('--set-password', dest='new_password', help='set this as the new password (it must be strong enough)')

    def handle(self, *args, **opts):
        User = get_user_model()
        ident = opts['identifier'].strip()
        by_name, by_mail = User.objects.filter(username__iexact=ident).first(), User.objects.filter(email__iexact=ident).first()
        user = by_name or by_mail
        if user is None:
            raise CommandError(f'No account has the user name or e-mail "{ident}". Check the spelling, or create it '
                               '(python manage.py createsuperuser, or Admin > Students / Teachers > Add).')
        role = get_role(user)
        info = SIGN_IN_ROLES[role]
        out = self.stdout.write
        out(self.style.MIGRATE_HEADING(f'Account found: {user.username}'))
        out(f'  user name            {user.username}')
        out(f'  e-mail               {user.email or "(none)"}      <- both the user name and the e-mail work when typing the name')
        out(f'  full name            {user.get_full_name() or "(none)"}')
        out(f'  active               {"yes" if user.is_active else "NO: deactivated accounts cannot sign in (Admin > Students/Teachers > Activate)"}')
        out(f'  role                 {role}' + ('   (a Django superuser is always an administrator)' if user.is_superuser else ''))
        out(self.style.SUCCESS(f'  USE THE SIGN-IN      {info["label"].upper()}   -> /account/login/?as={role}'))
        out(f'  must change password {"yes" if user.profile.must_change_password else "no"}')
        if by_name and by_mail and by_name.pk != by_mail.pk:
            out(self.style.WARNING(f'  WARNING: "{ident}" is the user name of one account and the e-mail of another ({by_mail.username}).'))
        locked = limited('login-account', digest(ident), ACCOUNT_FAILURE_LIMIT)
        out(f'  locked out           {"YES: wait 10 minutes (or restart the server)" if locked else "no"}'
            + ('' if locked else '   (the lock-out counters live in the server\'s memory: with the default in-memory cache this check only sees them inside the running server)'))
        out(f'  (per address, {IP_FAILURE_LIMIT} wrong tries lock that address for 10 minutes)')
        if opts['password'] is not None:
            ok = user.check_password(opts['password'])
            out((self.style.SUCCESS if ok else self.style.ERROR)(f'  password             {"RIGHT" if ok else "WRONG"}'))
        if opts['new_password']:
            try:
                validate_password(opts['new_password'], user)
            except ValidationError as e:
                raise CommandError('That password is not strong enough: ' + ' '.join(e.messages))
            user.set_password(opts['new_password'])
            user.save(update_fields=['password'])
            out(self.style.SUCCESS('  password             CHANGED. Sign in at the ' + info['label'] + ' sign-in.'))
